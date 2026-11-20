"use client";
import { create } from "zustand";
import type { Edge, EdgeChangeset, Node, NodeChangeset } from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import { toast } from "sonner";
import { api, postSSE } from "@/lib/api";
import type { Anchor, CanvasEdgeData, CanvasNodeData, Doc, NodeType, Reward, Source } from "@/lib/types";
import { useProfile } from "./profile";

export type FlowNode = Node<CanvasNodeData, NodeType>;

const NODE_W: Record<NodeType, number> = { excerpt: 320, qa: 420, note: 320, concept: 300 };

const toFlowNode = (n: CanvasNodeData): FlowNode => ({
  id: String(n.id),
  type: n.type,
  position: { x: n.x, y: n.y },
  data: n,
  style: { width: NODE_W[n.type] },
});

const toFlowEdge = (e: CanvasEdgeData): Edge => ({
  id: `e${e.id}`,
  source: String(e.source),
  target: String(e.target),
  data: { dbId: e.id },
  animated: false,
});

export interface CardDraftRequest {
  text: string;
  context?: string;
  source: string;
  source_ref?: string;
}

interface CreateNodeInput {
  type: NodeType;
  kind?: string;
  title?: string;
  question?: string;
  content?: string;
  anchor?: Anchor | null;
  parent_ids?: number[];
  x?: number;
  y?: number;
}

interface WorkspaceState {
  courseId: number | null;
  docs: Doc[];
  activeDocId: number | null;
  jump: { anchor: Anchor; nonce: number } | null;
  nodes: FlowNode[];
  edges: Edge[];
  streaming: Record<string, boolean>;
  focus: { id: string; nonce: number } | null;
  cardDraft: CardDraftRequest | null;
  readerVisible: boolean;
  canvasVisible: boolean;

  load: (courseId: number) => Promise<void>;
  refreshDocs: () => Promise<void>;
  openDoc: (docId: number) => void;
  jumpTo: (anchor: Anchor) => void;
  setPanels: (p: { readerVisible?: boolean; canvasVisible?: boolean }) => void;

  onNodesChange: (c: NodeChangeset<FlowNode>) => void;
  onEdgesChange: (c: EdgeChangeset<Edge>) => void;
  createNode: (input: CreateNodeInput) => Promise<CanvasNodeData | null>;
  ask: (input: { question: string; kind: string; parentIds: number[]; type?: NodeType; title?: string }) => Promise<void>;
  askFromExcerpt: (anchor: Anchor, question: string) => Promise<void>;
  streamAnswer: (nodeId: number) => Promise<void>;
  patchNode: (nodeId: number, patch: Partial<CanvasNodeData>, persist?: boolean) => void;
  deleteNodes: (ids: number[]) => Promise<void>;
  deleteEdges: (dbIds: number[]) => Promise<void>;
  connect: (source: number, target: number) => Promise<void>;
  savePositions: (nodes: FlowNode[]) => void;
  autoLayout: () => void;
  focusNode: (id: number) => void;
  openCardDraft: (r: CardDraftRequest | null) => void;
}

const nodeById = (s: WorkspaceState, id: number) => s.nodes.find((n) => n.id === String(id));

export const useWorkspace = create<WorkspaceState>((set, get) => ({
  courseId: null,
  docs: [],
  activeDocId: null,
  jump: null,
  nodes: [],
  edges: [],
  streaming: {},
  focus: null,
  cardDraft: null,
  readerVisible: true,
  canvasVisible: true,

  load: async (courseId) => {
    set({ courseId, nodes: [], edges: [], docs: [], activeDocId: null, jump: null });
    const [docs, canvas] = await Promise.all([
      api.get<Doc[]>(`/api/courses/${courseId}/documents`),
      api.get<{ nodes: CanvasNodeData[]; edges: CanvasEdgeData[] }>(`/api/courses/${courseId}/canvas`),
    ]);
    if (get().courseId !== courseId) return;
    let last: number | null = null;
    try {
      last = Number(localStorage.getItem(`alp-doc-${courseId}`)) || null;
    } catch {
      /* ignore */
    }
    const active = docs.find((d) => d.id === last)?.id ?? docs[0]?.id ?? null;
    set({ docs, activeDocId: active, nodes: canvas.nodes.map(toFlowNode), edges: canvas.edges.map(toFlowEdge) });
  },

  refreshDocs: async () => {
    const cid = get().courseId;
    if (!cid) return;
    const docs = await api.get<Doc[]>(`/api/courses/${cid}/documents`);
    set((s) => ({ docs, activeDocId: s.activeDocId ?? docs[0]?.id ?? null }));
  },

  openDoc: (docId) => {
    set({ activeDocId: docId, readerVisible: true });
    try {
      localStorage.setItem(`alp-doc-${get().courseId}`, String(docId));
    } catch {
      /* ignore */
    }
  },

  jumpTo: (anchor) => {
    get().openDoc(anchor.document_id);
    set({ jump: { anchor, nonce: Date.now() } });
  },

  setPanels: (p) =>
    set((s) => {
      const next = { readerVisible: p.readerVisible ?? s.readerVisible, canvasVisible: p.canvasVisible ?? s.canvasVisible };
      if (!next.readerVisible && !next.canvasVisible) next.canvasVisible = true;
      return next;
    }),

  // @xyflow/react v13：变更以 Changeset 形式传入，用 applyTo 应用
  onNodesChange: (changes) => set((s) => ({ nodes: changes.applyTo(s.nodes) })),
  onEdgesChange: (changes) => set((s) => ({ edges: changes.applyTo(s.edges) })),

  createNode: async (input) => {
    const s = get();
    if (!s.courseId) return null;
    const parents = (input.parent_ids ?? []).map((id) => nodeById(s, id)).filter(Boolean) as FlowNode[];
    let { x, y } = input;
    if (x === undefined || y === undefined) {
      if (parents.length) {
        const maxX = Math.max(...parents.map((p) => p.position.x + (p.measured?.width ?? NODE_W[p.type as NodeType])));
        const avgY = parents.reduce((a, p) => a + p.position.y, 0) / parents.length;
        const siblings = s.edges.filter((e) => e.source === parents[0].id).length;
        x = maxX + 80;
        y = avgY + siblings * 60;
      } else {
        const col = s.nodes.filter((n) => n.type === input.type && n.position.x < 200);
        x = 0;
        y = col.length ? Math.max(...col.map((n) => n.position.y + (n.measured?.height ?? 180))) + 60 : 0;
      }
      // 简单避让：与已有节点重叠就往下挪，直到找到空位
      const w = NODE_W[input.type];
      const h = 220;
      const overlaps = (yy: number) =>
        s.nodes.find((n) => {
          const nw = n.measured?.width ?? NODE_W[n.type as NodeType];
          const nh = n.measured?.height ?? 200;
          return x! < n.position.x + nw + 20 && x! + w + 20 > n.position.x && yy < n.position.y + nh + 20 && yy + h + 20 > n.position.y;
        });
      for (let i = 0, hit = overlaps(y); hit && i < 50; i++, hit = overlaps(y)) {
        y = hit.position.y + (hit.measured?.height ?? 200) + 30;
      }
    }
    try {
      const res = await api.post<{ node: CanvasNodeData; edges: CanvasEdgeData[]; reward: Reward | null }>(
        `/api/courses/${s.courseId}/nodes`,
        { ...input, x, y, parent_ids: input.parent_ids ?? [] },
      );
      set((st) => ({
        nodes: [...st.nodes.map((n) => ({ ...n, selected: false })), { ...toFlowNode(res.node), selected: true }],
        edges: [...st.edges, ...res.edges.map(toFlowEdge)],
      }));
      useProfile.getState().applyReward(res.reward);
      get().focusNode(res.node.id);
      return res.node;
    } catch (e) {
      toast.error(`创建节点失败：${(e as Error).message}`);
      return null;
    }
  },

  ask: async ({ question, kind, parentIds, type = "qa", title = "" }) => {
    const node = await get().createNode({ type, kind, question, title, parent_ids: parentIds });
    if (node) await get().streamAnswer(node.id);
  },

  askFromExcerpt: async (anchor, question) => {
    const ex = await get().createNode({ type: "excerpt", content: anchor.quote, anchor });
    if (!ex) return;
    await get().ask({ question, kind: "highlight", parentIds: [ex.id] });
  },

  streamAnswer: async (nodeId) => {
    const key = String(nodeId);
    if (get().streaming[key]) return;
    set((s) => ({ streaming: { ...s.streaming, [key]: true } }));
    get().patchNode(nodeId, { content: "", sources: [] }, false);
    let text = "";
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      flushTimer = null;
      get().patchNode(nodeId, { content: text }, false);
    };
    try {
      await postSSE(`/api/nodes/${nodeId}/answer`, {
        onEvent: (e) => {
          if (e.type === "sources") get().patchNode(nodeId, { sources: e.sources as Source[] }, false);
          else if (e.type === "delta") {
            text += e.text as string;
            flushTimer ??= setTimeout(flush, 50); // 合并高频增量，减少重渲染
          } else if (e.type === "error") toast.error(e.message as string);
          else if (e.type === "done") useProfile.getState().applyReward(e.reward as Reward | null);
        },
      });
    } catch (e) {
      toast.error(`回答失败：${(e as Error).message}`);
    } finally {
      if (flushTimer) clearTimeout(flushTimer);
      flush();
      set((s) => {
        const next = { ...s.streaming };
        delete next[key];
        return { streaming: next };
      });
    }
  },

  patchNode: (nodeId, patch, persist = true) => {
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === String(nodeId) ? { ...n, data: { ...n.data, ...patch } } : n)),
    }));
    if (persist) {
      const body: Record<string, unknown> = {};
      for (const k of ["title", "question", "content"] as const) if (k in patch) body[k] = patch[k];
      if (Object.keys(body).length) api.patch(`/api/nodes/${nodeId}`, body).catch((e) => toast.error(e.message));
    }
  },

  deleteNodes: async (ids) => {
    const keys = new Set(ids.map(String));
    set((s) => ({
      nodes: s.nodes.filter((n) => !keys.has(n.id)),
      edges: s.edges.filter((e) => !keys.has(e.source) && !keys.has(e.target)),
    }));
    await Promise.all(ids.map((id) => api.del(`/api/nodes/${id}`).catch(() => undefined)));
  },

  deleteEdges: async (dbIds) => {
    const keys = new Set(dbIds.map((i) => `e${i}`));
    set((s) => ({ edges: s.edges.filter((e) => !keys.has(e.id)) }));
    await Promise.all(dbIds.map((id) => api.del(`/api/edges/${id}`).catch(() => undefined)));
  },

  connect: async (source, target) => {
    const cid = get().courseId;
    try {
      const e = await api.post<CanvasEdgeData>(`/api/courses/${cid}/edges`, { source, target });
      set((s) => (s.edges.some((x) => x.id === `e${e.id}`) ? s : { edges: [...s.edges, toFlowEdge(e)] }));
    } catch (err) {
      toast.error((err as Error).message);
    }
  },

  savePositions: (nodes) => {
    if (!nodes.length) return;
    api
      .post("/api/nodes/positions", nodes.map((n) => ({ id: Number(n.id), x: n.position.x, y: n.position.y })))
      .catch(() => undefined);
  },

  autoLayout: () => {
    const { nodes, edges } = get();
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 90 });
    g.setDefaultEdgeLabel(() => ({}));
    nodes.forEach((n) =>
      g.setNode(n.id, { width: n.measured?.width ?? NODE_W[n.type as NodeType], height: n.measured?.height ?? 200 }),
    );
    edges.forEach((e) => g.setEdge(e.source, e.target));
    dagre.layout(g);
    const next = nodes.map((n) => {
      const p = g.node(n.id);
      const w = n.measured?.width ?? NODE_W[n.type as NodeType];
      const h = n.measured?.height ?? 200;
      return { ...n, position: { x: p.x - w / 2, y: p.y - h / 2 } };
    });
    set({ nodes: next });
    get().savePositions(next);
  },

  focusNode: (id) => set({ focus: { id: String(id), nonce: Date.now() } }),
  openCardDraft: (r) => set({ cardDraft: r }),
}));

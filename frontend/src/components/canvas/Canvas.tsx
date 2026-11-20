"use client";
import { useEffect, useMemo, useState } from "react";
import { Background, Controls, MiniMap, Panel, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import { LayoutGrid, Merge, MessageCirclePlus, Send, StickyNote } from "lucide-react";
import { useWorkspace, type FlowNode } from "@/stores/workspace";
import { nodeTypes } from "./nodes";

const MINIMAP_COLOR: Record<string, string> = {
  excerpt: "var(--color-amber-300)",
  qa: "var(--color-indigo-300)",
  note: "var(--color-emerald-300)",
  concept: "var(--color-violet-300)",
};

function FocusController() {
  const focus = useWorkspace((s) => s.focus);
  const { setCenter, getNode, getZoom } = useReactFlow();
  useEffect(() => {
    if (!focus) return;
    // 等节点挂载、测量完成后再居中
    const t = setTimeout(() => {
      const n = getNode(focus.id);
      if (!n) return;
      const w = n.measured?.width ?? 380;
      const h = n.measured?.height ?? 200;
      void setCenter(n.position.x + w / 2, n.position.y + Math.min(h, 400) / 2, { zoom: Math.max(getZoom(), 0.8), duration: 450 });
    }, 80);
    return () => clearTimeout(t);
  }, [focus, setCenter, getNode, getZoom]);
  return null;
}

function MergeBar({ selected }: { selected: FlowNode[] }) {
  const [q, setQ] = useState("");
  const ask = useWorkspace((s) => s.ask);
  if (selected.length < 2) return null;
  return (
    <Panel position="top-center">
      <form
        className="flex w-[460px] items-center gap-2 rounded-xl border border-indigo-200 bg-surface p-1.5 pl-3 shadow-lg"
        onSubmit={(e) => {
          e.preventDefault();
          if (!q.trim()) return;
          void ask({ question: q.trim(), kind: "merge", parentIds: selected.map((n) => Number(n.id)) });
          setQ("");
        }}
      >
        <Merge size={16} className="shrink-0 text-indigo-500" />
        <span className="shrink-0 text-xs text-slate-500">已选 {selected.length} 个节点</span>
        <input
          className="min-w-0 flex-1 text-sm outline-none"
          placeholder="把它们汇总到一个新问题，如：对比两者异同"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="rounded-lg bg-indigo-600 p-2 text-white disabled:opacity-40" disabled={!q.trim()}>
          <Send size={14} />
        </button>
      </form>
    </Panel>
  );
}

function RootAsk({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const ask = useWorkspace((s) => s.ask);
  return (
    <form
      className="flex w-[360px] items-center gap-1 rounded-xl border border-slate-200 bg-surface p-1.5 shadow-lg"
      onSubmit={(e) => {
        e.preventDefault();
        if (!q.trim()) return;
        void ask({ question: q.trim(), kind: "followup", parentIds: [] });
        onClose();
      }}
    >
      <input
        autoFocus
        className="min-w-0 flex-1 px-2 text-sm outline-none"
        placeholder="向整门课的资料提一个新问题…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      />
      <button className="rounded-lg bg-indigo-600 p-2 text-white disabled:opacity-40" disabled={!q.trim()}>
        <Send size={14} />
      </button>
    </form>
  );
}

function CanvasInner() {
  const { nodes, edges, onNodesChange, onEdgesChange, connect, deleteNodes, deleteEdges, savePositions, autoLayout, createNode } = useWorkspace();
  const [rootAsk, setRootAsk] = useState(false);
  const selected = useMemo(() => nodes.filter((n) => n.selected), [nodes]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={(c) => c.source && c.target && void connect(Number(c.source), Number(c.target))}
      onNodesDelete={(ns) => void deleteNodes(ns.map((n) => Number(n.id)))}
      onEdgesDelete={(es) => void deleteEdges(es.map((e) => (e.data as { dbId: number }).dbId))}
      onNodeDragStop={(_, __, dragged) => savePositions(dragged as FlowNode[])}
      deleteKeyCode="Delete"
      minZoom={0.15}
      maxZoom={2}
      fitView
      fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
      defaultEdgeOptions={{ type: "default" }}
    >
      <Background gap={22} size={1.2} />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable nodeColor={(n) => MINIMAP_COLOR[n.type ?? ""] ?? "var(--color-slate-300)"} className="!rounded-xl !border !border-slate-200" />
      <FocusController />
      <MergeBar selected={selected} />
      <Panel position="top-left">
        <div className="flex flex-col items-start gap-2">
          <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-surface/95 p-1 shadow-sm">
            <ToolbarBtn icon={<MessageCirclePlus size={15} />} label="新问题" onClick={() => setRootAsk((v) => !v)} />
            <ToolbarBtn
              icon={<StickyNote size={15} />}
              label="新笔记"
              onClick={() => void createNode({ type: "note", title: "", content: "", parent_ids: selected.map((n) => Number(n.id)).slice(0, 5) })}
            />
            <ToolbarBtn icon={<LayoutGrid size={15} />} label="自动排布" onClick={autoLayout} />
          </div>
          {rootAsk && <RootAsk onClose={() => setRootAsk(false)} />}
        </div>
      </Panel>
      {nodes.length === 0 && (
        <Panel position="top-center" className="!top-1/3">
          <div className="max-w-sm rounded-2xl border border-dashed border-slate-300 bg-surface/90 p-6 text-center text-sm leading-6 text-slate-500">
            <div className="mb-2 text-2xl">🧭</div>
            在左侧原文里<b>拖选一段文字</b>，点「分叉提问」，这里就会长出第一张原文卡和 AI 问答卡。
            <br />
            <span className="text-xs text-slate-400">Ctrl/Shift 多选节点可汇总提问 · 拖拽连线控制上下文 · Delete 删除</span>
          </div>
        </Panel>
      )}
    </ReactFlow>
  );
}

function ToolbarBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-slate-600 hover:bg-indigo-50 hover:text-indigo-700" onClick={onClick}>
      {icon}
      {label}
    </button>
  );
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}

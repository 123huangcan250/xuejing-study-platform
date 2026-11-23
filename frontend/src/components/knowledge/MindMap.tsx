"use client";
import { useMemo } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import clsx from "clsx";
import { ChevronRight, Flame } from "lucide-react";
import type { KNode } from "@/lib/types";

const COLORS = ["indigo", "emerald", "amber", "rose", "sky", "violet", "orange"];
const W: Record<number, number> = { 0: 190, 1: 230, 2: 220, 3: 250 };
const H: Record<number, number> = { 0: 46, 1: 50, 2: 46, 3: 48 };

type MMData = {
  title: string;
  level: 0 | 1 | 2 | 3;
  color: string;
  examTotal: number;
  hasKids: boolean;
  collapsed: boolean;
  importance: number;
  selected: boolean;
  onToggle: () => void;
  [key: string]: unknown;
};
type MMNode = Node<MMData, "mm">;

const CENTER_HANDLE: React.CSSProperties = { left: "50%", top: "50%", transform: "translate(-50%, -50%)", opacity: 0, pointerEvents: "none" };

function MMNodeView({ data }: NodeProps<MMNode>) {
  const c = `var(--color-${data.color}-500)`;
  const style: React.CSSProperties =
    data.level === 1
      ? { background: `var(--color-${data.color}-600)`, color: "white" } // 章节用更深的 600 档，白字更清楚
      : data.level === 2
        ? { borderColor: c, background: `color-mix(in oklab, ${c} 13%, var(--surface))` }
        : { borderLeftColor: c };
  return (
    <div
      className={clsx(
        "flex items-center gap-1.5 text-left shadow-sm transition",
        data.level === 0 && "rounded-2xl bg-ink px-4 py-2.5 text-[14px] font-bold text-ink-fg",
        data.level === 1 && "rounded-xl px-3.5 py-2 text-[13.5px] font-semibold",
        data.level === 2 && "rounded-lg border-[1.5px] px-3 py-1.5 text-[13px] font-medium text-slate-800",
        data.level === 3 && "rounded-lg border border-l-[3px] border-slate-200 bg-surface px-2.5 py-1.5 text-[12.5px] text-slate-700",
        data.level === 3 && data.importance >= 3 && "font-semibold text-slate-900",
        data.selected && "ring-2 ring-offset-1 ring-offset-surface",
      )}
      style={{ ...style, width: W[data.level], minHeight: H[data.level], ["--tw-ring-color" as string]: c }}
    >
      <Handle type="target" position={Position.Top} style={CENTER_HANDLE} />
      <span className="line-clamp-2 min-w-0 flex-1 leading-snug">{data.title}</span>
      {data.examTotal > 0 && (
        <span className="flex shrink-0 items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800" title={`在试卷中出现 ${data.examTotal} 次`}>
          <Flame size={10} />
          {data.examTotal}
        </span>
      )}
      {data.hasKids && (
        <button
          className="nodrag grid h-5 w-5 shrink-0 place-items-center rounded-full bg-black/10 hover:bg-black/20"
          style={data.level === 1 ? { color: "white" } : undefined}
          title={data.collapsed ? "展开" : "折叠"}
          onClick={(e) => {
            e.stopPropagation();
            data.onToggle();
          }}
        >
          <ChevronRight size={13} className={clsx("transition", !data.collapsed && "rotate-90")} />
        </button>
      )}
      <Handle type="source" position={Position.Bottom} style={CENTER_HANDLE} />
    </div>
  );
}

const nodeTypes = { mm: MMNodeView };

interface Props {
  tree: KNode[];
  courseName: string;
  collapsed: Set<number>;
  onToggle: (id: number) => void;
  selectedId: number | null;
  onSelect: (n: KNode | null) => void;
  examOnly: boolean;
}

interface L {
  id: string;
  level: 0 | 1 | 2 | 3;
  title: string;
  color: string;
  examTotal: number;
  importance: number;
  hasKids: boolean;
  collapsed: boolean;
  kids: L[];
  leaves: number;
  weight: number;
  ang: number;
  depth: number;
  kn: KNode | null;
}

const XS = 1.5; // 椭圆：横向拉宽（节点是横向的长方形，屏幕也是宽的）
const CHORD = [0, 170, 160, 150]; // 同一圈上相邻两个节点中心之间至少要隔开的“弦长”
const RING_GAP = [0, 200, 190, 190]; // 相邻两圈半径至少相差多少

/** 放射布局：中心 → 一圈圈向外。每个分支占的角度正比于它包含的知识点数，每圈半径由最拥挤的位置决定。 */
function radial(center: L) {
  const all: L[] = [];
  const place = (n: L, a0: number, a1: number, depth: number) => {
    n.ang = (a0 + a1) / 2;
    n.depth = depth;
    all.push(n);
    const total = n.kids.reduce((x, k) => x + k.weight, 0) || 1;
    let a = a0;
    for (const k of n.kids) {
      const span = ((a1 - a0) * k.weight) / total;
      place(k, a, a + span, depth + 1);
      a += span;
    }
  };
  place(center, -Math.PI / 2, (3 * Math.PI) / 2, 0); // 从正上方开始，顺时针

  const maxDepth = Math.max(...all.map((n) => n.depth));
  const radius = [0];
  for (let d = 1; d <= maxDepth; d++) {
    const angs = all.filter((n) => n.depth === d).map((n) => n.ang).sort((a, b) => a - b);
    let minGap = 2 * Math.PI;
    for (let i = 0; i < angs.length; i++) {
      const next = i + 1 < angs.length ? angs[i + 1] : angs[0] + 2 * Math.PI;
      if (angs.length > 1) minGap = Math.min(minGap, Math.max(next - angs[i], 1e-3));
    }
    const k = Math.min(d, 3);
    radius[d] = Math.max(radius[d - 1] + RING_GAP[k], Math.min(CHORD[k] / minGap, 6000));
  }
  return all.map((n) => ({ n, x: XS * radius[n.depth] * Math.cos(n.ang), y: radius[n.depth] * Math.sin(n.ang) }));
}

export function MindMap({ tree, courseName, collapsed, onToggle, selectedId, onSelect, examOnly }: Props) {
  const { nodes, edges, byId, key } = useMemo(() => {
    const byId = new Map<number, KNode>();
    const build = (n: KNode, color: string): L | null => {
      if (examOnly && n.exam_total === 0) return null;
      byId.set(n.id, n);
      const all = examOnly ? n.children.filter((k) => k.exam_total > 0) : n.children;
      const isCollapsed = collapsed.has(n.id);
      const kids = isCollapsed ? [] : (all.map((k) => build(k, color)).filter(Boolean) as L[]);
      const leaves = kids.length ? kids.reduce((x, k) => x + k.leaves, 0) : 1;
      return { id: String(n.id), level: n.level, title: n.title, color, examTotal: n.exam_total, importance: n.importance, hasKids: all.length > 0, collapsed: isCollapsed, kids, leaves, weight: Math.pow(leaves, 0.75), ang: 0, depth: 0, kn: n };
    };

    // 只有一份课件（例如按课件筛选后）：以这份课件为中心；否则以课程为中心、各课件围成第一圈
    let center: L | null;
    if (tree.length === 1) center = build(tree[0], COLORS[0]);
    else {
      const kids = tree.map((c, i) => build(c, COLORS[i % COLORS.length])).filter(Boolean) as L[];
      const leaves = kids.reduce((x, k) => x + k.leaves, 0) || 1;
      center = { id: "0", level: 0, title: courseName, color: "indigo", examTotal: 0, importance: 2, hasKids: false, collapsed: false, kids, leaves, weight: 1, ang: 0, depth: 0, kn: null };
    }
    if (!center) return { nodes: [] as MMNode[], edges: [] as Edge[], byId, key: "empty" };

    const placed = radial(center);
    const ns: MMNode[] = placed.map(({ n, x, y }) => ({
      id: n.id,
      type: "mm",
      position: { x: x - W[n.level] / 2, y: y - H[n.level] / 2 },
      data: { title: n.title, level: n.level, color: n.color, examTotal: n.examTotal, hasKids: n.hasKids, collapsed: n.collapsed, importance: n.importance, selected: n.kn?.id === selectedId, onToggle: () => n.kn && onToggle(n.kn.id) },
    }));
    const es: Edge[] = [];
    for (const { n } of placed)
      for (const k of n.kids)
        es.push({ id: `e${n.id}-${k.id}`, type: "straight", source: n.id, target: k.id, style: { stroke: `var(--color-${k.color}-400)`, strokeWidth: k.level === 1 ? 2.4 : 1.6 } });
    // 布局只在结构变化时重算，选中状态变化不需要重新适配视图
    return { nodes: ns, edges: es, byId, key: ns.map((x) => x.id).join(",") };
  }, [tree, courseName, collapsed, examOnly, selectedId, onToggle]);

  return (
    <ReactFlow
      key={key.length > 4000 ? key.length : key}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      onNodeClick={(_, n) => onSelect(byId.get(Number(n.id)) ?? null)}
      onPaneClick={() => onSelect(null)}
      fitView
      fitViewOptions={{ maxZoom: 1, padding: 0.12 }}
      minZoom={0.05}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1.2} />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable className="!rounded-xl !border !border-slate-200" nodeColor={(n) => `var(--color-${(n.data as MMData).color}-300)`} />
    </ReactFlow>
  );
}

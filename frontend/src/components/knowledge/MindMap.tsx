"use client";
import { useMemo } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import dagre from "@dagrejs/dagre";
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

function MMNodeView({ data }: NodeProps<MMNode>) {
  const c = `var(--color-${data.color}-500)`;
  const style: React.CSSProperties =
    data.level === 1
      ? { background: c, color: "white" }
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
      <Handle type="target" position={Position.Left} className="!pointer-events-none !opacity-0" />
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
      <Handle type="source" position={Position.Right} className="!pointer-events-none !opacity-0" />
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

export function MindMap({ tree, courseName, collapsed, onToggle, selectedId, onSelect, examOnly }: Props) {
  const { nodes, edges, byId, key } = useMemo(() => {
    const ns: MMNode[] = [];
    const es: Edge[] = [];
    const byId = new Map<number, KNode>();
    ns.push({ id: "0", type: "mm", position: { x: 0, y: 0 }, data: { title: courseName, level: 0, color: "indigo", examTotal: 0, hasKids: false, collapsed: false, importance: 2, selected: false, onToggle: () => undefined } });
    const walk = (n: KNode, parent: string, color: string) => {
      if (examOnly && n.exam_total === 0) return;
      byId.set(n.id, n);
      const kids = examOnly ? n.children.filter((k) => k.exam_total > 0) : n.children;
      const isCollapsed = collapsed.has(n.id);
      ns.push({
        id: String(n.id),
        type: "mm",
        position: { x: 0, y: 0 },
        data: { title: n.title, level: n.level, color, examTotal: n.exam_total, hasKids: kids.length > 0, collapsed: isCollapsed, importance: n.importance, selected: n.id === selectedId, onToggle: () => onToggle(n.id) },
      });
      es.push({ id: `e${parent}-${n.id}`, source: parent, target: String(n.id), style: { stroke: `var(--color-${color}-400)`, strokeWidth: n.level === 1 ? 2.2 : 1.6 } });
      if (!isCollapsed) kids.forEach((k) => walk(k, String(n.id), color));
    };
    tree.forEach((ch, i) => walk(ch, "0", COLORS[i % COLORS.length]));

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 12, ranksep: 56 });
    g.setDefaultEdgeLabel(() => ({}));
    // dagre 会把同级节点的先后顺序翻转，倒序喂给它，最终从上到下才是讲课顺序
    [...ns].reverse().forEach((n) => g.setNode(n.id, { width: W[n.data.level], height: H[n.data.level] + 8 }));
    [...es].reverse().forEach((e) => g.setEdge(e.source, e.target));
    dagre.layout(g);
    ns.forEach((n) => {
      const p = g.node(n.id);
      n.position = { x: p.x - W[n.data.level] / 2, y: p.y - (H[n.data.level] + 8) / 2 };
    });
    // 布局只在结构变化时重算，选中状态变化不需要重新适配视图
    return { nodes: ns, edges: es, byId, key: ns.map((n) => n.id).join(",") };
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
      fitViewOptions={{ maxZoom: 1, padding: 0.15 }}
      minZoom={0.1}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1.2} />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable className="!rounded-xl !border !border-slate-200" nodeColor={(n) => `var(--color-${(n.data as MMData).color}-300)`} />
    </ReactFlow>
  );
}

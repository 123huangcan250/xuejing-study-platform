"use client";
import { useMemo } from "react";
import clsx from "clsx";
import { ChevronRight, Flame } from "lucide-react";
import type { KNode } from "@/lib/types";

interface Props {
  tree: KNode[];
  collapsed: Set<number>;
  onToggle: (id: number) => void;
  selectedId: number | null;
  onSelect: (n: KNode) => void;
  examOnly: boolean;
  query: string;
}

const match = (n: KNode, q: string): boolean =>
  !q || n.title.toLowerCase().includes(q) || n.summary.toLowerCase().includes(q) || n.points.some((p) => p.toLowerCase().includes(q)) || n.children.some((c) => match(c, q));

/** 知识梳理的大纲视图：课件 → 小节 → 知识点，可折叠、可搜索。 */
export function Outline({ tree, collapsed, onToggle, selectedId, onSelect, examOnly, query }: Props) {
  const q = query.trim().toLowerCase();
  const visible = useMemo(() => tree.filter((c) => (!examOnly || c.exam_total > 0) && match(c, q)), [tree, examOnly, q]);

  const row = (n: KNode): React.ReactNode => {
    if (examOnly && n.exam_total === 0) return null;
    if (!match(n, q)) return null;
    const open = q ? true : !collapsed.has(n.id);
    const kids = n.children;
    return (
      <div key={n.id}>
        <div
          className={clsx(
            "group flex cursor-pointer items-start gap-1.5 rounded-lg px-2 py-1.5 transition hover:bg-slate-100",
            selectedId === n.id && "bg-indigo-50 ring-1 ring-indigo-200",
            n.level === 1 && "mt-3",
          )}
          style={{ marginLeft: (n.level - 1) * 18 }}
          onClick={() => onSelect(n)}
        >
          {kids.length > 0 ? (
            <button
              className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded text-slate-400 hover:bg-slate-200"
              onClick={(e) => {
                e.stopPropagation();
                onToggle(n.id);
              }}
            >
              <ChevronRight size={14} className={clsx("transition", open && "rotate-90")} />
            </button>
          ) : (
            <span className="mt-2 ml-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={clsx(n.level === 1 && "text-[15px] font-bold text-slate-900", n.level === 2 && "text-[13.5px] font-semibold text-slate-800", n.level === 3 && "text-[13px] text-slate-700", n.importance >= 3 && n.level === 3 && "font-semibold")}>
                {n.title}
              </span>
              {n.level === 3 && n.importance >= 3 && <span className="text-[10px] text-amber-500">★★★</span>}
              {n.exam_total > 0 && (
                <span className="flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800">
                  <Flame size={10} />
                  考点 ×{n.exam_total}
                </span>
              )}
              {n.level === 3 && n.refs.length > 0 && <span className="text-[10.5px] text-slate-400">第 {n.refs.map((r) => r.page).join("、")} 页</span>}
            </div>
            {n.summary && n.level !== 1 && <div className="mt-0.5 text-xs leading-5 text-slate-500">{n.summary}</div>}
          </div>
        </div>
        {open && kids.map(row)}
      </div>
    );
  };

  if (!visible.length) return <div className="p-10 text-center text-sm text-slate-400">没有符合条件的内容</div>;
  return <div className="px-3 pb-10">{visible.map(row)}</div>;
}

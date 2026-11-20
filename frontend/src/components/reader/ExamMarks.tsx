"use client";
import { useState } from "react";
import { Pin } from "lucide-react";
import clsx from "clsx";
import type { ExamMark } from "@/lib/types";

/** 课件某一页上的「考点」提示：这页的知识点在试卷里考过。 */
export function ExamMarks({ marks, width }: { marks: ExamMark[]; width?: number }) {
  const [open, setOpen] = useState(false);
  if (!marks.length) return null;
  return (
    <div data-trans className="mx-auto mb-2" style={width ? { width } : undefined}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          "flex w-full items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-left text-xs transition",
          "border-amber-300/70 bg-amber-50 text-amber-800 hover:bg-amber-100",
        )}
      >
        <Pin size={13} className="shrink-0" />
        <span className="font-semibold">考点</span>
        <span className="min-w-0 flex-1 truncate">
          {marks[0].exam} {marks[0].number}
          {marks.length > 1 ? ` 等 ${marks.length} 道题` : ""} · {marks[0].node}
        </span>
        <span className="shrink-0 text-amber-600">{open ? "收起" : "展开"}</span>
      </button>
      {open && (
        <div className="mt-1 space-y-1.5 rounded-lg border border-amber-200 bg-surface p-2.5">
          {marks.map((m) => (
            <div key={m.question_id} className="text-xs leading-5 text-slate-600">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-slate-800">
                  {m.exam} · {m.number}
                </span>
                {m.qtype && <span className="rounded bg-slate-100 px-1.5 text-[10.5px]">{m.qtype}</span>}
                {m.points != null && <span className="rounded bg-slate-100 px-1.5 text-[10.5px]">{m.points} 分</span>}
                {m.ability && <span className="rounded bg-indigo-50 px-1.5 text-[10.5px] text-indigo-700">{m.ability}</span>}
              </div>
              <div className="mt-0.5 text-slate-500">{m.excerpt}…</div>
              <div className="mt-0.5 text-[11px] text-amber-700">对应知识点：{m.node}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

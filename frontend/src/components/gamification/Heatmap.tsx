"use client";
import { useEffect, useMemo, useRef } from "react";
import type { HeatDay } from "@/lib/types";

// 5 档颜色由主题定义（globals.css 里的 --heat-0..4），换肤时自动跟着变
const HEAT = [0, 1, 2, 3, 4].map((i) => `var(--heat-${i})`);

function level(xp: number, goal: number) {
  if (xp <= 0) return 0;
  if (xp < goal) return 1;
  if (xp < goal * 2.5) return 2;
  if (xp < goal * 5) return 3;
  return 4;
}

const fmt = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** GitHub 风格学习热力图：每列一周，颜色按当日 XP 相对每日目标分 5 档。 */
export function Heatmap({ data, weeks = 53, goal = 20 }: { data: HeatDay[]; weeks?: number; goal?: number }) {
  const { cols, months } = useMemo(() => {
    const map = new Map(data.map((d) => [d.date, d]));
    const today = new Date();
    const end = new Date(today);
    end.setDate(end.getDate() + (6 - end.getDay()));
    const start = new Date(end);
    start.setDate(start.getDate() - weeks * 7 + 1);
    const cols: { date: string; xp: number; d?: HeatDay; future: boolean }[][] = [];
    const months: { col: number; label: string }[] = [];
    const cur = new Date(start);
    for (let w = 0; w < weeks; w++) {
      const col = [];
      for (let i = 0; i < 7; i++) {
        const key = fmt(cur);
        const d = map.get(key);
        if (cur.getDate() === 1) months.push({ col: w, label: `${cur.getMonth() + 1}月` });
        col.push({ date: key, xp: d?.xp ?? 0, d, future: cur > today });
        cur.setDate(cur.getDate() + 1);
      }
      cols.push(col);
    }
    return { cols, months };
  }, [data, weeks]);

  const cell = 11;
  const gap = 3;
  const scrollRef = useRef<HTMLDivElement>(null);
  // 窄屏时默认滚到最右边（最近的日期）
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
  }, [cols]);
  return (
    <div ref={scrollRef} className="scroll-thin overflow-x-auto">
      <svg width={weeks * (cell + gap) + 24} height={7 * (cell + gap) + 18} className="text-[9px]">
        {months.map((m) => (
          <text key={`${m.col}-${m.label}`} x={24 + m.col * (cell + gap)} y={9} className="fill-slate-400">
            {m.label}
          </text>
        ))}
        {["一", "三", "五"].map((l, i) => (
          <text key={l} x={0} y={18 + (i * 2 + 1) * (cell + gap) + 9} className="fill-slate-400">
            {l}
          </text>
        ))}
        {cols.map((col, w) =>
          col.map((c, i) => (
            <rect
              key={c.date}
              x={24 + w * (cell + gap)}
              y={14 + i * (cell + gap)}
              width={cell}
              height={cell}
              rx={2.5}
              style={{ fill: c.future ? "transparent" : HEAT[level(c.xp, goal)] }}
            >
              <title>{`${c.date}  ${c.xp} XP${c.d ? `  ·  复习 ${c.d.reviews}  ·  提问 ${c.d.questions}` : ""}`}</title>
            </rect>
          )),
        )}
      </svg>
      <div className="mt-1 flex items-center justify-end gap-1 text-[10px] text-slate-400">
        少
        {HEAT.map((c) => (
          <span key={c} className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c }} />
        ))}
        多
      </div>
    </div>
  );
}

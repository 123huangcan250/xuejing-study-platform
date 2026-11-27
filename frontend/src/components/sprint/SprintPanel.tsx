"use client";
import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { CalendarClock, CheckCircle2, ChevronDown, ChevronRight, Circle, Flag, Layers, Loader2, PenLine, RotateCcw, Swords, Target } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Course, Reward, SprintPlan, SprintTask } from "@/lib/types";
import { useProfile } from "@/stores/profile";
import { Button, Card, ProgressBar } from "@/components/ui";

const KIND: Record<SprintTask["kind"], { label: string; color: string; icon: React.ElementType }> = {
  learn: { label: "学习", color: "bg-indigo-50 text-indigo-700", icon: Target },
  review: { label: "回顾", color: "bg-sky-50 text-sky-700", icon: RotateCcw },
  cards: { label: "闪卡", color: "bg-emerald-50 text-emerald-700", icon: Layers },
  wrong: { label: "错题", color: "bg-rose-50 text-rose-700", icon: PenLine },
  mock: { label: "模拟卷", color: "bg-violet-50 text-violet-700", icon: Flag },
};

export function SprintPanel({
  courseId,
  course,
  onCourseChanged,
  onPractice,
  onGoCards,
  onGoSim,
  onGoKnowledge,
}: {
  courseId: number;
  course: Course | null;
  onCourseChanged: () => void;
  onPractice: (nodeId: number | null, mode: "mix" | "wrong") => void;
  onGoCards: () => void;
  onGoSim: () => void;
  onGoKnowledge: () => void;
}) {
  const [plan, setPlan] = useState<SprintPlan | null>(null);
  const [date, setDate] = useState("");
  const [minutes, setMinutes] = useState(90);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const p = await api.get<SprintPlan>(`/api/courses/${courseId}/sprint`);
      setPlan(p);
      setDate(p.exam_date);
      setMinutes(p.daily_minutes);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [courseId]);
  useEffect(() => {
    void load();
  }, [load]);

  const save = async (clear = false) => {
    setSaving(true);
    try {
      await api.patch(`/api/courses/${courseId}`, { exam_date: clear ? "" : date, sprint_minutes: minutes });
      await load();
      onCourseChanged();
      if (clear) setDate("");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (t: SprintTask) => {
    // 先乐观更新，再同步
    setPlan((p) => p && { ...p, days: p.days?.map((d) => ({ ...d, tasks: d.tasks.map((x) => (x.key === t.key ? { ...x, done: !t.done } : x)) })) });
    try {
      const r = await api.post<{ reward: Reward | null }>(`/api/courses/${courseId}/sprint/done`, { key: t.key, done: !t.done });
      useProfile.getState().applyReward(r.reward);
      if (t.kind === "learn") void load();
    } catch (e) {
      toast.error((e as Error).message);
      void load();
    }
  };

  const go = (t: SprintTask) => {
    if (t.kind === "learn" || t.kind === "review") onPractice(t.node_id ?? null, "mix");
    else if (t.kind === "wrong") onPractice(null, "wrong");
    else if (t.kind === "cards") onGoCards();
    else onGoSim();
  };
  const goLabel = (t: SprintTask) => (t.kind === "cards" ? "去复习" : t.kind === "mock" ? "去做卷子" : t.kind === "wrong" ? "去做错题" : "做几道题");

  if (!plan) return <div className="p-10 text-center text-sm text-slate-400">加载中…</div>;

  const settings = (
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-xs text-slate-500">
        考试日期
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="mt-1 block h-9 rounded-lg border border-slate-300 bg-surface px-2 text-sm text-slate-800" />
      </label>
      <label className="text-xs text-slate-500">
        每天能学
        <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className="mt-1 block h-9 rounded-lg border border-slate-300 bg-surface px-2 text-sm text-slate-800">
          {[30, 45, 60, 90, 120, 180, 240, 360].map((m) => (
            <option key={m} value={m}>
              {m >= 60 ? `${m / 60} 小时` : `${m} 分钟`}
            </option>
          ))}
        </select>
      </label>
      <Button onClick={() => void save()} disabled={saving || !date}>
        {saving ? <Loader2 size={14} className="animate-spin" /> : <CalendarClock size={14} />} {plan.configured ? "更新计划" : "生成冲刺计划"}
      </Button>
      {plan.configured && (
        <button className="pb-2 text-xs text-slate-400 underline" onClick={() => void save(true)}>
          清除考试日期
        </button>
      )}
    </div>
  );

  if (!plan.configured)
    return (
      <div className="mx-auto max-w-2xl space-y-4 p-4 sm:p-6">
        <Card className="space-y-4 p-6">
          <div className="flex items-center gap-2 text-lg font-semibold text-slate-800">
            <CalendarClock className="text-indigo-500" /> 考前冲刺计划
          </div>
          <div className="text-sm leading-6 text-slate-500">告诉我考试日期和你每天能学多久，我按「真题考点频率 × 重要度 × 你还没掌握的程度」排出每天学什么：高频薄弱点先学，学完隔两天回顾，最后一两天只做错题和限时仿真卷。计划会随你做题自动调整。</div>
          {settings}
        </Card>
      </div>
    );

  if (plan.passed)
    return (
      <div className="mx-auto max-w-2xl space-y-4 p-4 sm:p-6">
        <Card className="space-y-3 p-6 text-sm text-slate-600">
          <div className="text-lg font-semibold text-slate-800">考试日期已过（{plan.exam_date}）</div>
          <div>辛苦了！换一个新的考试日期可以开始下一轮，或清除日期。</div>
          {settings}
        </Card>
      </div>
    );

  const days = plan.days ?? [];
  const today = days[0];
  const doneToday = today?.tasks.filter((t) => t.done).length ?? 0;

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 sm:p-6">
      <Card className="space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-5">
          <div className="text-center">
            <div className="text-5xl font-bold tabular-nums text-indigo-600">{plan.days_left}</div>
            <div className="text-xs text-slate-500">天后考试 · {plan.exam_date}</div>
          </div>
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>
                知识点覆盖 {plan.scheduled! + plan.nodes_done!}/{plan.nodes_total}
                {plan.nodes_done ? `（已学 ${plan.nodes_done}）` : ""}
              </span>
              <span>{plan.coverage != null ? `${Math.round(plan.coverage * 100)}%` : ""}</span>
            </div>
            <ProgressBar value={(plan.coverage ?? 0) * 100} max={100} color={(plan.coverage ?? 0) >= 0.99 ? "bg-emerald-500" : "bg-amber-500"} />
            {today && (
              <div className="text-xs text-slate-500">
                今天 {today.tasks.length} 项 · 约 {today.minutes} 分钟 · 已完成 {doneToday}
              </div>
            )}
          </div>
        </div>
        {plan.tips?.map((t, i) => (
          <div key={i} className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
            {t}
            {plan.no_knowledge && i === 0 && (
              <button className="ml-2 text-indigo-600 underline" onClick={onGoKnowledge}>
                去梳理
              </button>
            )}
          </div>
        ))}
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer select-none">调整考试日期 / 每天时长</summary>
          <div className="pt-3">{settings}</div>
        </details>
      </Card>

      {days.map((d, idx) => {
        const isOpen = open[d.date] ?? idx === 0;
        const n = d.tasks.filter((t) => t.done).length;
        return (
          <Card key={d.date} className="overflow-hidden">
            <button className="flex w-full items-center gap-2 p-3.5 text-left" onClick={() => setOpen((o) => ({ ...o, [d.date]: !isOpen }))}>
              {isOpen ? <ChevronDown size={16} className="text-slate-400" /> : <ChevronRight size={16} className="text-slate-400" />}
              <span className={clsx("font-semibold", idx === 0 ? "text-indigo-700" : "text-slate-700")}>{d.label}</span>
              <span className="text-xs text-slate-400">{d.date.slice(5)}</span>
              {d.final && <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[11px] text-rose-600">最后冲刺</span>}
              <span className="ml-auto text-xs text-slate-400">
                {n}/{d.tasks.length} 项 · {d.minutes} 分钟
              </span>
            </button>
            {isOpen && (
              <div className="divide-y divide-slate-100 border-t border-slate-100">
                {d.tasks.map((t) => {
                  const K = KIND[t.kind];
                  return (
                    <div key={t.key} className={clsx("flex items-start gap-3 px-4 py-2.5", t.done && "opacity-55")}>
                      <button onClick={() => void toggle(t)} className="mt-0.5 text-slate-300 hover:text-emerald-500" aria-label={t.done ? "取消完成" : "标记完成"}>
                        {t.done ? <CheckCircle2 size={18} className="text-emerald-500" /> : <Circle size={18} />}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className={clsx("rounded px-1.5 py-0.5 text-[11px]", K.color)}>{K.label}</span>
                          <span className={clsx("text-sm text-slate-800", t.done && "line-through")}>{t.title.replace(/（.*）$/, "")}</span>
                          <span className="text-xs text-slate-400">{t.minutes} 分钟</span>
                        </div>
                        {(t.reason || t.chapter) && (
                          <div className="mt-0.5 text-xs text-slate-400">
                            {t.chapter ? `${t.chapter.replace(/（.*）$/, "")} · ` : ""}
                            {t.reason}
                          </div>
                        )}
                      </div>
                      {!t.done && (
                        <Button size="sm" variant="soft" onClick={() => go(t)}>
                          {goLabel(t)}
                        </Button>
                      )}
                    </div>
                  );
                })}
                {d.tasks.length === 0 && <div className="px-4 py-3 text-xs text-slate-400">这天没有安排任务。</div>}
              </div>
            )}
          </Card>
        );
      })}

      {!!plan.skipped_count && (
        <Card className="space-y-2 p-4">
          <div className="flex items-center gap-1.5 text-sm font-semibold text-slate-700">
            <Swords size={15} className="text-amber-500" /> 时间排不下的 {plan.skipped_count} 个知识点（优先级较低）
          </div>
          <div className="flex flex-wrap gap-1.5">
            {plan.skipped?.map((s) => (
              <span key={s.node_id} title={s.reason} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">
                {s.title.replace(/（.*）$/, "")}
              </span>
            ))}
            {plan.skipped_count! > (plan.skipped?.length ?? 0) && <span className="text-xs text-slate-400">…等</span>}
          </div>
        </Card>
      )}
    </div>
  );
}

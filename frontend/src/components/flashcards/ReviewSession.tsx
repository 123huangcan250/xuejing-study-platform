"use client";
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { PartyPopper, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import type { Flashcard, Reward } from "@/lib/types";
import { Markdown } from "@/components/Markdown";
import { Button, ProgressBar } from "@/components/ui";
import { useProfile } from "@/stores/profile";
import { beep } from "@/lib/fx";

const RATINGS = [
  { r: 1, label: "忘了", key: "1", cls: "bg-rose-50 text-rose-700 hover:bg-rose-100" },
  { r: 2, label: "困难", key: "2", cls: "bg-amber-50 text-amber-700 hover:bg-amber-100" },
  { r: 3, label: "记得", key: "3", cls: "bg-emerald-50 text-emerald-700 hover:bg-emerald-100" },
  { r: 4, label: "简单", key: "4", cls: "bg-sky-50 text-sky-700 hover:bg-sky-100" },
];

/** FSRS 复习会话：空格翻面，1~4 评分。 */
export function ReviewSession({ courseId, onDone }: { courseId?: number; onDone?: () => void }) {
  const [queue, setQueue] = useState<Flashcard[] | null>(null);
  const [total, setTotal] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [stats, setStats] = useState({ done: 0, xp: 0, again: 0 });
  const [busy, setBusy] = useState(false);
  const applyReward = useProfile((s) => s.applyReward);

  const load = useCallback(async () => {
    const q = await api.get<Flashcard[]>(`/api/flashcards/due${courseId ? `?course_id=${courseId}` : ""}`);
    setQueue(q);
    setTotal(q.length);
    setFlipped(false);
    setStats({ done: 0, xp: 0, again: 0 });
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const card = queue?.[0];

  const rate = useCallback(
    async (rating: number) => {
      if (!card || busy) return;
      setBusy(true);
      try {
        const r = await api.post<{ card: Flashcard; reward: Reward }>(`/api/flashcards/${card.id}/review`, { rating });
        beep(rating === 1 ? "bad" : "good");
        // 忘记的卡如果 10 分钟内又到期，放回队尾本轮再复习一次
        const again = rating === 1 && new Date(r.card.due).getTime() - Date.now() < 15 * 60 * 1000;
        setQueue((q) => {
          if (!q) return q;
          const rest = q.slice(1);
          return again ? [...rest, { ...card, preview: card.preview }] : rest;
        });
        setStats((s) => ({ done: s.done + 1, xp: s.xp + r.reward.xp, again: s.again + (rating === 1 ? 1 : 0) }));
        if (r.reward.level_up || r.reward.new_achievements.length || r.reward.streak_lit_now) applyReward(r.reward);
        else applyReward({ ...r.reward, xp: 0, coins: 0 }); // 复习中不刷屏，只同步数值
        setFlipped(false);
      } finally {
        setBusy(false);
      }
    },
    [card, busy, applyReward],
  );

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT" || (e.target as HTMLElement)?.tagName === "TEXTAREA") return;
      if (e.code === "Space") {
        e.preventDefault();
        if (!flipped) {
          setFlipped(true);
          beep("flip");
        }
      } else if (flipped && ["1", "2", "3", "4"].includes(e.key)) void rate(Number(e.key));
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [flipped, rate]);

  if (queue === null) return <div className="p-10 text-center text-sm text-slate-400">加载到期卡片…</div>;

  if (!card) {
    return (
      <div className="mx-auto max-w-md rounded-2xl border border-slate-200 bg-surface p-8 text-center shadow-sm">
        <PartyPopper className="mx-auto mb-3 text-indigo-500" size={36} />
        {stats.done ? (
          <>
            <h3 className="mb-1 text-lg font-semibold text-slate-800">本轮复习完成！</h3>
            <p className="text-sm text-slate-500">
              复习 {stats.done} 次 · 获得 {stats.xp} XP · 忘记 {stats.again} 次
            </p>
          </>
        ) : (
          <>
            <h3 className="mb-1 text-lg font-semibold text-slate-800">现在没有到期的卡片</h3>
            <p className="text-sm text-slate-500">FSRS 会在你快要忘记时再提醒你。去画布划词做几张新卡吧。</p>
          </>
        )}
        <div className="mt-5 flex justify-center gap-2">
          <Button variant="outline" onClick={() => void load()}>
            <RotateCcw size={14} /> 再检查一次
          </Button>
          {onDone && <Button onClick={onDone}>返回</Button>}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-3 flex items-center gap-3 text-xs text-slate-500">
        <span className="shrink-0 tabular-nums">
          {stats.done} / {Math.max(total, stats.done + queue.length)}
        </span>
        <ProgressBar value={stats.done} max={Math.max(total, stats.done + queue.length)} />
        <span className="shrink-0 text-indigo-600">+{stats.xp} XP</span>
      </div>
      <AnimatePresence mode="wait">
        <motion.div
          key={`${card.id}-${stats.done}`}
          initial={{ opacity: 0, y: 16, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, x: -40 }}
          transition={{ duration: 0.22 }}
          className="min-h-[300px] rounded-2xl border border-slate-200 bg-surface p-8 shadow-md"
        >
          <div className="mb-2 text-[11px] uppercase tracking-wider text-slate-400">
            {card.card_type === "cloze" ? "填空" : card.card_type === "formula" ? "公式" : "问题"}
            {card.lapses > 0 && <span className="ml-2 text-rose-400">已遗忘 {card.lapses} 次</span>}
          </div>
          <div className="text-lg">
            <Markdown text={card.front} className="!text-lg !text-slate-800" />
          </div>
          <AnimatePresence>
            {flipped && (
              <motion.div initial={{ opacity: 0, rotateX: -20 }} animate={{ opacity: 1, rotateX: 0 }} className="mt-6 border-t border-dashed border-slate-200 pt-5">
                <Markdown text={card.back} className="!text-base" />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </AnimatePresence>
      <div className="mt-4">
        {!flipped ? (
          <Button className="h-11 w-full text-base" onClick={() => { setFlipped(true); beep("flip"); }}>
            显示答案 <kbd className="ml-2 rounded bg-white/20 px-1.5 text-xs">空格</kbd>
          </Button>
        ) : (
          <div className="grid grid-cols-4 gap-2">
            {RATINGS.map((x) => (
              <button key={x.r} disabled={busy} className={`rounded-xl px-2 py-2.5 text-sm font-medium transition ${x.cls}`} onClick={() => void rate(x.r)}>
                {x.label}
                <div className="text-[11px] font-normal opacity-70">
                  {card.preview?.[String(x.r)]} · <kbd>{x.key}</kbd>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

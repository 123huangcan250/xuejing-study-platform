"use client";
import { useCallback, useEffect, useState } from "react";
import { Play, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Flashcard, Reward } from "@/lib/types";
import { useProfile } from "@/stores/profile";
import { Button, Card, inputCls } from "@/components/ui";
import { Markdown } from "@/components/Markdown";
import { ReviewSession } from "./ReviewSession";

const STATE = ["新卡", "学习中", "复习", "重学"];

export function FlashcardsPanel({ courseId }: { courseId: number }) {
  const [cards, setCards] = useState<Flashcard[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");

  const load = useCallback(async () => setCards(await api.get<Flashcard[]>(`/api/courses/${courseId}/flashcards`)), [courseId]);
  useEffect(() => {
    void load();
  }, [load]);

  const due = cards.filter((c) => new Date(c.due) <= new Date()).length;

  const add = async () => {
    if (!front.trim() || !back.trim()) return;
    const r = await api.post<{ cards: Flashcard[]; reward: Reward }>(`/api/courses/${courseId}/flashcards`, [{ front, back }]);
    useProfile.getState().applyReward(r.reward);
    setFront("");
    setBack("");
    void load();
  };

  if (reviewing)
    return (
      <div className="p-6">
        <ReviewSession courseId={courseId} onDone={() => { setReviewing(false); void load(); }} />
      </div>
    );

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6">
      <Card className="flex items-center gap-6 p-5">
        <div>
          <div className="text-3xl font-bold text-indigo-600">{due}</div>
          <div className="text-xs text-slate-500">张卡片到期</div>
        </div>
        <div className="text-sm text-slate-500">
          共 {cards.length} 张 · FSRS 会根据你每次的评分动态安排下次复习时间（目标记忆保持率 90%）
        </div>
        <Button className="ml-auto" disabled={!due} onClick={() => setReviewing(true)}>
          <Play size={15} /> 开始复习
        </Button>
      </Card>

      <Card className="p-4">
        <div className="mb-2 text-sm font-medium text-slate-700">手动添加</div>
        <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
          <textarea className={`${inputCls} h-16`} placeholder="正面：问题（支持 Markdown / $公式$）" value={front} onChange={(e) => setFront(e.target.value)} />
          <textarea className={`${inputCls} h-16`} placeholder="背面：答案" value={back} onChange={(e) => setBack(e.target.value)} />
          <Button className="h-16" onClick={() => void add()} disabled={!front.trim() || !back.trim()}>
            <Plus size={15} /> 添加
          </Button>
        </div>
      </Card>

      <div className="grid gap-3 md:grid-cols-2">
        {cards.map((c) => (
          <Card key={c.id} className="group p-4">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400">
              <span className="rounded bg-slate-100 px-1.5 py-0.5">{STATE[c.state] ?? "?"}</span>
              <span>到期 {new Date(c.due).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
              <span>复习 {c.reps} 次</span>
              {c.lapses > 0 && <span className="text-rose-400">遗忘 {c.lapses}</span>}
              <button
                className="ml-auto opacity-0 transition group-hover:opacity-100 hover:text-rose-500"
                onClick={async () => {
                  if (!confirm("删除这张闪卡？")) return;
                  await api.del(`/api/flashcards/${c.id}`);
                  toast("已删除");
                  void load();
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
            <Markdown text={c.front} className="font-medium !text-slate-800" />
            <div className="mt-2 border-t border-dashed border-slate-100 pt-2">
              <Markdown text={c.back} className="!text-[13px] !text-slate-500" />
            </div>
          </Card>
        ))}
        {!cards.length && <div className="col-span-2 py-10 text-center text-sm text-slate-400">还没有闪卡。在阅读器里划词选择「做成闪卡」，或在画布节点上点「闪卡」。</div>}
      </div>
    </div>
  );
}

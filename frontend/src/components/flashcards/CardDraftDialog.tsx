"use client";
import { useEffect, useState } from "react";
import { Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { CardDraft, Flashcard, Reward } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { useProfile } from "@/stores/profile";
import { Button, Modal, inputCls } from "@/components/ui";

/** 从划词 / 画布节点生成闪卡：先由轻量档模型给出草稿，用户可编辑、删减后保存。 */
export function CardDraftDialog({ onSaved }: { onSaved?: () => void }) {
  const { cardDraft, openCardDraft, courseId } = useWorkspace();
  const [drafts, setDrafts] = useState<(CardDraft & { keep: boolean })[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const generate = async () => {
    if (!cardDraft || !courseId) return;
    setLoading(true);
    try {
      const r = await api.post<{ cards: CardDraft[] }>(`/api/courses/${courseId}/flashcards/generate`, {
        text: cardDraft.text,
        context: cardDraft.context ?? "",
        count: cardDraft.text.length > 600 ? 5 : 3,
      });
      setDrafts(r.cards.map((c) => ({ ...c, keep: true })));
      if (!r.cards.length) toast("模型没有生成卡片，可以手动添加");
    } catch (e) {
      toast.error((e as Error).message);
      setDrafts([{ front: "", back: cardDraft.text.slice(0, 300), card_type: "basic", keep: true }]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setDrafts([]);
    if (cardDraft) void generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardDraft]);

  const save = async () => {
    const keep = drafts.filter((d) => d.keep && d.front.trim() && d.back.trim());
    if (!keep.length || !courseId || !cardDraft) return;
    setSaving(true);
    try {
      const r = await api.post<{ cards: Flashcard[]; reward: Reward | null }>(
        `/api/courses/${courseId}/flashcards`,
        keep.map((d) => ({ front: d.front, back: d.back, card_type: d.card_type, source: cardDraft.source, source_ref: cardDraft.source_ref ?? "" })),
      );
      toast.success(`已加入 ${r.cards.length} 张闪卡，今天就能开始复习`);
      useProfile.getState().applyReward(r.reward);
      openCardDraft(null);
      onSaved?.();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const update = (i: number, patch: Partial<CardDraft & { keep: boolean }>) =>
    setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <Modal open={!!cardDraft} onClose={() => openCardDraft(null)} title="✨ 做成闪卡" width="max-w-2xl">
      {cardDraft && (
        <div className="space-y-4">
          <div className="max-h-28 overflow-y-auto rounded-lg bg-amber-50 p-3 text-xs leading-5 text-slate-600 whitespace-pre-wrap">{cardDraft.text}</div>
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500">
              <Loader2 className="animate-spin" size={16} /> AI 正在提炼知识点…
            </div>
          ) : (
            <div className="space-y-3">
              {drafts.map((d, i) => (
                <div key={i} className={`rounded-xl border p-3 ${d.keep ? "border-indigo-200 bg-surface" : "border-slate-100 bg-slate-50 opacity-60"}`}>
                  <div className="mb-2 flex items-center gap-2 text-xs text-slate-500">
                    <input type="checkbox" checked={d.keep} onChange={(e) => update(i, { keep: e.target.checked })} />
                    <span>卡片 {i + 1}</span>
                    <select className="rounded border border-slate-200 px-1 py-0.5" value={d.card_type} onChange={(e) => update(i, { card_type: e.target.value })}>
                      <option value="basic">概念卡</option>
                      <option value="cloze">填空卡</option>
                      <option value="formula">公式卡</option>
                    </select>
                    <button className="ml-auto text-slate-400 hover:text-rose-500" onClick={() => setDrafts((ds) => ds.filter((_, j) => j !== i))}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <textarea className={`${inputCls} mb-2 h-16`} placeholder="正面：问题" value={d.front} onChange={(e) => update(i, { front: e.target.value })} />
                  <textarea className={`${inputCls} h-16`} placeholder="背面：答案" value={d.back} onChange={(e) => update(i, { back: e.target.value })} />
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setDrafts((ds) => [...ds, { front: "", back: "", card_type: "basic", keep: true }])}>
              <Plus size={14} /> 手动添加
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void generate()} disabled={loading}>
              <Sparkles size={14} /> 重新生成
            </Button>
            <div className="flex-1" />
            <Button onClick={() => void save()} disabled={saving || loading || !drafts.some((d) => d.keep && d.front.trim() && d.back.trim())}>
              保存 {drafts.filter((d) => d.keep).length} 张
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

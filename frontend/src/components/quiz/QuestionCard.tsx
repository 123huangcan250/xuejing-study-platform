"use client";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { CheckCircle2, Layers, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { AttemptResult, Quiz } from "@/lib/types";
import { useProfile } from "@/stores/profile";
import { Button } from "@/components/ui";
import { Markdown } from "@/components/Markdown";

export const QTYPE_NAME: Record<string, string> = { choice: "单选题", multi: "多选题", judge: "判断题", fill: "填空题", short: "简答题", calc: "计算题", proof: "证明题" };
const LETTERS = "ABCDEF";

/** 一道题的作答卡：按题型给输入、提交后显示对错 / 解析 / 参考答案；做错的题自动进闪卡。 */
export function QuestionCard({
  q,
  onResult,
  onNext,
  nextLabel = "下一题",
  compact = false,
  hint,
}: {
  q: Quiz;
  onResult?: (r: AttemptResult) => void;
  onNext?: () => void;
  nextLabel?: string;
  compact?: boolean;
  hint?: React.ReactNode;
}) {
  const [pick, setPick] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [blanks, setBlanks] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<AttemptResult | null>(null);

  useEffect(() => {
    setPick([]);
    setText("");
    setBlanks(Array(q.blanks ?? 0).fill(""));
    setRes(null);
  }, [q.id, q.blanks]);

  const answerValue = () => {
    if (q.qtype === "choice" || q.qtype === "multi") return [...pick].sort().join("");
    if (q.qtype === "judge") return pick[0] ?? "";
    if (q.qtype === "fill") return JSON.stringify(blanks);
    return text;
  };
  const canSubmit = q.qtype === "fill" ? blanks.some((b) => b.trim()) : q.qtype === "short" || q.qtype === "calc" || q.qtype === "proof" ? true : pick.length > 0;

  const done = (r: AttemptResult) => {
    setRes(r);
    useProfile.getState().applyReward(r.reward);
    if (r.flashcard_created) toast("已加入闪卡，之后会按遗忘曲线再问你", { icon: "🃏", duration: 2200 });
    onResult?.(r);
  };

  const submit = async (self_score?: number) => {
    setBusy(true);
    try {
      done(await api.post<AttemptResult>(`/api/questions/${q.id}/attempt`, { answer: answerValue(), self_score }));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const locked = !!res?.graded;
  const toggle = (v: string) => {
    if (locked) return;
    if (q.qtype === "multi") setPick((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p, v]));
    else setPick([v]);
  };
  const right = (v: string) => locked && (res?.correct_answer?.includes(v) || false);

  return (
    <div className={clsx("space-y-3", compact ? "" : "rounded-2xl border border-slate-200 bg-surface p-5")}>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-700">{QTYPE_NAME[q.qtype]}</span>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">难度 {"★".repeat(q.difficulty)}</span>
        {q.ability && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">{q.ability}</span>}
        {q.points > 0 && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">{q.points} 分</span>}
        {q.origin === "simulated" && <span className="rounded bg-violet-50 px-1.5 py-0.5 text-violet-700">真题仿真</span>}
        {hint}
      </div>
      <Markdown text={q.stem} className="text-[15px] leading-7 text-slate-800" />

      {(q.qtype === "choice" || q.qtype === "multi") && (
        <div className="space-y-2">
          {q.options.map((o, i) => {
            const v = LETTERS[i];
            const on = pick.includes(v);
            return (
              <button
                key={v}
                onClick={() => toggle(v)}
                disabled={locked}
                className={clsx(
                  "flex w-full items-start gap-2.5 rounded-xl border px-3 py-2 text-left text-sm transition",
                  locked ? (right(v) ? "border-emerald-400 bg-emerald-50" : on ? "border-rose-300 bg-rose-50" : "border-slate-200 opacity-70") : on ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:border-indigo-300 hover:bg-slate-50",
                )}
              >
                <span className={clsx("mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-xs font-semibold", on ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500")}>{v}</span>
                <Markdown text={o} className="min-w-0 flex-1 text-slate-700" />
              </button>
            );
          })}
        </div>
      )}
      {q.qtype === "judge" && (
        <div className="flex gap-3">
          {["对", "错"].map((v) => (
            <button
              key={v}
              onClick={() => toggle(v)}
              disabled={locked}
              className={clsx(
                "h-11 flex-1 rounded-xl border text-base font-medium transition",
                locked ? (res?.correct_answer === v ? "border-emerald-400 bg-emerald-50 text-emerald-700" : pick[0] === v ? "border-rose-300 bg-rose-50 text-rose-600" : "border-slate-200 opacity-60") : pick[0] === v ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-slate-200 hover:border-indigo-300",
              )}
            >
              {v === "对" ? "✓ 对" : "✗ 错"}
            </button>
          ))}
        </div>
      )}
      {q.qtype === "fill" && (
        <div className="flex flex-wrap gap-2">
          {blanks.map((b, i) => (
            <input
              key={i}
              value={b}
              disabled={locked}
              onChange={(e) => setBlanks((arr) => arr.map((x, j) => (j === i ? e.target.value : x)))}
              placeholder={`第 ${i + 1} 空`}
              className="h-9 min-w-32 flex-1 rounded-lg border border-slate-300 bg-surface px-3 text-sm outline-none focus:border-indigo-400"
            />
          ))}
        </div>
      )}
      {(q.qtype === "short" || q.qtype === "calc" || q.qtype === "proof") && (
        <textarea
          value={text}
          disabled={locked || !!res?.need_self_grade}
          onChange={(e) => setText(e.target.value)}
          rows={q.qtype === "short" ? 4 : 7}
          placeholder={q.qtype === "calc" ? "写出步骤和结果（公式可用 $...$）" : "写下你的作答…"}
          className="w-full resize-y rounded-xl border border-slate-300 bg-surface p-3 text-sm leading-6 outline-none focus:border-indigo-400"
        />
      )}

      {!res && (
        <div className="flex items-center gap-2">
          <Button onClick={() => void submit()} disabled={busy || !canSubmit}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : null}
            {q.qtype === "short" || q.qtype === "calc" || q.qtype === "proof" ? (busy ? "批改中…" : "提交批改") : "提交"}
          </Button>
        </div>
      )}

      {res?.need_self_grade && (
        <div className="space-y-2 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-sm">
          <div className="font-medium text-amber-800">暂时没法让模型批改，请对照参考答案自评：</div>
          <Reference res={res} />
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => void submit(1)}>基本答对</Button>
            <Button size="sm" variant="outline" onClick={() => void submit(0.5)}>部分答对</Button>
            <Button size="sm" variant="outline" onClick={() => void submit(0)}>答错了</Button>
          </div>
        </div>
      )}

      {locked && res && (
        <div className={clsx("space-y-2 rounded-xl border p-3 text-sm", res.correct ? "border-emerald-200 bg-emerald-50/60" : "border-rose-200 bg-rose-50/60")}>
          <div className="flex items-center gap-1.5 font-medium">
            {res.correct ? <CheckCircle2 size={16} className="text-emerald-600" /> : <XCircle size={16} className="text-rose-500" />}
            <span className={res.correct ? "text-emerald-700" : "text-rose-600"}>
              {res.correct ? ((res.score ?? 1) < 1 ? "基本正确" : "答对了") : (res.score ?? 0) > 0 ? "部分正确" : "答错了"}
            </span>
            {q.qtype !== "choice" && q.qtype !== "judge" && res.score != null && <span className="text-xs text-slate-500">得分 {Math.round(res.score * 100)}%</span>}
            {res.graded_by === "llm" && <span className="text-[11px] text-slate-400">AI 批改</span>}
            {res.flashcard_created && (
              <span className="ml-auto flex items-center gap-1 text-xs text-indigo-600">
                <Layers size={12} /> 已加入闪卡
              </span>
            )}
          </div>
          {res.feedback && <div className="text-slate-600">{res.feedback}</div>}
          <Reference res={res} />
          {res.graded_by === "llm" && res.attempt_id && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              批改不对？
              {[1, 0.5, 0].map((sc) => (
                <button
                  key={sc}
                  className="rounded border border-slate-200 px-1.5 py-0.5 hover:bg-slate-50"
                  onClick={async () => {
                    await api.patch(`/api/attempts/${res.attempt_id}`, { score: sc });
                    setRes({ ...res, score: sc, correct: sc >= 0.6, graded_by: "self" });
                    toast.success("已按你的判断修正");
                  }}
                >
                  {sc === 1 ? "其实全对" : sc === 0.5 ? "算一半" : "其实不对"}
                </button>
              ))}
            </div>
          )}
          {onNext && (
            <div className="pt-1">
              <Button onClick={onNext}>{nextLabel}</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Reference({ res }: { res: AttemptResult }) {
  return (
    <div className="space-y-1 text-slate-700">
      <div>
        <span className="mr-1 font-medium text-slate-500">参考答案</span>
        <Markdown text={res.answer} className="inline" />
      </div>
      {res.key_points?.length > 0 && <div className="text-xs text-slate-500">评分要点：{res.key_points.join("；")}</div>}
      {res.explanation && <div className="text-xs text-slate-500">解析：{res.explanation}</div>}
    </div>
  );
}

"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { BookOpen, ClipboardCheck, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import type { ExamDoc, ExamQ } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { Button, Card } from "@/components/ui";

const ABILITY_COLOR: Record<string, string> = {
  记忆: "bg-slate-100 text-slate-600",
  理解: "bg-sky-50 text-sky-700",
  应用: "bg-emerald-50 text-emerald-700",
  分析: "bg-amber-50 text-amber-800",
  综合: "bg-rose-50 text-rose-700",
};

function Bars({ title, counts }: { title: string; counts: [string, number][] }) {
  const max = Math.max(1, ...counts.map(([, n]) => n));
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-1.5 text-xs font-semibold text-slate-500">{title}</div>
      <div className="space-y-1">
        {counts.map(([k, n]) => (
          <div key={k} className="flex items-center gap-2 text-xs">
            <span className="w-8 shrink-0 text-slate-600">{k}</span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full rounded-full bg-indigo-500" style={{ width: `${(n / max) * 100}%` }} />
            </div>
            <span className="w-5 text-right tabular-nums text-slate-500">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ExamPanel({ courseId, onOpenReader, onGoLibrary, onGoKnowledge }: { courseId: number; onOpenReader: () => void; onGoLibrary: () => void; onGoKnowledge: () => void }) {
  const [exams, setExams] = useState<ExamDoc[] | null>(null);
  const [hasMap, setHasMap] = useState(true);
  const { jumpTo } = useWorkspace();

  const load = useCallback(async () => {
    try {
      const [e, k] = await Promise.all([
        api.get<ExamDoc[]>(`/api/courses/${courseId}/exams`),
        api.get<{ tree: unknown[] }>(`/api/courses/${courseId}/knowledge`),
      ]);
      setExams(e);
      setHasMap(k.tree.length > 0);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);
  const running = exams?.some((e) => e.analysis.state === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 2000);
    return () => clearInterval(t);
  }, [running, load]);

  const all = useMemo(() => (exams ?? []).flatMap((e) => e.questions), [exams]);
  const stats = useMemo(() => {
    const count = (f: (q: ExamQ) => string) => {
      const m = new Map<string, number>();
      all.forEach((q) => f(q) && m.set(f(q), (m.get(f(q)) ?? 0) + 1));
      return [...m.entries()].sort((a, b) => b[1] - a[1]);
    };
    return { ability: count((q) => q.ability), qtype: count((q) => q.qtype) };
  }, [all]);

  const analyze = async (did: number) => {
    try {
      await api.post(`/api/documents/${did}/exam/analyze`);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "无法开始分析");
    }
  };

  if (!exams) return <div className="p-10 text-center text-sm text-slate-400">加载中…</div>;
  if (!exams.length) {
    return (
      <div className="mx-auto max-w-xl p-10 text-center">
        <div className="mx-auto mb-4 grid h-16 w-16 place-items-center rounded-2xl bg-amber-50 text-amber-600">
          <ClipboardCheck size={30} />
        </div>
        <h2 className="text-lg font-bold text-slate-800">试卷与考点</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">上传往年试卷（上传时选「试卷」），AI 会拆出每道题、识别考点和能力要求，并对应到课件的知识点。之后读课件时，相关页面会提示「这里是考点」。</p>
        <Button className="mt-4" onClick={onGoLibrary}>
          去资料库上传试卷
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      {!hasMap && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <span>要把考点对应到课件，需要先对课件做「知识梳理」。</span>
          <Button size="sm" onClick={onGoKnowledge}>
            去知识梳理
          </Button>
        </div>
      )}

      {all.length > 0 && (
        <Card className="flex flex-wrap gap-8 p-5">
          <div className="text-center">
            <div className="text-3xl font-bold text-indigo-600">{all.length}</div>
            <div className="text-xs text-slate-500">道题 · {exams.length} 份试卷</div>
          </div>
          <Bars title="能力层级分布" counts={stats.ability} />
          <Bars title="题型分布" counts={stats.qtype} />
        </Card>
      )}

      {exams.map((d) => {
        const a = d.analysis;
        return (
          <section key={d.document_id} className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="min-w-0 flex-1 truncate text-base font-bold text-slate-800">{d.filename.replace(/\.\w+$/, "")}</h3>
              {a.state === "running" ? (
                <span className="flex items-center gap-1.5 text-xs text-indigo-600">
                  <Loader2 size={13} className="animate-spin" /> {a.stage}
                  {a.total ? ` ${a.done}/${a.total}` : "…"}
                </span>
              ) : (
                <>
                  {a.state === "error" && <span className="max-w-72 truncate text-xs text-rose-600" title={a.error}>{a.error}</span>}
                  <Button size="sm" variant={d.questions.length ? "outline" : "primary"} disabled={d.status !== "ready"} onClick={() => void analyze(d.document_id)}>
                    <Sparkles size={13} /> {d.questions.length ? "重新分析" : "分析考点"}
                  </Button>
                </>
              )}
            </div>
            {d.questions.length === 0 && a.state !== "running" && <p className="text-xs text-slate-400">还没有分析。点「分析考点」拆分题目并对应到课件。</p>}
            {d.questions.map((q) => (
              <Card key={q.id} className="p-4">
                <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="rounded-md bg-ink px-2 py-0.5 font-semibold text-ink-fg">{q.number || "—"}</span>
                  {q.qtype && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{q.qtype}</span>}
                  {q.points != null && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{q.points} 分</span>}
                  {q.ability && <span className={clsx("rounded px-1.5 py-0.5", ABILITY_COLOR[q.ability] ?? "bg-slate-100")}>{q.ability}</span>}
                  <span className="text-amber-500" title={`难度 ${q.difficulty}/5`}>
                    {"●".repeat(q.difficulty)}
                    <span className="text-slate-300">{"●".repeat(5 - q.difficulty)}</span>
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-[13.5px] leading-7 text-slate-800">{q.text}</p>
                {q.topics.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                    <span className="text-slate-400">考点</span>
                    {q.topics.map((t) => (
                      <span key={t} className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-slate-400">对应课件</span>
                  {q.nodes.length === 0 && <span className="text-slate-400">没有找到对应的知识点</span>}
                  {q.nodes.map((n) => (
                    <button
                      key={n.id}
                      className="flex items-center gap-1 rounded-lg bg-indigo-50 px-2 py-1 text-indigo-700 hover:bg-indigo-100"
                      onClick={() => {
                        if (n.document_id && n.page) {
                          jumpTo({ document_id: n.document_id, page: n.page, quote: "", rects: [] });
                          onOpenReader();
                        }
                      }}
                    >
                      <BookOpen size={11} /> {n.title}
                      {n.page ? ` · 第 ${n.page} 页` : ""}
                    </button>
                  ))}
                </div>
              </Card>
            ))}
          </section>
        );
      })}
    </div>
  );
}

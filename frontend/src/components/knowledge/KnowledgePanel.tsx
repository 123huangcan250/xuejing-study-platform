"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import clsx from "clsx";
import { BookOpen, Download, Flame, Layers, ListTree, Loader2, MessageCircleQuestion, Network, RefreshCw, Search, Sparkles, X } from "lucide-react";
import { toast } from "sonner";
import { API_BASE, api, ApiError } from "@/lib/api";
import type { ExamDoc, KNode, KnowledgeData } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { Button } from "@/components/ui";
import { MindMap } from "./MindMap";
import { Outline } from "./Outline";

const flat = (ns: KNode[]): KNode[] => ns.flatMap((n) => [n, ...flat(n.children)]);

export function KnowledgePanel({ courseId, courseName, onOpenReader, onGoLibrary }: { courseId: number; courseName: string; onOpenReader: () => void; onGoLibrary: () => void }) {
  const [data, setData] = useState<KnowledgeData | null>(null);
  const [exams, setExams] = useState<ExamDoc[]>([]);
  const [view, setView] = useState<"map" | "outline">("map");
  const [collapsed, setCollapsed] = useState<Set<number> | null>(null);
  const [selected, setSelected] = useState<KNode | null>(null);
  const [docFilter, setDocFilter] = useState<number | null>(null);
  const [examOnly, setExamOnly] = useState(false);
  const [query, setQuery] = useState("");
  const { jumpTo, ask, openCardDraft } = useWorkspace();

  const load = useCallback(async () => {
    try {
      const [k, e] = await Promise.all([api.get<KnowledgeData>(`/api/courses/${courseId}/knowledge`), api.get<ExamDoc[]>(`/api/courses/${courseId}/exams`)]);
      setData(k);
      setExams(e);
    } catch (err) {
      toast.error((err as Error).message);
    }
  }, [courseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 梳理进行中：定期刷新，已完成的课件会陆续出现
  const running = data?.status.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 2500);
    return () => clearInterval(t);
  }, [running, load]);

  const tree = useMemo(() => (data ? data.tree.filter((c) => docFilter === null || c.document_id === docFilter) : []), [data, docFilter]);

  // 默认：展开到小节（知识点折叠起来，导图不会一上来就铺满屏）
  useEffect(() => {
    if (data && collapsed === null && data.tree.length) setCollapsed(new Set(flat(data.tree).filter((n) => n.level === 2).map((n) => n.id)));
  }, [data, collapsed]);
  const col = collapsed ?? new Set<number>();
  const toggle = useCallback((id: number) => setCollapsed((c) => {
    const n = new Set(c ?? []);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  }), []);

  const build = async (force: boolean) => {
    if (!data) return;
    const n = data.docs.slides;
    if (!confirm(`将对 ${force ? n : Math.max(data.docs.slides - data.docs.built, 1)} 份课件做知识梳理。每份课件需要若干次模型调用（页数越多越多），会消耗模型额度。继续？`)) return;
    try {
      await api.post(`/api/courses/${courseId}/knowledge/build`, { force });
      setCollapsed(null);
      await load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "无法开始");
    }
  };

  const relatedQuestions = (n: KNode) => {
    const ids = new Set(flat([n]).map((x) => x.id));
    return exams.flatMap((d) => d.questions.filter((q) => q.nodes.some((x) => ids.has(x.id))).map((q) => ({ ...q, exam: d.filename.replace(/\.\w+$/, "") })));
  };

  if (!data) return <div className="p-10 text-center text-sm text-slate-400">加载中…</div>;
  const st = data.status;
  const hasTree = data.tree.length > 0;

  if (!hasTree && !running) {
    return (
      <div className="mx-auto max-w-xl p-10 text-center">
        <div className="mx-auto mb-4 grid h-16 w-16 place-items-center rounded-2xl bg-indigo-50 text-indigo-600">
          <Network size={30} />
        </div>
        <h2 className="text-lg font-bold text-slate-800">知识梳理与思维导图</h2>
        {data.docs.slides === 0 ? (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500">知识梳理只针对「课件」。这门课还没有课件类资料：上传时选择「课件」，或在资料库里把已有资料改成「课件」。</p>
            <Button className="mt-4" onClick={onGoLibrary}>
              去资料库
            </Button>
          </>
        ) : (
          <>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              把 {data.docs.slides} 份课件提炼成「课件 → 小节 → 知识点」，并记录每个知识点在哪一页。之后可以用思维导图浏览，试卷的考点也会对应到这里。
            </p>
            {st.state === "error" && <p className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{st.error}</p>}
            <Button className="mt-4" onClick={() => void build(false)}>
              <Sparkles size={15} /> 开始知识梳理
            </Button>
            <p className="mt-2 text-xs text-slate-400">会调用模型，只处理「课件」，试卷和参考资料不参与。</p>
          </>
        )}
      </div>
    );
  }

  const chapters = data.tree;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-slate-200 bg-surface px-4 py-2 text-xs">
        <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
          {([["map", "思维导图", Network], ["outline", "大纲", ListTree]] as const).map(([id, name, Icon]) => (
            <button key={id} onClick={() => setView(id)} className={clsx("flex items-center gap-1 rounded-md px-2.5 py-1 transition", view === id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-700")}>
              <Icon size={13} /> {name}
            </button>
          ))}
        </div>
        <select className="max-w-52 truncate rounded-md border border-slate-200 bg-surface px-2 py-1" value={docFilter ?? ""} onChange={(e) => setDocFilter(e.target.value ? Number(e.target.value) : null)}>
          <option value="">全部课件（{chapters.length}）</option>
          {chapters.map((c) => (
            <option key={c.id} value={c.document_id ?? ""}>
              {c.title}
            </option>
          ))}
        </select>
        <label className={clsx("flex cursor-pointer items-center gap-1 rounded-md px-2 py-1", examOnly ? "bg-amber-100 text-amber-800" : "text-slate-500 hover:bg-slate-100")} title={exams.some((e) => e.questions.length) ? "只显示在试卷里考过的知识点" : "还没有分析过试卷"}>
          <input type="checkbox" className="hidden" checked={examOnly} onChange={(e) => setExamOnly(e.target.checked)} />
          <Flame size={13} /> 只看考点
        </label>
        {view === "outline" ? (
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1.5 text-slate-400" />
            <input className="w-40 rounded-md border border-slate-200 bg-surface py-1 pl-6 pr-2 outline-none focus:border-indigo-400" placeholder="搜索知识点" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        ) : null}
        <div className="inline-flex items-center gap-0.5 text-slate-500">
          <button className="rounded px-1.5 py-1 hover:bg-slate-100" onClick={() => setCollapsed(new Set())}>
            展开全部
          </button>
          <button className="rounded px-1.5 py-1 hover:bg-slate-100" onClick={() => setCollapsed(new Set(flat(data.tree).filter((n) => n.level === 2).map((n) => n.id)))}>
            到小节
          </button>
          <button className="rounded px-1.5 py-1 hover:bg-slate-100" onClick={() => setCollapsed(new Set(data.tree.map((n) => n.id)))}>
            到章节
          </button>
        </div>
        <div className="flex-1" />
        {running ? (
          <span className="flex items-center gap-1.5 text-indigo-600">
            <Loader2 size={13} className="animate-spin" /> 梳理中 {st.done}/{st.total}
            {st.current ? <span className="max-w-44 truncate text-slate-400">· {st.current}</span> : null}
            <button className="ml-1 rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100" onClick={() => void api.post(`/api/courses/${courseId}/knowledge/cancel`).then(load)}>
              取消
            </button>
          </span>
        ) : (
          <>
            {st.state === "error" && <span className="max-w-60 truncate text-rose-600" title={st.error}>梳理中断：{st.error}</span>}
            {data.docs.slides > data.docs.built && (
              <Button size="sm" variant="soft" onClick={() => void build(false)}>
                <Sparkles size={13} /> 梳理新增课件（{data.docs.slides - data.docs.built}）
              </Button>
            )}
            <Button size="sm" variant="ghost" title="重新梳理全部课件" onClick={() => void build(true)}>
              <RefreshCw size={13} />
            </Button>
          </>
        )}
        <a href={`${API_BASE}/api/courses/${courseId}/knowledge/export`} download className="flex items-center gap-1 rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100">
          <Download size={13} /> 导出
        </a>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto bg-page">
          {view === "map" ? (
            <div className="h-full w-full">
              <ReactFlowProvider>
                <MindMap tree={tree} courseName={courseName} collapsed={col} onToggle={toggle} selectedId={selected?.id ?? null} onSelect={setSelected} examOnly={examOnly} />
              </ReactFlowProvider>
            </div>
          ) : (
            <Outline tree={tree} collapsed={col} onToggle={toggle} selectedId={selected?.id ?? null} onSelect={setSelected} examOnly={examOnly} query={query} />
          )}
        </div>

        {selected && (
          <aside className="scroll-thin w-[340px] shrink-0 overflow-y-auto border-l border-slate-200 bg-surface p-4">
            <div className="mb-2 flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10.5px]">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">{selected.level === 1 ? "课件" : selected.level === 2 ? "小节" : "知识点"}</span>
                  {selected.level === 3 && <span className="text-amber-500">{"★".repeat(selected.importance)}</span>}
                  {selected.exam_total > 0 && (
                    <span className="flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800">
                      <Flame size={10} /> 考过 {selected.exam_total} 次
                    </span>
                  )}
                </div>
                <h3 className="text-[15px] font-bold leading-snug text-slate-900">{selected.title}</h3>
              </div>
              <button className="rounded p-1 text-slate-400 hover:bg-slate-100" onClick={() => setSelected(null)}>
                <X size={16} />
              </button>
            </div>
            {selected.summary && <p className="text-[13px] leading-6 text-slate-600">{selected.summary}</p>}
            {selected.points.length > 0 && (
              <ul className="mt-3 space-y-1.5 text-[13px] leading-6 text-slate-700">
                {selected.points.map((p, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            )}
            {selected.refs.length > 0 && (
              <div className="mt-4">
                <div className="mb-1.5 text-xs font-semibold text-slate-500">原文位置</div>
                <div className="flex flex-wrap gap-1.5">
                  {selected.refs.slice(0, 12).map((r) => (
                    <button
                      key={`${r.document_id}-${r.page}`}
                      className="flex items-center gap-1 rounded-lg bg-indigo-50 px-2 py-1 text-xs text-indigo-700 hover:bg-indigo-100"
                      onClick={() => {
                        jumpTo({ document_id: r.document_id, page: r.page, quote: "", rects: [] });
                        onOpenReader();
                      }}
                    >
                      <BookOpen size={12} /> 第 {r.page} 页
                    </button>
                  ))}
                </div>
              </div>
            )}
            {(() => {
              const qs = relatedQuestions(selected);
              return qs.length > 0 ? (
                <div className="mt-4">
                  <div className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-amber-700">
                    <Flame size={12} /> 相关试题（{qs.length}）
                  </div>
                  <div className="space-y-2">
                    {qs.slice(0, 8).map((q) => (
                      <div key={q.id} className="rounded-lg border border-amber-200 bg-amber-50/60 p-2.5 text-xs leading-5 text-slate-600">
                        <div className="mb-0.5 flex flex-wrap items-center gap-1.5 font-medium text-slate-800">
                          {q.exam} · {q.number}
                          {q.qtype && <span className="rounded bg-white/70 px-1.5 text-[10.5px] font-normal">{q.qtype}</span>}
                          {q.ability && <span className="rounded bg-indigo-50 px-1.5 text-[10.5px] font-normal text-indigo-700">{q.ability}</span>}
                        </div>
                        <div className="line-clamp-3">{q.text}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null;
            })()}
            <div className="mt-5 flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => {
                  void ask({ question: `请系统讲解「${selected.title}」：核心思想、关键公式或结论、容易出错的地方，以及常见的考法。`, kind: "followup", parentIds: [] });
                  onOpenReader();
                }}
              >
                <MessageCircleQuestion size={13} /> 问 AI
              </Button>
              <Button size="sm" variant="outline" onClick={() => openCardDraft({ text: [selected.title, selected.summary, ...selected.points].filter(Boolean).join("\n"), source: "node", source_ref: String(selected.id) })}>
                <Layers size={13} /> 做成闪卡
              </Button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}

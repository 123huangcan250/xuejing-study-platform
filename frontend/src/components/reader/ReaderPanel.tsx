"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Check, Copy, GitBranch, Languages, Layers, Lightbulb, Loader2, NotebookPen, Send, ZoomIn, ZoomOut, X } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { useWorkspace } from "@/stores/workspace";
import { useTranslation } from "@/stores/translation";
import type { Anchor, ExamMark } from "@/lib/types";
import type { ReaderHighlight, ReaderSelection } from "./PdfReader";
import BlocksReader from "./BlocksReader";
import TextReader from "./TextReader";
import { TranslationBar } from "./TranslationBar";

const PdfReader = dynamic(() => import("./PdfReader"), {
  ssr: false,
  loading: () => <div className="p-10 text-center text-sm text-slate-400">加载阅读器…</div>,
});

export const EXPLAIN_PROMPT = "用通俗易懂的语言解释这段内容：先给一句话直觉，再分点讲清楚，最后举一个具体例子。";

type SelTrans = { state: "loading" } | { state: "done"; text: string; model: string } | { state: "error"; msg: string } | null;

const TYPE_ICON: Record<string, string> = { pdf: "📕", docx: "📘", pptx: "📙", md: "📝", txt: "📄" };

export function ReaderPanel({ onGoLibrary }: { onGoLibrary: () => void }) {
  const { docs, activeDocId, openDoc, jump, nodes, focusNode, askFromExcerpt, createNode, openCardDraft } = useWorkspace();
  const doc = docs.find((d) => d.id === activeDocId) ?? null;
  const [zoom, setZoom] = useState(1);
  const [sel, setSel] = useState<ReaderSelection | null>(null);
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");
  const [selTrans, setSelTrans] = useState<SelTrans>(null);
  const [copied, setCopied] = useState(false);
  const [page, setPage] = useState({ cur: 1, total: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const [marks, setMarks] = useState<Record<number, ExamMark[]>>({});

  // 翻译（整份文档级）
  const mode = useTranslation((s) => s.mode);
  const setMode = useTranslation((s) => s.setMode);
  const initTrans = useTranslation((s) => s.init);
  const tStatus = useTranslation((s) => (doc ? s.status[doc.id] : undefined));
  const tPages = useTranslation((s) => (doc ? s.pages[doc.id] : undefined));
  const blocked = useTranslation((s) => (doc ? (s.blocked[doc.id] ?? "") : ""));
  const { loadStatus, ensurePages, startAll, cancel, clear, retry } = useTranslation.getState();

  useEffect(() => initTrans(), [initTrans]);
  useEffect(() => {
    setSel(null);
    setAsking(false);
    setSelTrans(null);
    if (activeDocId) void loadStatus(activeDocId);
  }, [activeDocId, loadStatus]);

  // 课件：取回「这页是考点」的提示（来自试卷分析）
  const isSlides = doc?.kind === "slides";
  useEffect(() => {
    setMarks({});
    if (!activeDocId || !isSlides) return;
    api
      .get<{ pages: Record<string, ExamMark[]> }>(`/api/documents/${activeDocId}/exam-marks`)
      .then((r) => setMarks(Object.fromEntries(Object.entries(r.pages).map(([p, m]) => [Number(p), m]))))
      .catch(() => undefined);
  }, [activeDocId, isSlides]);

  const docReady = doc?.status === "ready";
  const onVisiblePages = useCallback(
    (pages: number[]) => {
      if (activeDocId && docReady) ensurePages(activeDocId, pages);
    },
    [activeDocId, docReady, ensurePages],
  );

  const highlights: ReaderHighlight[] = useMemo(
    () =>
      nodes
        .filter((n) => n.type === "excerpt" && n.data.anchor?.document_id === activeDocId && n.data.anchor?.rects?.length)
        .map((n) => ({ key: n.id, page: n.data.anchor!.page, rects: n.data.anchor!.rects, onClick: () => focusNode(Number(n.id)) })),
    [nodes, activeDocId, focusNode],
  );

  const anchor = (): Anchor | null => (sel && doc ? { document_id: doc.id, page: sel.page, quote: sel.quote, rects: sel.rects } : null);

  const close = () => {
    setSel(null);
    setAsking(false);
    setQuestion("");
    setSelTrans(null);
    window.getSelection()?.removeAllRanges();
  };

  const onSelect = (s: ReaderSelection | null) => {
    setSel(s);
    setAsking(false);
    setSelTrans(null);
  };

  const translateSelection = async () => {
    if (!sel || !doc) return;
    setSelTrans({ state: "loading" });
    try {
      const r = await api.post<{ translation: string; model: string }>("/api/translate/text", { text: sel.quote, context: doc.filename });
      setSelTrans({ state: "done", text: r.translation, model: r.model });
    } catch (e) {
      setSelTrans({ state: "error", msg: e instanceof ApiError ? e.message : "翻译失败" });
    }
  };

  const act = async (kind: "ask" | "explain" | "card" | "note" | "translate") => {
    const a = anchor();
    if (!a) return;
    if (kind === "translate") return translateSelection();
    if (kind === "ask") {
      setAsking(true);
      setTimeout(() => inputRef.current?.focus(), 0);
      return;
    }
    close();
    if (kind === "explain") await askFromExcerpt(a, EXPLAIN_PROMPT);
    if (kind === "card") openCardDraft({ text: a.quote, source: "highlight", source_ref: JSON.stringify({ document_id: a.document_id, page: a.page }) });
    if (kind === "note") {
      const ex = await createNode({ type: "excerpt", content: a.quote, anchor: a });
      if (ex) await createNode({ type: "note", title: "我的笔记", content: "", parent_ids: [ex.id] });
    }
  };

  const submitAsk = async () => {
    const a = anchor();
    const q = question.trim();
    if (!a || !q) return;
    close();
    await askFromExcerpt(a, q);
  };

  if (!docs.length) {
    return (
      <div className="grid h-full place-items-center bg-slate-50 text-center">
        <div>
          <BookOpen className="mx-auto mb-3 text-slate-300" size={40} />
          <p className="mb-3 text-sm text-slate-500">这个课程世界还没有资料</p>
          <button className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white" onClick={onGoLibrary}>
            去上传 PDF / Word / PPT / Markdown
          </button>
        </div>
      </div>
    );
  }

  const useBlocks = !!doc && (doc.filetype === "docx" || doc.filetype === "pptx" || ((doc.filetype === "md" || doc.filetype === "txt") && mode !== "orig"));

  return (
    <div className="relative flex h-full min-w-0 flex-col">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-slate-200 bg-surface px-3">
        <select
          className="min-w-0 flex-1 truncate rounded-md border border-slate-200 bg-surface px-2 py-1 text-sm"
          value={activeDocId ?? ""}
          onChange={(e) => openDoc(Number(e.target.value))}
        >
          {docs.map((d) => (
            <option key={d.id} value={d.id}>
              {TYPE_ICON[d.filetype] ?? "📄"} {d.filename}
              {d.status !== "ready" ? `（${d.status === "processing" ? "解析中" : "解析失败"}）` : ""}
            </option>
          ))}
        </select>
        {doc?.filetype === "pdf" && page.total > 0 && (
          <span className="shrink-0 text-xs tabular-nums text-slate-400">
            {page.cur} / {page.total}
          </span>
        )}
        <button className="rounded p-1 text-slate-500 hover:bg-slate-100" onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.1).toFixed(1)))} title="缩小">
          <ZoomOut size={16} />
        </button>
        <span className="w-9 text-center text-xs tabular-nums text-slate-400">{Math.round(zoom * 100)}%</span>
        <button className="rounded p-1 text-slate-500 hover:bg-slate-100" onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(1)))} title="放大">
          <ZoomIn size={16} />
        </button>
      </div>
      {doc && docReady && (
        <TranslationBar
          doc={doc}
          mode={mode}
          status={tStatus}
          blocked={blocked}
          onMode={setMode}
          onStartAll={() => void startAll(doc.id)}
          onCancel={() => void cancel(doc.id)}
          onClear={() => void clear(doc.id).then(() => toast("已清除译文"))}
        />
      )}
      <div className="min-h-0 flex-1">
        {doc && doc.filetype === "pdf" ? (
          <PdfReader
            key={doc.id}
            documentId={doc.id}
            zoom={zoom}
            highlights={highlights}
            jump={jump}
            onSelect={onSelect}
            onPageChange={(cur, total) => setPage({ cur, total })}
            mode={mode}
            translations={tPages}
            translateError={tStatus?.state === "error" ? tStatus.error : blocked}
            translateRunning={tStatus?.running}
            onRetryTranslate={() => retry(doc.id)}
            onVisiblePages={onVisiblePages}
            marks={marks}
          />
        ) : doc && useBlocks ? (
          <BlocksReader
            key={`${doc.id}-b`}
            documentId={doc.id}
            filetype={doc.filetype as "docx" | "pptx" | "md" | "txt"}
            zoom={zoom}
            mode={mode}
            pages={tPages}
            jump={jump}
            onSelect={onSelect}
            onVisiblePages={onVisiblePages}
            marks={marks}
          />
        ) : doc ? (
          <TextReader key={doc.id} documentId={doc.id} filetype={doc.filetype as "md" | "txt"} zoom={zoom} jump={jump} onSelect={onSelect} />
        ) : null}
      </div>

      {sel && (
        <div
          className="fixed z-50 -translate-x-1/2 rounded-xl border border-slate-200 bg-surface p-1 shadow-xl"
          style={{ left: Math.min(Math.max(sel.clientX, 250), window.innerWidth - 250), top: Math.min(sel.clientY + 8, window.innerHeight - 90) }}
          onMouseUp={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {!asking ? (
            <div className="flex items-center gap-0.5 text-sm">
              <ToolBtn icon={<Languages size={15} />} label="翻译" onClick={() => act("translate")} accent />
              <ToolBtn icon={<GitBranch size={15} />} label="分叉提问" onClick={() => act("ask")} />
              <ToolBtn icon={<Lightbulb size={15} />} label="通俗解释" onClick={() => act("explain")} />
              <ToolBtn icon={<Layers size={15} />} label="做成闪卡" onClick={() => act("card")} />
              <ToolBtn icon={<NotebookPen size={15} />} label="加入笔记" onClick={() => act("note")} />
              <button className="ml-0.5 rounded-md p-1.5 text-slate-400 hover:bg-slate-100" onClick={close}>
                <X size={14} />
              </button>
            </div>
          ) : (
            <form
              className="flex w-[380px] items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                void submitAsk();
              }}
            >
              <input
                ref={inputRef}
                autoFocus
                className="min-w-0 flex-1 rounded-lg bg-transparent px-2.5 py-1.5 text-sm outline-none"
                placeholder="针对这段原文，你想问什么？"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && close()}
              />
              <button type="submit" className="rounded-lg bg-indigo-600 p-2 text-white disabled:opacity-40" disabled={!question.trim()}>
                <Send size={14} />
              </button>
            </form>
          )}
          {selTrans && !asking && (
            <div className="mt-1 w-[min(92vw,460px)] border-t border-slate-100 px-3 py-2.5">
              {selTrans.state === "loading" && (
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 size={13} className="animate-spin" /> 翻译中…
                </div>
              )}
              {selTrans.state === "error" && (
                <div className="text-xs text-rose-600">
                  {selTrans.msg}{" "}
                  {selTrans.msg.includes("配置") && (
                    <Link href="/settings" className="underline">
                      去设置
                    </Link>
                  )}
                </div>
              )}
              {selTrans.state === "done" && (
                <>
                  <p className="scroll-thin max-h-52 select-text overflow-y-auto whitespace-pre-wrap font-read text-[13.5px] leading-7 text-slate-800">{selTrans.text}</p>
                  <div className="mt-2 flex items-center gap-1 text-[11px] text-slate-400">
                    <span className="truncate">{selTrans.model}</span>
                    <div className="flex-1" />
                    <button
                      className="flex items-center gap-1 rounded px-1.5 py-1 text-slate-500 hover:bg-slate-100"
                      onClick={() => {
                        void navigator.clipboard.writeText(selTrans.text).then(() => {
                          setCopied(true);
                          setTimeout(() => setCopied(false), 1200);
                        });
                      }}
                    >
                      {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "已复制" : "复制"}
                    </button>
                    <button
                      className="flex items-center gap-1 rounded px-1.5 py-1 text-slate-500 hover:bg-slate-100"
                      onClick={() => {
                        const a = anchor();
                        if (!a) return;
                        openCardDraft({ text: a.quote, context: `参考译文：${selTrans.text}`, source: "highlight", source_ref: JSON.stringify({ document_id: a.document_id, page: a.page }) });
                        close();
                      }}
                    >
                      <Layers size={12} /> 做成闪卡
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ToolBtn({ icon, label, onClick, accent }: { icon: React.ReactNode; label: string; onClick: () => void; accent?: boolean }) {
  return (
    <button
      className={`flex items-center gap-1 rounded-lg px-2.5 py-1.5 hover:bg-indigo-50 hover:text-indigo-700 ${accent ? "font-medium text-indigo-700" : "text-slate-700"}`}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}

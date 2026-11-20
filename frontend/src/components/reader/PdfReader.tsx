"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import { fileUrl } from "@/lib/api";
import type { Anchor, ExamMark, NRect, TBlock, TMode } from "@/lib/types";
import { ExamMarks } from "./ExamMarks";
import { TransCard } from "./TransCard";

// worker 必须在使用 react-pdf 的同一模块里设置；文件由 scripts/copy-pdf-worker.mjs 复制到 public/
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
// 中文 PDF 常用 CID 字体，需要 CMap 才能正确渲染和提取文本层
const PDF_OPTIONS = { cMapUrl: "/cmaps/", cMapPacked: true, standardFontDataUrl: "/standard_fonts/" };

export interface ReaderSelection {
  quote: string;
  page: number;
  rects: NRect[];
  clientX: number;
  clientY: number;
}

export interface ReaderHighlight {
  key: string;
  page: number;
  rects: NRect[];
  onClick?: () => void;
}

interface Props {
  documentId: number;
  zoom: number;
  highlights: ReaderHighlight[];
  jump: { anchor: Anchor; nonce: number } | null;
  onSelect: (s: ReaderSelection | null) => void;
  onPageChange?: (page: number, total: number) => void;
  /** 翻译：原文 / 对照（每页下方显示译文）/ 仅译文 */
  mode?: TMode;
  translations?: Record<number, TBlock[]>;
  translateError?: string;
  translateRunning?: boolean;
  onRetryTranslate?: () => void;
  onVisiblePages?: (pages: number[]) => void;
  /** 课件的考点提示：页码 → 这页对应的试题 */
  marks?: Record<number, ExamMark[]>;
}

export default function PdfReader({
  documentId,
  zoom,
  highlights,
  jump,
  onSelect,
  onPageChange,
  mode = "orig",
  translations,
  translateError,
  translateRunning,
  onRetryTranslate,
  onVisiblePages,
  marks,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [numPages, setNumPages] = useState(0);
  const [ratio, setRatio] = useState(1.414);
  const [visible, setVisible] = useState<Set<number>>(new Set([1, 2]));
  const [active, setActive] = useState<{ page: number; rects: NRect[]; nonce: number } | null>(null);
  const pendingJump = useRef<{ anchor: Anchor; nonce: number } | null>(null);
  const showPage = mode !== "trans";

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(280, el.clientWidth - 32)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pageW = Math.round(width * zoom);
  const pageH = Math.round(pageW * ratio);

  // 懒渲染：只渲染视口附近的页；同时把“哪些页在视口附近”告诉翻译（优先翻这些页）
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !numPages) return;
    const io = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const p = Number((e.target as HTMLElement).dataset.pgwrap);
            if (e.isIntersecting) next.add(p);
            else next.delete(p);
          }
          return next;
        });
      },
      { root, rootMargin: "900px 0px" },
    );
    root.querySelectorAll<HTMLElement>("[data-pgwrap]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [numPages, pageH, mode]);

  useEffect(() => {
    if (mode !== "orig") onVisiblePages?.([...visible].filter((p) => p >= 1 && p <= numPages).sort((a, b) => a - b));
  }, [visible, mode, numPages, onVisiblePages]);

  const doJump = useCallback(
    (j: { anchor: Anchor; nonce: number }) => {
      const root = scrollRef.current;
      const el = root?.querySelector<HTMLElement>(`[data-pgwrap="${j.anchor.page}"]`);
      if (!root || !el) return false;
      const y = showPage ? (j.anchor.rects?.[0]?.y ?? 0) : 0;
      root.scrollTo({ top: el.offsetTop + y * pageH - 80, behavior: "smooth" });
      setActive({ page: j.anchor.page, rects: j.anchor.rects ?? [], nonce: j.nonce });
      return true;
    },
    [pageH, showPage],
  );

  useEffect(() => {
    if (!jump || jump.anchor.document_id !== documentId) return;
    if (!numPages || !doJump(jump)) pendingJump.current = jump;
  }, [jump, documentId, numPages, doJump]);

  const handleScroll = () => {
    const root = scrollRef.current;
    if (!root || !numPages) return;
    // 每页高度不一定相同（对照模式下页后面还有译文），按元素位置找当前页
    const mid = root.scrollTop + root.clientHeight / 3;
    let cur = 1;
    for (const el of root.querySelectorAll<HTMLElement>("[data-pgwrap]")) {
      if (el.offsetTop <= mid) cur = Number(el.dataset.pgwrap);
      else break;
    }
    onPageChange?.(cur, numPages);
  };

  const handleMouseUp = () => {
    const sel = window.getSelection();
    const quote = sel?.toString().replace(/\s+\n/g, "\n").trim() ?? "";
    if (!sel || !quote || sel.rangeCount === 0) {
      onSelect(null);
      return;
    }
    const range = sel.getRangeAt(0);
    const startEl = (range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement) as HTMLElement | null;
    const pageEl = startEl?.closest<HTMLElement>("[data-page]");
    const inTrans = !!startEl?.closest("[data-trans]");
    if (!scrollRef.current?.contains(startEl)) {
      onSelect(null);
      return;
    }
    const last = range.getBoundingClientRect();
    if (!pageEl || inTrans) {
      // 在译文里划词：没有原文坐标，只带页码（可翻译、可问、可做卡片，但回跳时只能定位到页）
      const wrap = startEl?.closest<HTMLElement>("[data-pgwrap]");
      onSelect({ quote: quote.slice(0, 4000), page: Number(wrap?.dataset.pgwrap ?? 1), rects: [], clientX: last.left + last.width / 2, clientY: last.bottom });
      return;
    }
    const box = pageEl.getBoundingClientRect();
    const rects = Array.from(range.getClientRects())
      .filter((r) => r.width > 1 && r.height > 1 && r.top >= box.top - 2 && r.bottom <= box.bottom + 2)
      .map((r) => ({
        x: (r.left - box.left) / box.width,
        y: (r.top - box.top) / box.height,
        w: r.width / box.width,
        h: r.height / box.height,
      }));
    onSelect({
      quote: quote.slice(0, 4000),
      page: Number(pageEl.dataset.page),
      rects,
      clientX: last.left + last.width / 2,
      clientY: last.bottom,
    });
  };

  return (
    <div ref={scrollRef} className="scroll-thin relative h-full overflow-auto bg-slate-100 px-4 py-4" onScroll={handleScroll} onMouseUp={handleMouseUp}>
      <Document
        file={fileUrl(documentId)}
        options={PDF_OPTIONS}
        loading={<div className="p-10 text-center text-sm text-slate-400">正在加载 PDF…</div>}
        error={<div className="p-10 text-center text-sm text-rose-500">PDF 加载失败</div>}
        onLoadSuccess={async (pdf) => {
          setNumPages(pdf.numPages);
          try {
            const p1 = await pdf.getPage(1);
            const vp = p1.getViewport({ scale: 1 });
            setRatio(vp.height / vp.width);
          } catch {
            /* 用默认 A4 比例 */
          }
          onPageChange?.(1, pdf.numPages);
          if (pendingJump.current) {
            const j = pendingJump.current;
            pendingJump.current = null;
            setTimeout(() => doJump(j), 150);
          }
        }}
      >
        {Array.from({ length: numPages }, (_, i) => i + 1).map((p) => (
          <div key={p} data-pgwrap={p}>
            {marks?.[p] && <ExamMarks marks={marks[p]} width={showPage ? pageW : Math.min(pageW, 720)} />}
            {showPage && (
              <div data-page={p} className="pdf-page" style={{ width: pageW, height: pageH }}>
                {visible.has(p) && <Page pageNumber={p} width={pageW} renderAnnotationLayer={false} renderTextLayer loading={null} />}
                {highlights
                  .filter((h) => h.page === p)
                  .flatMap((h) => [
                    // 高亮本身不拦截鼠标，保证还能在其上继续划词；左侧小图钉负责定位画布节点
                    ...h.rects.map((r, i) => (
                      <div
                        key={`${h.key}-${i}`}
                        className="hl-rect"
                        style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%`, pointerEvents: "none" }}
                      />
                    )),
                    h.rects[0] && (
                      <button
                        key={`${h.key}-pin`}
                        className="absolute z-10 grid h-4 w-4 -translate-x-full place-items-center rounded-full bg-amber-400 text-[9px] text-white shadow hover:bg-orange-500"
                        title="定位到画布上的摘录卡"
                        style={{ left: `calc(${h.rects[0].x * 100}% - 3px)`, top: `${h.rects[0].y * 100}%` }}
                        onMouseUp={(e) => e.stopPropagation()}
                        onClick={h.onClick}
                      >
                        ◆
                      </button>
                    ),
                  ])}
                {active?.page === p &&
                  active.rects.map((r, i) => (
                    <div
                      key={`active-${active.nonce}-${i}`}
                      className="hl-rect active"
                      style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%`, pointerEvents: "none" }}
                    />
                  ))}
                <span className="absolute bottom-1 right-2 z-10 text-[10px] text-slate-300">{p}</span>
              </div>
            )}
            {mode !== "orig" && (
              <div>
                {!showPage && <div className="mb-1.5 text-center text-[11px] text-slate-400">第 {p} 页</div>}
                <TransCard
                  blocks={translations?.[p]}
                  error={translateError}
                  running={translateRunning}
                  onRetry={onRetryTranslate}
                  width={showPage ? pageW : Math.min(pageW, 720)}
                />
              </div>
            )}
          </div>
        ))}
      </Document>
    </div>
  );
}

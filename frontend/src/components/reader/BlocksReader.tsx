"use client";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { Anchor, DocBlockInfo, ExamMark, TBlock, TMode } from "@/lib/types";
import { ExamMarks } from "./ExamMarks";
import type { ReaderSelection } from "./PdfReader";

interface Props {
  documentId: number;
  filetype: "docx" | "pptx" | "md" | "txt";
  zoom: number;
  mode: TMode;
  pages: Record<number, TBlock[]> | undefined;
  jump: { anchor: Anchor; nonce: number } | null;
  onSelect: (s: ReaderSelection | null) => void;
  onVisiblePages: (pages: number[]) => void;
  marks?: Record<number, ExamMark[]>;
}

const norm = (s: string) => s.replace(/\s+/g, "");

/** DOCX / PPTX（以及翻译模式下的 Markdown / TXT）的阅读器：按文字块渲染，可显示原文、译文或对照。 */
export default function BlocksReader({ documentId, filetype, zoom, mode, pages, jump, onSelect, onVisiblePages, marks }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [blocks, setBlocks] = useState<DocBlockInfo[] | null>(null);
  const [visible, setVisible] = useState<Set<number>>(new Set([1, 2]));

  useEffect(() => {
    setBlocks(null);
    api.get<DocBlockInfo[]>(`/api/documents/${documentId}/blocks`).then(setBlocks).catch(() => setBlocks([]));
  }, [documentId]);

  const byPage = useMemo(() => {
    const m = new Map<number, DocBlockInfo[]>();
    for (const b of blocks ?? []) {
      const list = m.get(b.page) ?? [];
      list.push(b);
      m.set(b.page, list);
    }
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  }, [blocks]);

  // 译文与是否需要翻译，按块 id 查找
  const info = useMemo(() => {
    const m = new Map<number, TBlock>();
    for (const list of Object.values(pages ?? {})) for (const b of list) m.set(b.id, b);
    return m;
  }, [pages]);

  // 哪些页在视口附近：只为这些页取译文 / 触发翻译
  useEffect(() => {
    const root = rootRef.current;
    if (!root || !byPage.length) return;
    const io = new IntersectionObserver(
      (entries) =>
        setVisible((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const p = Number((e.target as HTMLElement).dataset.page);
            if (e.isIntersecting) next.add(p);
            else next.delete(p);
          }
          return next;
        }),
      { root: root.parentElement, rootMargin: "700px 0px" },
    );
    root.querySelectorAll<HTMLElement>("[data-page]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [byPage]);

  useEffect(() => {
    if (mode !== "orig") onVisiblePages([...visible].sort((a, b) => a - b));
  }, [visible, mode, onVisiblePages]);

  // 从引用角标 / 检索结果跳回来：优先按原文片段，其次按页（幻灯片）
  useEffect(() => {
    if (!jump || jump.anchor.document_id !== documentId || !blocks || !rootRef.current) return;
    const root = rootRef.current;
    let el: HTMLElement | null = null;
    const q = norm(jump.anchor.quote).slice(0, 24);
    if (q) {
      const hit = blocks.find((b) => norm(b.text).includes(q));
      if (hit) el = root.querySelector<HTMLElement>(`[data-block="${hit.id}"]`);
    }
    el ??= root.querySelector<HTMLElement>(`[data-page="${jump.anchor.page}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("md-flash");
      void el.offsetWidth;
      el.classList.add("md-flash");
    }
  }, [jump, documentId, blocks]);

  const handleMouseUp = () => {
    const sel = window.getSelection();
    const quote = sel?.toString().trim() ?? "";
    if (!sel || !quote || sel.rangeCount === 0 || !rootRef.current?.contains(sel.anchorNode)) {
      onSelect(null);
      return;
    }
    const start = (sel.anchorNode?.nodeType === 1 ? sel.anchorNode : sel.anchorNode?.parentElement) as HTMLElement | null;
    const page = Number(start?.closest<HTMLElement>("[data-page]")?.dataset.page ?? 1);
    const r = sel.getRangeAt(0).getBoundingClientRect();
    onSelect({ quote: quote.slice(0, 4000), page, rects: [], clientX: r.left + r.width / 2, clientY: r.bottom });
  };

  const renderBlock = (b: DocBlockInfo) => {
    const t = info.get(b.id);
    const translatable = t?.translatable ?? false;
    const showOrig = mode !== "trans" || !translatable || !t?.translation;
    const heading = b.kind === "title" || b.kind.startsWith("h");
    const cls = clsx(
      "leading-7",
      b.kind === "title" && "text-xl font-bold text-slate-900",
      b.kind === "h1" && "text-lg font-bold text-slate-900",
      (b.kind === "h2" || b.kind === "h3") && "text-base font-semibold text-slate-800",
      b.kind === "note" && "italic text-slate-500",
      b.kind === "code" && "whitespace-pre-wrap rounded bg-slate-100 px-2 py-1 font-mono text-[12.5px]",
      !heading && b.kind !== "code" && "text-[14px] text-slate-700",
      b.kind === "li" && "pl-4",
    );
    return (
      <div key={b.id} data-block={b.id} className="space-y-1">
        {showOrig && <div className={cls}>{b.text}</div>}
        {mode !== "orig" && translatable && (
          <div
            data-trans
            className={clsx(
              "whitespace-pre-wrap font-read leading-7",
              heading ? "text-[15px] font-semibold" : "text-[13.5px]",
              t?.translation
                ? mode === "both"
                  ? "rounded-md border-l-2 border-indigo-300 bg-indigo-50/60 px-2.5 py-1 text-slate-800"
                  : "text-slate-800"
                : "h-4 w-1/2 animate-pulse rounded bg-indigo-100/80",
            )}
          >
            {t?.translation ?? ""}
          </div>
        )}
      </div>
    );
  };

  /** 把连续的表格单元格渲染成真正的表格 */
  const renderPage = (list: DocBlockInfo[]) => {
    const out: React.ReactNode[] = [];
    for (let i = 0; i < list.length; ) {
      const b = list[i];
      if (b.kind !== "cell" || !b.table) {
        out.push(renderBlock(b));
        i++;
        continue;
      }
      const tid = b.table[0];
      const cells: DocBlockInfo[] = [];
      while (i < list.length && list[i].kind === "cell" && list[i].table?.[0] === tid) cells.push(list[i++]);
      const rows = new Map<number, Map<number, DocBlockInfo[]>>();
      for (const c of cells) {
        const [, r, col] = c.table!;
        const row = rows.get(r) ?? new Map<number, DocBlockInfo[]>();
        const cell = row.get(col) ?? [];
        cell.push(c);
        row.set(col, cell);
        rows.set(r, row);
      }
      out.push(
        <div key={`t${b.id}`} className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <tbody>
              {[...rows.entries()]
                .sort((x, y) => x[0] - y[0])
                .map(([r, row]) => (
                  <tr key={r}>
                    {[...row.entries()]
                      .sort((x, y) => x[0] - y[0])
                      .map(([c, items]) => (
                        <td key={c} className="border border-slate-200 px-2 py-1.5 align-top">
                          <div className="space-y-1">
                            {items.map((x) => (
                              <Fragment key={x.id}>{renderBlock(x)}</Fragment>
                            ))}
                          </div>
                        </td>
                      ))}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>,
      );
    }
    return out;
  };

  const slides = filetype === "pptx";
  return (
    <div className="scroll-thin h-full overflow-auto bg-slate-100 p-4" onMouseUp={handleMouseUp}>
      <div ref={rootRef} className="mx-auto max-w-3xl space-y-4" style={{ fontSize: `${zoom}em` }}>
        {blocks === null && <div className="p-10 text-center text-sm text-slate-400">加载中…</div>}
        {blocks?.length === 0 && <div className="p-10 text-center text-sm text-slate-400">没有解析出文字内容</div>}
        {slides
          ? byPage.map(([p, list]) => (
              <section key={p} data-page={p} className="rounded-xl bg-surface p-5 shadow-sm">
                <div className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-400">第 {p} 张幻灯片</div>
                {marks?.[p] && <ExamMarks marks={marks[p]} />}
                <div className="space-y-2.5">{renderPage(list)}</div>
              </section>
            ))
          : byPage.length > 0 && (
              <div className="rounded-xl bg-surface p-7 shadow-sm">
                {byPage.map(([p, list]) => (
                  <section key={p} data-page={p} className="space-y-3 pb-3">
                    {marks?.[p] && <ExamMarks marks={marks[p]} />}
                    {renderPage(list)}
                  </section>
                ))}
              </div>
            )}
      </div>
    </div>
  );
}

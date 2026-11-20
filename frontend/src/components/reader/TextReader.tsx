"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Anchor } from "@/lib/types";
import { Markdown } from "@/components/Markdown";
import type { ReaderSelection } from "./PdfReader";

interface Props {
  documentId: number;
  filetype: "md" | "txt";
  zoom: number;
  jump: { anchor: Anchor; nonce: number } | null;
  onSelect: (s: ReaderSelection | null) => void;
}

const norm = (s: string) => s.replace(/\s+/g, "");

/** Markdown / TXT 阅读器。锚点只记录 quote，回跳时在文本节点里查找并闪烁定位。 */
export default function TextReader({ documentId, filetype, zoom, jump, onSelect }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    setText(null);
    api
      .get<{ text: string }>(`/api/documents/${documentId}/text`)
      .then((r) => setText(r.text))
      .catch(() => setText("（加载失败）"));
  }, [documentId]);

  useEffect(() => {
    if (!jump || jump.anchor.document_id !== documentId || text === null || !ref.current) return;
    const target = norm(jump.anchor.quote).slice(0, 24);
    if (!target) return;
    const walker = document.createTreeWalker(ref.current, NodeFilter.SHOW_ELEMENT);
    let best: HTMLElement | null = null;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n as HTMLElement;
      if (norm(el.textContent ?? "").includes(target)) best = el; // 不断向内找到最深的包含元素
    }
    if (best) {
      best.scrollIntoView({ behavior: "smooth", block: "center" });
      best.classList.remove("md-flash");
      void best.offsetWidth;
      best.classList.add("md-flash");
    }
  }, [jump, documentId, text]);

  const handleMouseUp = () => {
    const sel = window.getSelection();
    const quote = sel?.toString().trim() ?? "";
    if (!sel || !quote || sel.rangeCount === 0 || !ref.current?.contains(sel.anchorNode)) {
      onSelect(null);
      return;
    }
    const r = sel.getRangeAt(0).getBoundingClientRect();
    onSelect({ quote: quote.slice(0, 4000), page: 1, rects: [], clientX: r.left + r.width / 2, clientY: r.bottom });
  };

  return (
    <div className="scroll-thin h-full overflow-auto bg-slate-100 p-4" onMouseUp={handleMouseUp}>
      <div ref={ref} className="mx-auto max-w-3xl rounded-xl bg-surface p-8 shadow-sm" style={{ fontSize: `${zoom}em` }}>
        {text === null ? (
          <div className="text-sm text-slate-400">加载中…</div>
        ) : filetype === "md" ? (
          <Markdown text={text} />
        ) : (
          <pre className="whitespace-pre-wrap font-sans text-sm leading-7 text-slate-700">{text}</pre>
        )}
      </div>
    </div>
  );
}

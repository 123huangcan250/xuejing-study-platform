"use client";
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

interface Props {
  text: string;
  onTerm?: (term: string, el: HTMLElement) => void;
  onCite?: (n: number) => void;
  className?: string;
}

/** 预处理：[[术语]] → 可点击术语；[1] → 引用角标；\( \) \[ \] → $ 定界符。 */
function prepare(text: string, terms: boolean, cites: boolean): string {
  let t = text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, m) => `$$${m}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, m) => `$${m}$`);
  // 代码块里的内容不做替换
  const parts = t.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  t = parts
    .map((p, i) => {
      if (i % 2 === 1) return p;
      if (terms) p = p.replace(/\[\[([^\[\]\n]{1,40})\]\]/g, (_, w) => `[${w}](#term:${encodeURIComponent(w)})`);
      else p = p.replace(/\[\[([^\[\]\n]{1,40})\]\]/g, "**$1**");
      if (cites) p = p.replace(/\[(\d{1,2})\](?![(\]])/g, (_, n) => `[${n}](#cite:${n})`);
      return p;
    })
    .join("");
  return t;
}

function MarkdownImpl({ text, onTerm, onCite, className }: Props) {
  return (
    <div className={`md ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#term:")) {
              const term = decodeURIComponent(href.slice(6));
              return (
                <button
                  type="button"
                  className="term-chip nodrag"
                  onClick={(e) => {
                    e.stopPropagation();
                    onTerm?.(term, e.currentTarget);
                  }}
                >
                  {children}
                </button>
              );
            }
            if (href?.startsWith("#cite:")) {
              const n = Number(href.slice(6));
              return (
                <button
                  type="button"
                  className="cite-badge nodrag"
                  title="跳转到原文"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCite?.(n);
                  }}
                >
                  {n}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {prepare(text, !!onTerm, !!onCite)}
      </ReactMarkdown>
    </div>
  );
}

export const Markdown = memo(MarkdownImpl);

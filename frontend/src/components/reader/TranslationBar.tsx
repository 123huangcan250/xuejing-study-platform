"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { ChevronDown, Download, Languages, Loader2, Trash2, X } from "lucide-react";
import { API_BASE } from "@/lib/api";
import type { Doc, TMode, TStatus } from "@/lib/types";

const MODES: { id: TMode; name: string; tip: string }[] = [
  { id: "orig", name: "原文", tip: "只显示英文原文" },
  { id: "both", name: "对照", tip: "原文 + 中文译文" },
  { id: "trans", name: "译文", tip: "只显示中文译文" },
];

interface Props {
  doc: Doc;
  mode: TMode;
  status: TStatus | undefined;
  blocked: string;
  onMode: (m: TMode) => void;
  onStartAll: () => void;
  onCancel: () => void;
  onClear: () => void;
}

/** 阅读器上方的翻译工具栏：切换原文 / 对照 / 译文，翻译整份，导出译文。 */
export function TranslationBar({ doc, mode, status, blocked, onMode, onStartAll, onCancel, onClear }: Props) {
  const [menu, setMenu] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const down = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setMenu(false);
    document.addEventListener("mousedown", down);
    return () => document.removeEventListener("mousedown", down);
  }, [menu]);

  const st = status;
  const total = st?.translatable ?? 0;
  const done = st?.translated ?? 0;
  const running = !!st?.running;
  const all = total > 0 && done >= total;
  const exportUrl = (mode: string, format = "native") => `${API_BASE}/api/documents/${doc.id}/translation/export?mode=${mode}&format=${format}`;

  const items: { label: string; hint: string; href: string }[] =
    doc.filetype === "pdf"
      ? [
          { label: "中文版 PDF（原页面上覆盖中文）", hint: "保持原版式，尽力而为", href: exportUrl("zh") },
          { label: "中英对照 Markdown", hint: "逐页原文 + 译文，便于复习", href: exportUrl("zh", "md") },
        ]
      : doc.filetype === "docx" || doc.filetype === "pptx"
        ? [
            { label: `中文版 ${doc.filetype.toUpperCase()}（替换原文）`, hint: "保留原有格式", href: exportUrl("zh") },
            { label: `中英对照 ${doc.filetype.toUpperCase()}`, hint: "原文下方附上译文", href: exportUrl("bilingual") },
            { label: "中英对照 Markdown", hint: "纯文本，便于复习", href: exportUrl("zh", "md") },
          ]
        : [{ label: "中英对照 Markdown", hint: "逐段原文 + 译文", href: exportUrl("zh", "md") }];

  return (
    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-slate-200 bg-surface px-3 text-xs">
      <Languages size={15} className="shrink-0 text-indigo-500" />
      <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
        {MODES.map((m) => (
          <button
            key={m.id}
            title={m.tip}
            onClick={() => onMode(m.id)}
            className={clsx("rounded-md px-2.5 py-1 transition", mode === m.id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-700")}
          >
            {m.name}
          </button>
        ))}
      </div>

      <div className="min-w-0 flex-1 truncate text-slate-500" title={st?.chain?.length ? `将依次使用：${st.chain.join(" → ")}` : undefined}>
        {blocked ? (
          <span className="text-rose-600">
            {blocked}{" "}
            {blocked.includes("配置") && (
              <Link href="/settings" className="underline">
                去设置
              </Link>
            )}
          </span>
        ) : running ? (
          <span className="inline-flex items-center gap-1.5 text-indigo-600">
            <Loader2 size={12} className="animate-spin" /> 翻译中 {done}/{total}
            {st?.model ? <span className="hidden text-slate-400 xl:inline">· {st.model}</span> : null}
          </span>
        ) : st?.state === "error" && st.error ? (
          <span className="text-rose-600" title={st.error}>
            翻译中断：{st.error}
          </span>
        ) : total === 0 && st ? (
          <span>没有需要翻译的英文内容</span>
        ) : all ? (
          <span className="text-emerald-600">✓ 已全部翻译（{total} 块）</span>
        ) : st ? (
          <span>
            已译 {done}/{total}
          </span>
        ) : null}
      </div>

      {running ? (
        <button className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={onCancel}>
          <X size={12} /> 取消
        </button>
      ) : (
        !all &&
        total > 0 && (
          <button className="shrink-0 rounded-md bg-indigo-50 px-2.5 py-1 font-medium text-indigo-700 hover:bg-indigo-100" onClick={onStartAll}>
            {st?.state === "error" ? "重试" : "翻译全文"}
          </button>
        )
      )}

      <div ref={box} className="relative shrink-0">
        <button
          onClick={() => setMenu((v) => !v)}
          disabled={done === 0}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          title={done === 0 ? "先翻译一些内容再导出" : "导出译文"}
        >
          <Download size={13} /> 导出 <ChevronDown size={11} />
        </button>
        {menu && (
          <div className="absolute right-0 top-full z-50 mt-1 w-64 overflow-hidden rounded-xl border border-slate-200 bg-surface py-1 shadow-xl">
            {!all && total > 0 && <div className="px-3 py-1.5 text-[11px] text-amber-600">目前只翻译了 {done}/{total} 块，未翻译的部分将保持原文</div>}
            {items.map((it) => (
              <a key={it.label} href={it.href} download onClick={() => setMenu(false)} className="block px-3 py-2 hover:bg-slate-50">
                <div className="text-[12.5px] text-slate-700">{it.label}</div>
                <div className="text-[10.5px] text-slate-400">{it.hint}</div>
              </a>
            ))}
            <button
              className="flex w-full items-center gap-1.5 border-t border-slate-100 px-3 py-2 text-left text-[12px] text-rose-600 hover:bg-rose-50"
              onClick={() => {
                setMenu(false);
                if (confirm("清除这份资料的全部译文？之后需要重新翻译（会再次调用模型）。")) onClear();
              }}
            >
              <Trash2 size={12} /> 清除译文
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

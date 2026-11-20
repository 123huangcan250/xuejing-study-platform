"use client";
import { Loader2, RefreshCw } from "lucide-react";
import clsx from "clsx";
import type { TBlock } from "@/lib/types";

/** 某一页的中文译文。PDF 的“对照 / 译文”模式用它显示每页的翻译。 */
export function TransCard({
  blocks,
  error,
  running,
  onRetry,
  width,
}: {
  blocks: TBlock[] | undefined;
  error?: string;
  running?: boolean;
  onRetry?: () => void;
  width?: number;
}) {
  const shown = (blocks ?? []).filter((b) => b.translatable);
  const pending = shown.filter((b) => !b.translation);
  return (
    <div
      data-trans
      className="mx-auto mb-4 rounded-xl border border-indigo-200/70 bg-indigo-50/60 px-4 py-3 font-read shadow-sm"
      style={width ? { width } : undefined}
    >
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-indigo-700">
        中文译文
        {pending.length > 0 && !error && (
          <span className="flex items-center gap-1 font-normal text-slate-400">
            <Loader2 size={11} className="animate-spin" /> 翻译中…
          </span>
        )}
      </div>
      {blocks === undefined ? (
        <div className="text-xs text-slate-400">加载中…</div>
      ) : shown.length === 0 ? (
        <div className="text-xs text-slate-400">这一页没有需要翻译的英文内容</div>
      ) : (
        <div className="space-y-1.5">
          {shown.map((b) =>
            b.translation ? (
              <p
                key={b.id}
                className={clsx(
                  "whitespace-pre-wrap leading-7 text-slate-800 selection:bg-indigo-200/60",
                  b.kind === "title" || b.kind.startsWith("h") ? "text-[15px] font-semibold" : "text-[13.5px]",
                )}
              >
                {b.translation}
              </p>
            ) : (
              <div key={b.id} className="h-4 animate-pulse rounded bg-indigo-100/80" style={{ width: `${40 + (b.id % 5) * 12}%` }} />
            ),
          )}
        </div>
      )}
      {error && pending.length > 0 && !running && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-rose-50 px-2.5 py-1.5 text-xs text-rose-700">
          <span className="flex-1">{error}</span>
          {onRetry && (
            <button className="flex shrink-0 items-center gap-1 text-rose-700 underline" onClick={onRetry}>
              <RefreshCw size={11} /> 重试
            </button>
          )}
        </div>
      )}
    </div>
  );
}

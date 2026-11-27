"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, ScanText } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Doc } from "@/lib/types";
import { Button } from "@/components/ui";

interface OcrStatus {
  pending: number;
  recognized: number;
  vision: { ok: boolean; reason: string; model?: string };
  job: { state: string; total: number; done: number; failed: number; error: string } | null;
}

/** 扫描版 PDF：没有文字层的页交给视觉模型识别（公式转 LaTeX）。 */
export function OcrControl({ doc, onChanged }: { doc: Doc; onChanged: () => void }) {
  const [st, setSt] = useState<OcrStatus | null>(null);
  const hasEmpty = doc.filetype === "pdf" && doc.status === "ready" && !!doc.empty_pages;

  const load = useCallback(async () => {
    try {
      setSt(await api.get<OcrStatus>(`/api/documents/${doc.id}/ocr`));
    } catch {
      /* 状态取不到就不显示 */
    }
  }, [doc.id]);

  useEffect(() => {
    if (hasEmpty || st?.job?.state === "running") void load();
  }, [hasEmpty, load, doc.empty_pages]); // eslint-disable-line react-hooks/exhaustive-deps

  const running = st?.job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), 1500);
    return () => clearInterval(t);
  }, [running, load]);

  // 任务结束：刷新资料（页数、分块数会变）并提示
  const state = st?.job?.state;
  useEffect(() => {
    if (state === "done" || state === "error" || state === "cancelled") {
      onChanged();
      if (state === "done") toast.success(`${doc.filename}：文字识别完成`);
    }
  }, [state]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!hasEmpty && !running) return null;
  if (!st) return null;

  const start = async () => {
    const pages = st.pending;
    if (!confirm(`将把「${doc.filename}」没有文字的 ${pages} 页渲染成图片，发给视觉模型（${st.vision.model ?? ""}）识别，会产生 token 费用。继续？`)) return;
    try {
      setSt(await api.post<OcrStatus>(`/api/documents/${doc.id}/ocr`, {}));
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (running && st.job) {
    return (
      <div className="mt-1.5 flex items-center gap-2 text-xs text-indigo-600">
        <Loader2 size={12} className="animate-spin" />
        正在识别 {st.job.done}/{st.job.total} 页…
        <button className="text-slate-400 underline" onClick={async () => setSt(await api.post<OcrStatus>(`/api/documents/${doc.id}/ocr/cancel`, {}))}>
          停止
        </button>
      </div>
    );
  }
  return (
    <div className="mt-1.5 space-y-1 text-xs">
      <div className="flex flex-wrap items-center gap-2 text-amber-700">
        <ScanText size={12} />
        {st.pending > 0 ? `${st.pending} 页没有文字层（扫描件）` : "所有扫描页都已识别"}
        {st.pending > 0 && st.vision.ok && (
          <Button size="sm" variant="soft" onClick={start}>
            识别文字
          </Button>
        )}
        {st.recognized > 0 && <span className="text-slate-400">已识别 {st.recognized} 页</span>}
      </div>
      {st.pending > 0 && !st.vision.ok && (
        <div className="text-slate-500">
          {st.vision.reason}{" "}
          <Link href="/settings" className="text-indigo-600 underline">
            去设置
          </Link>
        </div>
      )}
      {st.job?.error && <div className="text-rose-500">{st.job.error}</div>}
    </div>
  );
}

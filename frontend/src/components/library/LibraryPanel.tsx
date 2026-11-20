"use client";
import { useEffect, useRef, useState } from "react";
import { BookOpen, FileUp, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Doc, DocKind, Reward } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { useProfile } from "@/stores/profile";
import { Button, Card } from "@/components/ui";
import { CloudPanel } from "./CloudPanel";

const KINDS: { id: DocKind; name: string; hint: string }[] = [
  { id: "slides", name: "课件", hint: "用于知识梳理和思维导图（课件、讲义、教材章节）" },
  { id: "exam", name: "试卷", hint: "往年试卷、模拟题：识别考点，并对应到课件" },
  { id: "reference", name: "参考资料", hint: "可检索、可翻译，但不参与知识梳理" },
];

export function LibraryPanel({ courseId, onOpen }: { courseId: number; onOpen: () => void }) {
  const { docs, refreshDocs, openDoc } = useWorkspace();
  const [kind, setKind] = useState<DocKind>("slides");
  const [uploading, setUploading] = useState(0);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 有资料在解析中时轮询状态
  useEffect(() => {
    if (!docs.some((d) => d.status === "processing")) return;
    const t = setInterval(() => void refreshDocs(), 1500);
    return () => clearInterval(t);
  }, [docs, refreshDocs]);

  const upload = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      setUploading((n) => n + 1);
      try {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("kind", kind);
        const r = await api.post<{ document: Doc; reward: Reward }>(`/api/courses/${courseId}/documents`, fd);
        useProfile.getState().applyReward(r.reward);
        toast.success(`已上传 ${f.name}，正在解析…`);
      } catch (e) {
        toast.error(`${f.name}：${(e as Error).message}`);
      } finally {
        setUploading((n) => n - 1);
        void refreshDocs();
      }
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-surface p-3">
        <span className="text-sm font-medium text-slate-700">上传为</span>
        <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-sm">
          {KINDS.map((k) => (
            <button key={k.id} onClick={() => setKind(k.id)} className={`rounded-md px-3 py-1 transition ${kind === k.id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              {k.name}
            </button>
          ))}
        </div>
        <span className="min-w-0 flex-1 text-xs text-slate-400">{KINDS.find((k) => k.id === kind)?.hint}</span>
      </div>
      <div
        className={`grid cursor-pointer place-items-center rounded-2xl border-2 border-dashed p-10 text-center transition ${drag ? "border-indigo-400 bg-indigo-50" : "border-slate-300 bg-surface hover:border-indigo-300"}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void upload(e.dataTransfer.files);
        }}
      >
        {uploading ? <Loader2 className="mb-2 animate-spin text-indigo-500" size={32} /> : <FileUp className="mb-2 text-indigo-400" size={32} />}
        <div className="text-sm font-medium text-slate-700">拖拽或点击上传课程资料</div>
        <div className="mt-1 text-xs text-slate-400">支持 PDF / Word（.docx）/ PPT（.pptx）/ Markdown / TXT · 自动分块并建立全文 + 向量索引，英文资料可一键翻译</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.pptx,.md,.markdown,.txt"
          className="hidden"
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <CloudPanel courseId={courseId} onImported={() => void refreshDocs()} />

      <div className="space-y-2">
        {docs.map((d) => (
          <Card key={d.id} className="flex items-center gap-3 p-3.5">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-50 text-xl">{{ pdf: "📕", docx: "📘", pptx: "📙", md: "📝", txt: "📄" }[d.filetype] ?? "📄"}</div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 truncate text-sm font-medium text-slate-800">
                {d.source === "cloud" && <span title="来自清华云盘，原文件不存本机" className="shrink-0 rounded bg-sky-50 px-1 text-[10px] font-normal text-sky-600">☁ 云盘</span>}
                <span className="truncate">{d.filename}</span>
              </div>
              <div className="text-xs text-slate-400">
                {d.status === "processing" && <span className="text-indigo-500">解析中…</span>}
                {d.status === "ready" && (
                  <span>
                    {d.filetype === "pdf" ? `${d.page_count} 页 · ` : d.filetype === "pptx" ? `${d.page_count} 张幻灯片 · ` : ""}
                    {d.chunk_count} 个分块
                  </span>
                )}
                {d.status === "error" && <span className="text-rose-500">解析失败</span>}
                {d.error && <span className="ml-2 text-amber-600">{d.error}</span>}
              </div>
            </div>
            <select
              className="shrink-0 rounded-md border border-slate-200 bg-surface px-1.5 py-1 text-xs text-slate-600"
              value={d.kind}
              title="资料种类：课件用于知识梳理，试卷用于识别考点"
              onChange={async (e) => {
                const next = e.target.value as DocKind;
                if (d.kind === "slides" && !confirm("改成非课件后，这份课件已有的知识梳理结果会被清除。继续？")) return;
                if (d.kind === "exam" && !confirm("改成非试卷后，这份试卷已分析出的题目和考点会被清除。继续？")) return;
                await api.patch(`/api/documents/${d.id}`, { kind: next });
                void refreshDocs();
              }}
            >
              {KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>
            <Button
              variant="soft"
              size="sm"
              onClick={() => {
                openDoc(d.id);
                onOpen();
              }}
            >
              <BookOpen size={13} /> 阅读
            </Button>
            <Button
              variant="ghost"
              size="sm"
              title="重新解析（配置向量模型后可用来补建向量索引）"
              onClick={async () => {
                await api.post(`/api/documents/${d.id}/reindex`);
                void refreshDocs();
              }}
            >
              <RefreshCw size={13} />
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={async () => {
                if (!confirm(`删除「${d.filename}」？画布上的摘录卡会保留，但无法再跳回原文。`)) return;
                await api.del(`/api/documents/${d.id}`);
                void refreshDocs();
              }}
            >
              <Trash2 size={13} />
            </Button>
          </Card>
        ))}
      </div>
    </div>
  );
}

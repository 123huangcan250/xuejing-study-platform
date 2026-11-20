"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { SearchHit } from "@/lib/types";
import { useWorkspace } from "@/stores/workspace";
import { Button, Card, inputCls } from "@/components/ui";

export function SearchPanel({ courseId, onOpen }: { courseId: number; onOpen: () => void }) {
  const [q, setQ] = useState("");
  const [global, setGlobal] = useState(false);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const jumpTo = useWorkspace((s) => s.jumpTo);
  const router = useRouter();

  const run = async () => {
    if (!q.trim()) return;
    setLoading(true);
    try {
      setHits(await api.get<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}&k=15${global ? "" : `&course_id=${courseId}`}`));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <input className={inputCls} placeholder="在课程资料里检索（关键词 + 语义混合）" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="flex shrink-0 items-center gap-1 text-xs text-slate-500">
          <input type="checkbox" checked={global} onChange={(e) => setGlobal(e.target.checked)} /> 跨课程
        </label>
        <Button type="submit" disabled={loading}>
          {loading ? <Loader2 className="animate-spin" size={15} /> : <Search size={15} />} 检索
        </Button>
      </form>
      {hits?.length === 0 && <div className="py-8 text-center text-sm text-slate-400">没有找到相关内容</div>}
      {hits?.map((h) => (
        <Card key={h.chunk_id} className="cursor-pointer p-4 transition hover:border-indigo-300">
          <button
            className="w-full text-left"
            onClick={() => {
              const anchor = { document_id: h.document_id, page: h.page, quote: h.text.slice(0, 80), rects: [] };
              if (h.course_id !== courseId) {
                router.push(`/courses/${h.course_id}?doc=${h.document_id}&page=${h.page}`);
                return;
              }
              jumpTo(anchor);
              onOpen();
            }}
          >
            <div className="mb-1 flex items-center gap-2 text-xs text-slate-400">
              <span className="font-medium text-indigo-600">{h.filename}</span>
              {h.filetype === "pdf" && <span>第 {h.page} 页</span>}
              <span className="ml-auto">{h.via.replace(/^\+/, "")}</span>
            </div>
            <p className="line-clamp-4 text-sm leading-6 text-slate-600 whitespace-pre-wrap">{h.text}</p>
          </button>
        </Card>
      ))}
    </div>
  );
}

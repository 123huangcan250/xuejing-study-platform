"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { Cloud, FolderOpen, Link2, Loader2, Lock, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import type { CloudEntry, CloudImportStatus, CloudSource, DocKind } from "@/lib/types";
import { Button, Card, Modal, inputCls } from "@/components/ui";

const KINDS: { id: DocKind; name: string }[] = [
  { id: "slides", name: "课件" },
  { id: "exam", name: "试卷" },
  { id: "reference", name: "参考资料" },
];
const fmtSize = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : n ? `${n} B` : "");
const STATE: Record<CloudEntry["state"], { text: string; cls: string }> = {
  new: { text: "新", cls: "bg-emerald-50 text-emerald-700" },
  imported: { text: "已导入", cls: "bg-slate-100 text-slate-500" },
  updated: { text: "云盘有更新", cls: "bg-amber-50 text-amber-700" },
};

/** 从清华云盘分享链接读取资料：只读、原文件不落地（只保存解析出的文字和索引）。 */
export function CloudPanel({ courseId, onImported }: { courseId: number; onImported: () => void }) {
  const [sources, setSources] = useState<CloudSource[]>([]);
  const [adding, setAdding] = useState(false);
  const [browse, setBrowse] = useState<{ source: CloudSource; entries: CloudEntry[]; skipped: number } | null>(null);

  const load = useCallback(async () => {
    try {
      setSources(await api.get<CloudSource[]>(`/api/courses/${courseId}/cloud/sources`));
    } catch {
      /* 后端未运行时静默 */
    }
  }, [courseId]);
  useEffect(() => {
    void load();
  }, [load]);

  const running = sources.some((s) => s.import.state === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      void load();
      onImported();
    }, 2000);
    return () => clearInterval(t);
  }, [running, load, onImported]);

  const open = async (s: CloudSource) => {
    try {
      const r = await api.get<{ source: CloudSource; entries: CloudEntry[]; skipped: number }>(`/api/cloud/sources/${s.id}/entries`);
      setBrowse(r);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "读取失败";
      if (e instanceof ApiError && e.status === 401) {
        const pw = window.prompt(`${msg}\n请输入新的提取码（只在本机加密保存）：`);
        if (pw) {
          try {
            await api.put(`/api/cloud/sources/${s.id}/password`, { password: pw });
            await load();
            toast.success("提取码已更新");
          } catch (e2) {
            toast.error((e2 as Error).message);
          }
        }
      } else toast.error(msg);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Cloud size={16} className="text-sky-500" />
        <h3 className="text-sm font-semibold text-slate-800">清华云盘</h3>
        <span className="text-xs text-slate-400">只读分享链接 · 原文件不存本机</span>
        <div className="flex-1" />
        <Button size="sm" variant="soft" onClick={() => setAdding(true)}>
          <Link2 size={13} /> 添加分享链接
        </Button>
      </div>

      {sources.map((s) => (
        <Card key={s.id} className="flex flex-wrap items-center gap-3 p-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-sky-50 text-sky-600">{s.share_type === "d" ? <FolderOpen size={17} /> : <Cloud size={17} />}</div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 truncate text-sm font-medium text-slate-800">
              {s.name || (s.share_type === "d" ? "文件夹分享" : "文件分享")}
              {s.has_password && <Lock size={12} className="text-slate-400" aria-label="有提取码" />}
            </div>
            <div className="truncate text-xs text-slate-400">
              {s.url} · 已导入 {s.documents} 份{s.last_synced ? ` · ${new Date(s.last_synced).toLocaleDateString("zh-CN")} 同步` : ""}
            </div>
          </div>
          {s.import.state === "running" ? (
            <span className="flex items-center gap-1.5 text-xs text-indigo-600">
              <Loader2 size={13} className="animate-spin" /> 导入中 {s.import.done}/{s.import.total}
              {s.import.current ? <span className="hidden max-w-40 truncate text-slate-400 md:inline">· {s.import.current}</span> : null}
            </span>
          ) : (
            <>
              {s.import.state === "error" && <span className="max-w-56 truncate text-xs text-rose-600" title={s.import.error}>{s.import.error}</span>}
              {s.import.errors.length > 0 && <span className="text-xs text-amber-600" title={s.import.errors.map((x) => `${x.name}：${x.error}`).join("\n")}>{s.import.errors.length} 个文件失败</span>}
              <Button size="sm" variant="outline" onClick={() => void open(s)}>
                <RefreshCw size={13} /> 浏览 / 导入
              </Button>
            </>
          )}
          <Button
            size="sm"
            variant="danger"
            title="删除来源"
            onClick={async () => {
              if (!confirm(`删除这个云盘来源？从它导入的 ${s.documents} 份资料也会一并删除（云盘里的文件不受影响）。`)) return;
              await api.del(`/api/cloud/sources/${s.id}`);
              await load();
              onImported();
            }}
          >
            <Trash2 size={13} />
          </Button>
        </Card>
      ))}
      {!sources.length && <p className="text-xs leading-5 text-slate-400">在云盘里创建文件夹或文件的分享链接（有无提取码都行），粘贴到这里，就能直接读取，不用先下载到本机。</p>}

      <AddDialog
        open={adding}
        courseId={courseId}
        onClose={() => setAdding(false)}
        onAdded={async (r) => {
          setAdding(false);
          await load();
          setBrowse(r);
        }}
      />
      {browse && (
        <BrowseDialog
          data={browse}
          onClose={() => setBrowse(null)}
          onStarted={async () => {
            setBrowse(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

function AddDialog({ open, courseId, onClose, onAdded }: { open: boolean; courseId: number; onClose: () => void; onAdded: (r: { source: CloudSource; entries: CloudEntry[]; skipped: number }) => void }) {
  const [url, setUrl] = useState("");
  const [password, setPassword] = useState("");
  const [needPw, setNeedPw] = useState(false);
  const [kind, setKind] = useState<DocKind>("slides");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.post<{ source: CloudSource; entries: CloudEntry[]; skipped: number }>(`/api/courses/${courseId}/cloud/sources`, {
        url,
        password: password || null,
        default_kind: kind,
      });
      setUrl("");
      setPassword("");
      setNeedPw(false);
      onAdded(r);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "添加失败";
      if (e instanceof ApiError && e.status === 401) setNeedPw(true);
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="添加清华云盘分享链接">
      <div className="space-y-3">
        <input className={inputCls} autoFocus placeholder="https://cloud.tsinghua.edu.cn/d/xxxxxxxx/" value={url} onChange={(e) => setUrl(e.target.value)} />
        {(needPw || password) && (
          <input className={clsx(inputCls, needPw && "border-amber-400")} type="password" autoComplete="off" placeholder="提取码" value={password} onChange={(e) => setPassword(e.target.value)} />
        )}
        {!needPw && !password && (
          <button className="text-xs text-indigo-600 hover:underline" onClick={() => setNeedPw(true)}>
            这个链接有提取码
          </button>
        )}
        <div className="flex items-center gap-2 text-xs text-slate-500">
          导入为
          <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
            {KINDS.map((k) => (
              <button key={k.id} onClick={() => setKind(k.id)} className={clsx("rounded-md px-2.5 py-1", kind === k.id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500")}>
                {k.name}
              </button>
            ))}
          </div>
        </div>
        {err && <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{err}</p>}
        <p className="text-[11px] leading-5 text-slate-400">只读取分享链接本身，不需要清华账号。提取码仅以 Windows 用户加密保存在本机，不会出现在日志里。仅支持清华云盘（cloud.tsinghua.edu.cn）。</p>
        <div className="flex justify-end">
          <Button onClick={() => void submit()} disabled={busy || !url.trim()}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : null} 连接并浏览
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function BrowseDialog({ data, onClose, onStarted }: { data: { source: CloudSource; entries: CloudEntry[]; skipped: number }; onClose: () => void; onStarted: () => void }) {
  const { source, entries, skipped } = data;
  const [picked, setPicked] = useState<Set<string>>(new Set(entries.filter((e) => e.state === "new").map((e) => e.path)));
  const [kind, setKind] = useState<DocKind>(source.default_kind);
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => {
    const m = new Map<string, CloudEntry[]>();
    for (const e of entries) {
      const dir = e.path.includes("/") ? e.path.slice(0, e.path.lastIndexOf("/")) || "/" : "/";
      m.set(dir, [...(m.get(dir) ?? []), e]);
    }
    return [...m.entries()];
  }, [entries]);
  const updatedPicked = entries.filter((e) => picked.has(e.path) && e.state === "updated").length;

  const toggle = (p: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });

  const start = async () => {
    if (updatedPicked && !confirm(`其中 ${updatedPicked} 个文件在云盘上有更新。导入会替换原有资料，旧资料上的译文、知识梳理结果会丢失。继续？`)) return;
    setBusy(true);
    try {
      await api.post(`/api/cloud/sources/${source.id}/import`, { paths: [...picked], kind, replace: updatedPicked > 0 });
      toast("开始导入，可以先去做别的");
      onStarted();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={`云盘文件（${entries.length}）`} width="max-w-2xl">
      <div className="space-y-3">
        {entries.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-400">没有找到可导入的文件（支持 PDF / Word / PPT / Markdown / TXT）。</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <button className="rounded px-2 py-1 text-indigo-600 hover:bg-indigo-50" onClick={() => setPicked(new Set(entries.filter((e) => e.state !== "imported").map((e) => e.path)))}>
                全选未导入
              </button>
              <button className="rounded px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={() => setPicked(new Set())}>
                清空
              </button>
              <span className="text-slate-400">已选 {picked.size}</span>
              {skipped > 0 && <span className="text-slate-400">· 另有 {skipped} 个不支持的文件已忽略</span>}
            </div>
            <div className="scroll-thin max-h-[46vh] space-y-3 overflow-y-auto rounded-xl border border-slate-200 p-2">
              {groups.map(([dir, items]) => (
                <div key={dir}>
                  <div className="mb-1 flex items-center gap-1 px-1 text-[11px] font-medium text-slate-400">
                    <FolderOpen size={11} /> {dir}
                  </div>
                  {items.map((e) => (
                    <label key={e.path} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-slate-50">
                      <input type="checkbox" checked={picked.has(e.path)} onChange={() => toggle(e.path)} />
                      <span className="min-w-0 flex-1 truncate text-slate-700">{e.name}</span>
                      <span className="shrink-0 text-xs text-slate-400">{fmtSize(e.size)}</span>
                      <span className={clsx("shrink-0 rounded px-1.5 py-0.5 text-[10.5px]", STATE[e.state].cls)}>{STATE[e.state].text}</span>
                    </label>
                  ))}
                </div>
              ))}
            </div>
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            导入为
            <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
              {KINDS.map((k) => (
                <button key={k.id} onClick={() => setKind(k.id)} className={clsx("rounded-md px-2.5 py-1", kind === k.id ? "bg-surface font-medium text-indigo-700 shadow-sm" : "text-slate-500")}>
                  {k.name}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1" />
          <Button onClick={() => void start()} disabled={busy || picked.size === 0}>
            导入所选（{picked.size}）
          </Button>
        </div>
        <p className="text-[11px] leading-5 text-slate-400">导入时只保存解析出的文字和检索索引；原文件放在临时缓存里，应用重启就会清空，需要阅读时再从云盘取回。</p>
      </div>
    </Modal>
  );
}

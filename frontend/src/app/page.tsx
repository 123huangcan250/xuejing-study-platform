"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Layers, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Course, HeatDay, LLMConfig } from "@/lib/types";
import { useProfile } from "@/stores/profile";
import { Button, Card, Modal, inputCls } from "@/components/ui";
import { Heatmap } from "@/components/gamification/Heatmap";
import { LevelCard } from "@/components/gamification/LevelCard";

const EMOJIS = ["📘", "🖥️", "🧠", "📈", "⚛️", "🧪", "📐", "🌍", "💡", "🎨", "⚙️", "📜"];
const COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#64748b"];

export default function Home() {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [heat, setHeat] = useState<HeatDay[]>([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", emoji: "📘", color: "#6366f1" });
  const [needKey, setNeedKey] = useState(false);
  const [offline, setOffline] = useState(false);
  const { profile, refresh } = useProfile();

  const load = async () => {
    try {
      const [c, h, s] = await Promise.all([
        api.get<Course[]>("/api/courses"),
        api.get<HeatDay[]>("/api/heatmap"),
        api.get<LLMConfig>("/api/settings"),
      ]);
      setCourses(c);
      setHeat(h);
      const usesTsinghua = s.tiers.fast.provider === "tsinghua" && s.tiers.strong.provider === "tsinghua";
      setNeedKey(!s.has_api_key && !usesTsinghua);
      setOffline(false);
    } catch {
      setOffline(true);
      setCourses([]);
    }
    void refresh();
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async () => {
    if (!form.name.trim()) return;
    try {
      await api.post("/api/courses", form);
      setCreating(false);
      setForm({ name: "", description: "", emoji: "📘", color: "#6366f1" });
      void load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const totalDue = courses?.reduce((a, c) => a + c.stats.due, 0) ?? 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      {offline && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
          连接不上后端服务。请在项目根目录运行 <code className="rounded bg-surface px-1">scripts\dev.ps1</code> 启动。
        </div>
      )}
      {needKey && !offline && (
        <div className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          <span>还没有配置模型 API。阅读、上传、闪卡复习可以先用，AI 问答需要先填 OpenAI 兼容的 API Key。</span>
          <Link href="/settings" className="shrink-0 rounded-lg bg-amber-500 px-3 py-1.5 text-white">
            去配置
          </Link>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-6">
          <div className="flex items-end justify-between">
            <div>
              <h1 className="text-2xl font-bold text-slate-800">课程世界</h1>
              <p className="mt-1 text-sm text-slate-500">一门课 = 一个冒险世界：资料、画布、闪卡和进度都在里面。</p>
            </div>
            <Button onClick={() => setCreating(true)}>
              <Plus size={16} /> 新建课程
            </Button>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            {courses?.map((c) => (
              <Link key={c.id} href={`/courses/${c.id}`} className="group">
                <Card className="relative overflow-hidden p-5 transition group-hover:-translate-y-0.5 group-hover:shadow-md">
                  <div className="absolute inset-x-0 top-0 h-1" style={{ background: c.color }} />
                  <div className="flex items-start gap-3">
                    <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl text-2xl" style={{ background: `${c.color}18` }}>
                      {c.emoji}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-semibold text-slate-800">{c.name}</div>
                      <div className="line-clamp-1 text-xs text-slate-400">{c.description || "还没有描述"}</div>
                    </div>
                    <button
                      className="rounded p-1 text-slate-300 opacity-0 transition hover:text-rose-500 group-hover:opacity-100"
                      title="删除课程"
                      onClick={async (e) => {
                        e.preventDefault();
                        if (!confirm(`删除课程「${c.name}」？资料、画布、闪卡都会被删除，无法恢复。`)) return;
                        await api.del(`/api/courses/${c.id}`);
                        void load();
                      }}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                  <div className="mt-4 grid grid-cols-4 gap-2 text-center text-xs text-slate-500">
                    <Stat n={c.stats.documents} label="资料" />
                    <Stat n={c.stats.nodes} label="画布节点" />
                    <Stat n={c.stats.flashcards} label="闪卡" />
                    <Stat n={c.stats.due} label="待复习" highlight={c.stats.due > 0} />
                  </div>
                </Card>
              </Link>
            ))}
            {courses?.length === 0 && !offline && (
              <button onClick={() => setCreating(true)} className="col-span-2 rounded-2xl border-2 border-dashed border-slate-300 p-12 text-center text-slate-400 hover:border-indigo-300 hover:text-indigo-500">
                <div className="mb-2 text-3xl">🗺️</div>
                创建你的第一个课程世界，比如《操作系统》《机器学习》
              </button>
            )}
          </div>

          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold text-slate-800">学习热力图</h2>
              <span className="text-xs text-slate-400">每天达到 {profile?.today.goal ?? 20} XP 即点亮连胜</span>
            </div>
            <Heatmap data={heat} goal={profile?.today.goal ?? 20} />
          </Card>
        </div>

        <div className="space-y-4">
          {profile && <LevelCard profile={profile} />}
          <Card className="p-5">
            <div className="flex items-center gap-3">
              <div className="grid h-11 w-11 place-items-center rounded-xl bg-indigo-50 text-indigo-600">
                <Layers size={20} />
              </div>
              <div className="flex-1">
                <div className="text-sm font-medium text-slate-700">今日到期闪卡</div>
                <div className="text-2xl font-bold text-slate-800">{totalDue}</div>
              </div>
              <Link href="/review">
                <Button disabled={!totalDue}>去复习</Button>
              </Link>
            </div>
          </Card>
          {profile && (
            <Card className="p-5">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-700">成就</h3>
                <Link href="/profile" className="text-xs text-indigo-600">
                  全部 →
                </Link>
              </div>
              <div className="grid grid-cols-6 gap-2">
                {profile.achievements.map((a) => (
                  <div
                    key={a.key}
                    title={`${a.name}：${a.desc}`}
                    className={`grid aspect-square place-items-center rounded-xl text-xl ${a.unlocked ? "bg-amber-50" : "bg-slate-50 opacity-30 grayscale"}`}
                  >
                    {a.icon}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>

      <Modal open={creating} onClose={() => setCreating(false)} title="新建课程世界">
        <div className="space-y-4">
          <input className={inputCls} autoFocus placeholder="课程名，如：操作系统" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} onKeyDown={(e) => e.key === "Enter" && void create()} />
          <input className={inputCls} placeholder="一句话描述（可选）" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <div className="flex flex-wrap gap-2">
            {EMOJIS.map((e) => (
              <button key={e} onClick={() => setForm({ ...form, emoji: e })} className={`grid h-9 w-9 place-items-center rounded-lg text-lg ${form.emoji === e ? "bg-indigo-100 ring-2 ring-indigo-400" : "bg-slate-50"}`}>
                {e}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            {COLORS.map((c) => (
              <button key={c} onClick={() => setForm({ ...form, color: c })} className={`h-7 w-7 rounded-full ${form.color === c ? "ring-2 ring-offset-2 ring-offset-surface" : ""}`} style={{ background: c, ["--tw-ring-color" as string]: c }} />
            ))}
          </div>
          <div className="flex justify-end">
            <Button onClick={() => void create()} disabled={!form.name.trim()}>
              创建
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Stat({ n, label, highlight }: { n: number; label: string; highlight?: boolean }) {
  return (
    <div className={`rounded-lg py-1.5 ${highlight ? "bg-indigo-50 text-indigo-600" : "bg-slate-50"}`}>
      <div className="text-base font-semibold">{n}</div>
      <div className="text-[10px]">{label}</div>
    </div>
  );
}

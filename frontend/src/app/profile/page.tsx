"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { HeatDay } from "@/lib/types";
import { useProfile } from "@/stores/profile";
import { Card } from "@/components/ui";
import { Heatmap } from "@/components/gamification/Heatmap";
import { LevelCard } from "@/components/gamification/LevelCard";

const EVENT_LABEL: Record<string, string> = {
  DOC_UPLOADED: "上传资料",
  HIGHLIGHT_ASKED: "原文分叉提问",
  NODE_ASKED: "画布追问",
  TERM_EXPLORED: "术语探索",
  MERGE_ASKED: "多节点汇总提问",
  NOTE_CREATED: "写笔记",
  FLASHCARD_CREATED: "制作闪卡",
  FLASHCARD_REVIEWED: "复习闪卡",
};

interface Ev {
  id: number;
  type: string;
  xp: number;
  created_at: string;
}

export default function ProfilePage() {
  const { profile, refresh } = useProfile();
  const [heat, setHeat] = useState<HeatDay[]>([]);
  const [events, setEvents] = useState<Ev[]>([]);

  useEffect(() => {
    void refresh();
    api.get<HeatDay[]>("/api/heatmap").then(setHeat).catch(() => undefined);
    api.get<Ev[]>("/api/events?limit=40").then(setEvents).catch(() => undefined);
  }, [refresh]);

  if (!profile) return <div className="p-10 text-center text-sm text-slate-400">加载中…</div>;
  const groups = Array.from(new Set(profile.achievements.map((a) => a.category ?? "其他")));
  const unlocked = profile.achievements.filter((a) => a.unlocked).length;

  return (
    <div className="mx-auto grid max-w-6xl gap-6 p-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <div className="min-w-0 space-y-4">
        <LevelCard profile={profile} />
        <Card className="p-5">
          <h3 className="mb-3 text-sm font-semibold text-slate-700">今天</h3>
          <div className="grid grid-cols-2 gap-3 text-center">
            <div className="rounded-xl bg-slate-50 p-3">
              <div className="text-xl font-bold text-slate-800">{profile.today.reviews}</div>
              <div className="text-xs text-slate-400">次复习</div>
            </div>
            <div className="rounded-xl bg-slate-50 p-3">
              <div className="text-xl font-bold text-slate-800">{profile.today.questions}</div>
              <div className="text-xs text-slate-400">个提问</div>
            </div>
          </div>
        </Card>
        <Card className="p-5">
          <h3 className="mb-3 text-sm font-semibold text-slate-700">最近动态</h3>
          <div className="scroll-thin max-h-80 space-y-1.5 overflow-y-auto text-xs">
            {events.map((e) => (
              <div key={e.id} className="flex items-center gap-2">
                <span className="text-slate-400 tabular-nums">{new Date(e.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                <span className="flex-1 text-slate-600">{EVENT_LABEL[e.type] ?? e.type}</span>
                <span className={e.xp ? "text-indigo-600" : "text-slate-300"}>+{e.xp}</span>
              </div>
            ))}
            {!events.length && <div className="text-slate-400">还没有学习记录</div>}
          </div>
        </Card>
      </div>
      <div className="space-y-6">
        <Card className="p-5">
          <h2 className="mb-3 font-semibold text-slate-800">学习热力图</h2>
          <Heatmap data={heat} goal={profile.today.goal} />
        </Card>
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold text-slate-800">成就徽章墙</h2>
            <span className="text-xs text-slate-400">
              {unlocked} / {profile.achievements.length}
            </span>
          </div>
          {groups.map((g) => (
            <div key={g} className="mb-5">
              <div className="mb-2 text-xs font-medium text-slate-400">{g}</div>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {profile.achievements
                  .filter((a) => (a.category ?? "其他") === g)
                  .map((a) => (
                    <div key={a.key} className={`flex items-center gap-3 rounded-xl border p-3 ${a.unlocked ? "border-amber-200 bg-amber-50/60" : "border-slate-100 bg-slate-50"}`}>
                      <div className={`text-3xl ${a.unlocked ? "" : "opacity-30 grayscale"}`}>{a.icon}</div>
                      <div className="min-w-0">
                        <div className={`text-sm font-medium ${a.unlocked ? "text-slate-800" : "text-slate-400"}`}>{a.name}</div>
                        <div className="text-xs text-slate-400">{a.desc}</div>
                        {a.unlocked_at && <div className="text-[10px] text-amber-600">{new Date(a.unlocked_at).toLocaleDateString("zh-CN")} 解锁</div>}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

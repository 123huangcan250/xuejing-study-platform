"use client";
import { Coins, Flame, Target } from "lucide-react";
import type { Profile } from "@/lib/types";
import { Card, ProgressBar } from "@/components/ui";

/** 学习伙伴的成长形态（Phase 4 会做成可装扮的完整伙伴系统） */
export function companionStage(level: number) {
  if (level >= 30) return { icon: "🦉", name: "学科贤者" };
  if (level >= 15) return { icon: "🧭", name: "知识探索者" };
  if (level >= 5) return { icon: "📝", name: "笔记学徒" };
  return { icon: "🥚", name: "见习蛋" };
}

export function LevelCard({ profile }: { profile: Profile }) {
  const stage = companionStage(profile.level);
  // 今天还没学习、且已有学习历史时，伙伴“犯困”（温和提醒，不扣任何东西）
  const sleepy = profile.today.xp === 0 && profile.total_xp > 0;
  return (
    <Card className="overflow-hidden">
      <div className="bg-gradient-to-br from-indigo-500 via-indigo-600 to-violet-600 p-5 text-white">
        <div className="flex items-center gap-4">
          <div className="grid h-16 w-16 place-items-center rounded-2xl bg-white/15 text-4xl" title={sleepy ? "伙伴有点犯困了，来学一会儿吧" : stage.name}>
            <span className={sleepy ? "opacity-70 grayscale-[40%]" : "animate-bounce [animation-duration:2.5s]"}>{stage.icon}</span>
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-xs text-indigo-100">{sleepy ? "💤 伙伴在打哈欠…" : stage.name}</div>
            <div className="text-2xl font-bold">Lv.{profile.level}</div>
            <div className="mt-1.5 flex items-center gap-2 text-xs text-indigo-100">
              <ProgressBar value={profile.xp_in_level} max={profile.xp_to_next} className="!bg-white/20" color="bg-white" />
              <span className="shrink-0 tabular-nums">
                {profile.xp_in_level}/{profile.xp_to_next}
              </span>
            </div>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 divide-x divide-slate-100 text-center">
        <div className="p-3">
          <div className="flex items-center justify-center gap-1 text-lg font-semibold text-orange-500">
            <Flame size={17} className={profile.streak.today_lit ? "fill-orange-400" : ""} />
            {profile.streak.current}
          </div>
          <div className="text-[11px] text-slate-400">连胜天数 · 最长 {profile.streak.longest}</div>
        </div>
        <div className="p-3">
          <div className="flex items-center justify-center gap-1 text-lg font-semibold text-indigo-600">
            <Target size={17} />
            {profile.today.xp}/{profile.today.goal}
          </div>
          <div className="text-[11px] text-slate-400">今日 XP 目标</div>
        </div>
        <div className="p-3">
          <div className="flex items-center justify-center gap-1 text-lg font-semibold text-amber-500">
            <Coins size={17} />
            {profile.coins}
          </div>
          <div className="text-[11px] text-slate-400">学习金币</div>
        </div>
      </div>
    </Card>
  );
}

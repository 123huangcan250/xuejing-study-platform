"use client";
import { create } from "zustand";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Profile, Reward } from "@/lib/types";
import { celebrate } from "@/lib/fx";

interface ProfileState {
  profile: Profile | null;
  refresh: () => Promise<void>;
  applyReward: (r: Reward | null | undefined) => void;
}

export const useProfile = create<ProfileState>((set, get) => ({
  profile: null,
  refresh: async () => {
    try {
      set({ profile: await api.get<Profile>("/api/profile") });
    } catch {
      /* 后端未启动时静默 */
    }
  },
  /** 统一处理所有学习事件的奖励反馈：XP 浮字、升级彩带、成就弹窗、连胜点亮。 */
  applyReward: (r) => {
    if (!r) return;
    if (r.xp > 0) toast.success(`+${r.xp} XP${r.coins ? `  ·  +${r.coins} 金币` : ""}`, { duration: 1800 });
    if (r.streak_lit_now) {
      toast(`🔥 今日目标达成！连胜 ${r.streak} 天`, { duration: 4000 });
      celebrate("streak");
    }
    if (r.level_up) {
      toast(`🎉 升级到 Lv.${r.level}！`, { duration: 4000 });
      celebrate("level");
    }
    for (const a of r.new_achievements) {
      toast(`${a.icon} 解锁成就「${a.name}」`, { description: a.desc, duration: 5000 });
      celebrate("achievement");
    }
    const p = get().profile;
    if (p) {
      set({
        profile: {
          ...p,
          total_xp: r.total_xp,
          level: r.level,
          xp_in_level: r.xp_in_level,
          xp_to_next: r.xp_to_next,
          coins: p.coins + r.coins,
          today: { ...p.today, xp: r.today_xp },
          streak: { ...p.streak, current: r.streak, today_lit: r.today_xp >= r.daily_goal },
        },
      });
    }
    if (r.new_achievements.length) void get().refresh();
  },
}));

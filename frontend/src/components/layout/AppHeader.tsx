"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Flame, Coins, LogOut, Settings, Trophy, Home, Layers, Volume2, VolumeX } from "lucide-react";
import { useProfile } from "@/stores/profile";
import { isMuted, setMuted } from "@/lib/fx";
import { AppearanceMenu } from "./AppearanceMenu";
import { useAuth } from "@/stores/auth";

const NAV = [
  { href: "/", label: "课程世界", icon: Home },
  { href: "/review", label: "今日复习", icon: Layers },
  { href: "/profile", label: "成就", icon: Trophy },
  { href: "/settings", label: "设置", icon: Settings },
];

export function AppHeader() {
  const path = usePathname();
  const { profile, refresh } = useProfile();
  const { required, authenticated, logout } = useAuth();
  const [muted, setM] = useState(false);

  useEffect(() => {
    void refresh();
    setM(isMuted());
  }, [refresh]);

  const pct = profile ? Math.min(100, (profile.xp_in_level / profile.xp_to_next) * 100) : 0;
  const goalPct = profile ? Math.min(100, (profile.today.xp / profile.today.goal) * 100) : 0;

  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-4 border-b border-slate-200/80 bg-surface/85 px-4 backdrop-blur">
      <Link href="/" className="flex items-center gap-2 font-semibold text-slate-800">
        <span className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-white">
          学
        </span>
        <span className="hidden whitespace-nowrap xl:inline">学境 · AI 学习冒险</span>
      </Link>
      <nav className="flex items-center gap-1">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? path === "/" || path.startsWith("/courses") : path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition",
                active ? "bg-indigo-50 text-indigo-700" : "text-slate-500 hover:bg-slate-100 hover:text-slate-700",
              )}
            >
              <Icon size={16} />
              <span className="hidden whitespace-nowrap lg:inline">{label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto flex items-center gap-3 text-sm">
        <AppearanceMenu />
        {required && authenticated && (
          <button className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600" title="退出登录" onClick={() => void logout()}>
            <LogOut size={16} />
          </button>
        )}
        <button
          className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100"
          title={muted ? "开启音效" : "静音"}
          onClick={() => {
            setMuted(!muted);
            setM(!muted);
          }}
        >
          {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
        </button>
        {profile && (
          <>
            <div
              className={clsx(
                "flex items-center gap-1 rounded-full px-2.5 py-1 font-medium",
                profile.streak.today_lit ? "bg-orange-50 text-orange-600" : "bg-slate-100 text-slate-400",
              )}
              title={`今日 ${profile.today.xp}/${profile.today.goal} XP，达标即点亮连胜`}
            >
              <Flame size={15} className={profile.streak.today_lit ? "fill-orange-400" : ""} />
              {profile.streak.current}
              <span className="ml-1 hidden h-1.5 w-10 overflow-hidden rounded-full bg-surface/70 lg:inline-block">
                <span className="block h-full bg-orange-400" style={{ width: `${goalPct}%` }} />
              </span>
            </div>
            <div className="hidden items-center gap-1 text-amber-600 sm:flex" title="学习金币">
              <Coins size={15} />
              {profile.coins}
            </div>
            <Link href="/profile" className="flex items-center gap-2" title={`${profile.xp_in_level}/${profile.xp_to_next} XP`}>
              <span className="rounded-md bg-indigo-600 px-1.5 py-0.5 text-xs font-bold text-white">Lv.{profile.level}</span>
              <span className="hidden h-2 w-24 overflow-hidden rounded-full bg-slate-100 sm:block">
                <span className="block h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-700" style={{ width: `${pct}%` }} />
              </span>
            </Link>
          </>
        )}
      </div>
    </header>
  );
}

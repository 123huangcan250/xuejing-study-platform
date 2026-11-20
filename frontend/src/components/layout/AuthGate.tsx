"use client";
import { useEffect, useState } from "react";
import { Loader2, Lock, ShieldAlert } from "lucide-react";
import { AUTH_EVENT } from "@/lib/api";
import { useAuth } from "@/stores/auth";
import { Button, inputCls } from "@/components/ui";

/** 登录门：设置了访问密码就先登录；没设密码却从远程访问时，说明原因而不是一堆报错。 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { ready, required, authenticated, blocked, error, busy, check, require, login } = useAuth();
  const [pw, setPw] = useState("");

  useEffect(() => {
    void check();
    const on = () => require();
    window.addEventListener(AUTH_EVENT, on);
    return () => window.removeEventListener(AUTH_EVENT, on);
  }, [check, require]);

  if (!ready) return <div className="grid h-full place-items-center text-slate-400"><Loader2 className="animate-spin" /></div>;

  if (blocked) {
    return (
      <div className="grid h-full place-items-center p-6">
        <div className="max-w-md rounded-2xl border border-slate-200 bg-surface p-8 text-center shadow-sm">
          <ShieldAlert className="mx-auto mb-3 text-amber-500" size={34} />
          <h2 className="text-lg font-bold text-slate-800">远程访问还没有启用</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            为了安全，必须先在运行本程序的电脑上设置访问密码，才允许从其他设备访问。在那台电脑上运行 <code className="rounded bg-slate-100 px-1">scripts\remote.ps1</code> 会引导你完成。
          </p>
        </div>
      </div>
    );
  }

  if (required && !authenticated) {
    return (
      <div className="grid h-full place-items-center p-6">
        <form
          className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-surface p-8 shadow-sm"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await login(pw)) {
              setPw("");
              window.location.reload(); // 重新加载，让各页面带着登录状态取数据
            }
          }}
        >
          <div className="text-center">
            <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-2xl bg-indigo-50 text-indigo-600">
              <Lock size={22} />
            </div>
            <h2 className="text-lg font-bold text-slate-800">请输入访问密码</h2>
            <p className="mt-1 text-xs text-slate-400">这是你在宿舍电脑上设置的访问密码</p>
          </div>
          <input className={inputCls} type="password" autoFocus autoComplete="current-password" placeholder="访问密码" value={pw} onChange={(e) => setPw(e.target.value)} />
          {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}
          <Button type="submit" className="w-full" disabled={busy || !pw}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : null} 登录
          </Button>
        </form>
      </div>
    );
  }
  return <>{children}</>;
}

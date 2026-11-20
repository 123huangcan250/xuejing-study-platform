"use client";
import { useCallback, useEffect, useState } from "react";
import { KeyRound, Loader2, LogIn, RefreshCw, ShieldCheck, Stethoscope, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { TsinghuaStatus } from "@/lib/types";
import { Button, Card } from "@/components/ui";

const STATE_LABEL: Record<TsinghuaStatus["state"], { text: string; cls: string }> = {
  not_configured: { text: "未登录", cls: "bg-slate-100 text-slate-600" },
  valid: { text: "token 有效", cls: "bg-emerald-50 text-emerald-700" },
  refreshing: { text: "正在认证", cls: "bg-indigo-50 text-indigo-700" },
  needs_user: { text: "需要你完成验证", cls: "bg-amber-50 text-amber-700" },
  credentials_invalid: { text: "账号或密码可能有误", cls: "bg-rose-50 text-rose-700" },
  error: { text: "出错", cls: "bg-rose-50 text-rose-700" },
  expired: { text: "token 已过期", cls: "bg-rose-50 text-rose-700" },
  stopped: { text: "已停止", cls: "bg-slate-100 text-slate-600" },
};

const fmt = (t: number | null) => (t ? new Date(t * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const dur = (s: number | null) => (s === null ? "—" : s >= 3600 ? `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分` : `${Math.floor(s / 60)} 分钟`);

interface CheckResult {
  model: string;
  ok: boolean;
  reply?: string;
  error?: string;
  ms: number;
}

export function TsinghuaPanel() {
  const [st, setSt] = useState<TsinghuaStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [checks, setChecks] = useState<CheckResult[] | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<TsinghuaStatus>("/api/tsinghua/status"));
    } catch {
      /* 后端未运行 */
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), st?.state === "refreshing" || st?.state === "needs_user" ? 2000 : 15000);
    return () => clearInterval(t);
  }, [load, st?.state]);

  if (!st) return null;
  const label = STATE_LABEL[st.state] ?? STATE_LABEL.error;

  const refresh = async (interactive: boolean) => {
    setSt(await api.post<TsinghuaStatus>("/api/tsinghua/refresh", { interactive }));
    toast(interactive ? "正在打开清华登录窗口，请在弹出的浏览器中亲自完成登录 / 验证" : "已开始重新认证");
  };

  const check = async () => {
    if (!confirm("将对 3 个模型各发一次最小真实请求，会消耗少量学校提供的额度。继续？")) return;
    setChecking(true);
    try {
      setChecks((await api.post<{ results: CheckResult[] }>("/api/tsinghua/models/check", {})).results);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setChecking(false);
      void load();
    }
  };

  return (
    <Card className="space-y-4 p-5">
      <div className="flex items-center gap-2">
        <ShieldCheck size={18} className="text-violet-600" />
        <h2 className="font-semibold text-slate-800">清华大学大模型服务</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs ${label.cls}`}>{label.text}</span>
        {st.state === "refreshing" && <Loader2 size={14} className="animate-spin text-indigo-500" />}
      </div>
      <p className="text-sm text-slate-600">{st.message}</p>

      <div className="grid gap-3 text-xs sm:grid-cols-4">
        <Info label="token 剩余" value={st.token_valid ? dur(st.expires_in_s) : "—"} />
        <Info label="下次自动更新" value={fmt(st.next_run_at)} />
        <Info label="上次成功获取" value={fmt(st.last_success_at)} />
        <Info label="连续失败" value={String(st.consecutive_failures)} />
      </div>

      <div className="rounded-xl bg-slate-50 p-3 text-xs leading-6 text-slate-600">
        <div className="mb-1 flex items-center gap-1 font-medium text-slate-700">
          <KeyRound size={13} /> 本机凭据
          <span className={st.has_credentials ? "text-emerald-600" : "text-slate-400"}>{st.has_credentials ? "已加密保存" : "未保存"}</span>
          {st.password_blocked && <span className="text-rose-600">· 已暂停自动提交密码</span>}
          <span className="text-slate-400">· 浏览器登录状态 {st.has_session ? "已保存" : "无"}</span>
        </div>
        账号密码只在本机终端输入，不经过网页：在项目根目录运行
        <code className="mx-1 rounded bg-surface px-1.5 py-0.5">.\scripts\tsinghua-setup.ps1</code>
        （改密码后同样运行它）。也可以不保存密码，点「打开登录窗口」每次亲自登录。
        系统每次登录后的 token 约 5 小时有效，本项目在约 4.5 小时时自动重新认证；遇到验证码或二次验证会弹出浏览器窗口请你完成。
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => void refresh(false)} disabled={st.state === "refreshing"}>
          <RefreshCw size={13} /> 立即重新认证
        </Button>
        <Button variant="outline" size="sm" onClick={() => void refresh(true)} disabled={st.state === "refreshing"}>
          <LogIn size={13} /> 打开登录窗口
        </Button>
        <Button variant="outline" size="sm" onClick={() => void check()} disabled={!st.token_valid || checking}>
          {checking ? <Loader2 size={13} className="animate-spin" /> : <Stethoscope size={13} />} 检测模型可用性
        </Button>
        <div className="flex-1" />
        <Button
          variant="danger"
          size="sm"
          onClick={async () => {
            if (!confirm("清除本机 token 和浏览器登录状态？")) return;
            await api.post("/api/tsinghua/logout");
            void load();
          }}
        >
          <Trash2 size={13} /> 退出登录
        </Button>
        <Button
          variant="danger"
          size="sm"
          disabled={!st.has_credentials}
          onClick={async () => {
            if (!confirm("删除本机加密保存的清华账号密码？")) return;
            await api.del("/api/tsinghua/credentials");
            void load();
          }}
        >
          删除账号密码
        </Button>
      </div>

      {checks && (
        <table className="w-full text-left text-xs">
          <tbody>
            {checks.map((c) => (
              <tr key={c.model} className="border-t border-slate-100">
                <td className="py-1.5 font-medium">{c.model}</td>
                <td className={c.ok ? "text-emerald-600" : "text-rose-600"}>{c.ok ? `可用：${c.reply}` : c.error}</td>
                <td className="text-right text-slate-400">{c.ms} ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!!st.history.length && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">最近的认证记录</summary>
          <ul className="mt-1 space-y-0.5">
            {st.history.map((h, i) => (
              <li key={i}>
                {fmt(h.at)} · <span className={h.outcome === "ok" ? "text-emerald-600" : "text-rose-500"}>{h.outcome}</span> · {h.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="text-slate-400">{label}</div>
      <div className="mt-0.5 font-medium text-slate-700">{value}</div>
    </div>
  );
}

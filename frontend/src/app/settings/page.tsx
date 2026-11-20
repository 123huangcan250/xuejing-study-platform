"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { LLMConfig, TierConfig, Usage } from "@/lib/types";
import { Button, Card, inputCls } from "@/components/ui";
import { TsinghuaPanel } from "@/components/settings/TsinghuaPanel";

// 2026-11-12 经真实流式调用验证；以设置页「检测模型可用性」的当前结果为准
const TSINGHUA_MODELS = ["DeepSeek-V4.1-Flash", "DeepSeek-R1-W8A8", "qwen3.8-27b"];

type TierKey = keyof LLMConfig["tiers"];

const TIER_INFO: Record<TierKey, { name: string; desc: string }> = {
  fast: { name: "轻量快速档", desc: "闪卡生成、概念速查等高频轻任务" },
  strong: { name: "强推理档", desc: "画布深度追问、汇总对比（后续：费曼评判、Boss 出题、微课堂）" },
  translate: { name: "翻译档", desc: "文档翻译与划词翻译。留空则依次使用「轻量快速」「强推理」档；推荐选响应快的非推理模型" },
  vision: { name: "视觉解析档", desc: "扫描版 PDF / 公式截图解析（Phase 4 启用）" },
  embedding: { name: "向量模型", desc: "语义检索；留空则只用 FTS5 关键词检索（DeepSeek 等不提供向量接口时可单独配置其他服务商）" },
};

const PRESETS: { name: string; base_url: string; fast: string; strong: string; embedding: string }[] = [
  { name: "OpenAI", base_url: "https://api.openai.com/v1", fast: "gpt-4o-mini", strong: "gpt-4o", embedding: "text-embedding-3-small" },
  { name: "DeepSeek", base_url: "https://api.deepseek.com/v1", fast: "deepseek-chat", strong: "deepseek-reasoner", embedding: "" },
  { name: "OpenRouter", base_url: "https://openrouter.ai/api/v1", fast: "google/gemini-2.5-flash", strong: "anthropic/claude-sonnet-4.5", embedding: "" },
  { name: "硅基流动", base_url: "https://api.siliconflow.cn/v1", fast: "Qwen/Qwen2.5-7B-Instruct", strong: "deepseek-ai/DeepSeek-V3", embedding: "BAAI/bge-m3" },
  { name: "Gemini", base_url: "https://generativelanguage.googleapis.com/v1beta/openai", fast: "gemini-2.5-flash", strong: "gemini-2.5-pro", embedding: "text-embedding-004" },
];

export default function SettingsPage() {
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState<Record<string, { loading?: boolean; ok?: boolean; detail?: string }>>({});
  const [advanced, setAdvanced] = useState(false);

  const load = async () => {
    try {
      setCfg(await api.get<LLMConfig>("/api/settings"));
      setUsage(await api.get<Usage>("/api/usage"));
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  useEffect(() => {
    void load();
  }, []);

  if (!cfg) return <div className="p-10 text-center text-sm text-slate-400">加载中…</div>;

  const setTier = (t: TierKey, patch: Partial<TierConfig>) => setCfg({ ...cfg, tiers: { ...cfg.tiers, [t]: { ...cfg.tiers[t], ...patch } } });

  const save = async () => {
    setSaving(true);
    try {
      setCfg(await api.put<LLMConfig>("/api/settings", cfg));
      toast.success("已保存（密钥只存在本机 backend/data/app.db）");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const test = async (t: TierKey) => {
    await save();
    setTests((s) => ({ ...s, [t]: { loading: true } }));
    const r = await api.post<{ ok: boolean; detail: string }>(`/api/settings/test/${t}`).catch((e) => ({ ok: false, detail: (e as Error).message }));
    setTests((s) => ({ ...s, [t]: r }));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-xl font-bold text-slate-800">模型与 API 设置</h1>
        <p className="text-sm text-slate-500">支持任意 OpenAI 兼容接口。三档路由：轻任务走便宜快的模型，深度推理走强模型。</p>
      </div>

      <Card className="space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500">快速填充：</span>
          <Button
            variant="soft"
            size="sm"
            onClick={() =>
              setCfg({
                ...cfg,
                tiers: {
                  ...cfg.tiers,
                  fast: { ...cfg.tiers.fast, provider: "tsinghua", model: "DeepSeek-V4.1-Flash" },
                  strong: { ...cfg.tiers.strong, provider: "tsinghua", model: "DeepSeek-R1-W8A8" },
                },
              })
            }
          >
            清华大模型
          </Button>
          {PRESETS.map((p) => (
            <Button
              key={p.name}
              variant="outline"
              size="sm"
              onClick={() =>
                setCfg({
                  ...cfg,
                  base_url: p.base_url,
                  tiers: {
                    ...cfg.tiers,
                    // 选服务商预设时把档位切回「OpenAI 兼容」，否则会把该服务的模型名发给清华接口
                    fast: { ...cfg.tiers.fast, provider: "openai", model: p.fast },
                    strong: { ...cfg.tiers.strong, provider: "openai", model: p.strong },
                    embedding: { ...cfg.tiers.embedding, provider: "openai", model: p.embedding },
                  },
                })
              }
            >
              {p.name}
            </Button>
          ))}
        </div>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">Base URL</span>
          <input className={inputCls} value={cfg.base_url} onChange={(e) => setCfg({ ...cfg, base_url: e.target.value })} placeholder="https://api.openai.com/v1" />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">API Key {cfg.has_api_key && <span className="text-xs font-normal text-emerald-600">（已保存：{cfg.api_key}，留空不修改）</span>}</span>
          <input
            className={inputCls}
            type="password"
            autoComplete="off"
            value={cfg.api_key.includes("•") ? "" : cfg.api_key}
            onChange={(e) => setCfg({ ...cfg, api_key: e.target.value })}
            placeholder={cfg.has_api_key ? "输入新 Key 以替换" : "sk-..."}
          />
        </label>
      </Card>

      <Card className="divide-y divide-slate-100">
        {(Object.keys(TIER_INFO) as TierKey[]).map((t) => (
          <div key={t} className="space-y-2 p-5">
            <div className="flex items-center gap-2">
              <div className="flex-1">
                <div className="text-sm font-semibold text-slate-800">{TIER_INFO[t].name}</div>
                <div className="text-xs text-slate-400">{TIER_INFO[t].desc}</div>
              </div>
              {tests[t] && (
                <span className={`flex items-center gap-1 text-xs ${tests[t].loading ? "text-slate-400" : tests[t].ok ? "text-emerald-600" : "text-rose-600"}`}>
                  {tests[t].loading ? <Loader2 size={13} className="animate-spin" /> : tests[t].ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                  <span className="max-w-[260px] truncate" title={tests[t].detail}>{tests[t].loading ? "测试中" : tests[t].detail}</span>
                </span>
              )}
              <Button variant="outline" size="sm" onClick={() => void test(t)} disabled={!cfg.tiers[t].model}>
                测试
              </Button>
            </div>
            <div className="grid gap-2 sm:grid-cols-[150px_1fr_110px_110px]">
              <select
                className={inputCls}
                value={cfg.tiers[t].provider ?? "openai"}
                disabled={t === "embedding" || t === "vision"}
                title={t === "embedding" || t === "vision" ? "清华服务只提供文本对话" : "服务商"}
                onChange={(e) => {
                  const provider = e.target.value as "openai" | "tsinghua";
                  setTier(t, provider === "tsinghua" ? { provider, model: TSINGHUA_MODELS[0], price_in: 0, price_out: 0 } : { provider });
                }}
              >
                <option value="openai">OpenAI 兼容</option>
                <option value="tsinghua">清华大模型</option>
              </select>
              {cfg.tiers[t].provider === "tsinghua" ? (
                <select className={inputCls} value={cfg.tiers[t].model} onChange={(e) => setTier(t, { model: e.target.value })}>
                  {!TSINGHUA_MODELS.includes(cfg.tiers[t].model) && <option value={cfg.tiers[t].model}>{cfg.tiers[t].model}</option>}
                  {TSINGHUA_MODELS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              ) : (
                <input className={inputCls} value={cfg.tiers[t].model} onChange={(e) => setTier(t, { model: e.target.value })} placeholder={t === "embedding" ? "留空 = 不启用向量检索" : t === "translate" ? "留空 = 翻译时依次用轻量快速 / 强推理档" : "模型名"} />
              )}
              <input className={inputCls} type="number" step="0.01" value={cfg.tiers[t].price_in} onChange={(e) => setTier(t, { price_in: Number(e.target.value) })} title="输入价格 / 百万 token" />
              <input className={inputCls} type="number" step="0.01" value={cfg.tiers[t].price_out} onChange={(e) => setTier(t, { price_out: Number(e.target.value) })} title="输出价格 / 百万 token" />
            </div>
            {advanced && cfg.tiers[t].provider !== "tsinghua" && (
              <div className="grid gap-2 sm:grid-cols-2">
                <input className={inputCls} value={cfg.tiers[t].base_url} onChange={(e) => setTier(t, { base_url: e.target.value })} placeholder="覆盖 Base URL（留空用全局）" />
                <input
                  className={inputCls}
                  type="password"
                  autoComplete="off"
                  value={cfg.tiers[t].api_key.includes("•") ? "" : cfg.tiers[t].api_key}
                  onChange={(e) => setTier(t, { api_key: e.target.value })}
                  placeholder={cfg.tiers[t].api_key ? `已保存 ${cfg.tiers[t].api_key}` : "覆盖 API Key（留空用全局）"}
                />
              </div>
            )}
          </div>
        ))}
        <div className="flex items-center gap-3 p-4">
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            <input type="checkbox" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
            按档位单独配置服务商（例如对话用 DeepSeek、向量用硅基流动）
          </label>
          <span className="text-xs text-slate-400">价格单位：{cfg.currency} / 百万 token</span>
          <Button className="ml-auto" onClick={() => void save()} disabled={saving}>
            保存设置
          </Button>
        </div>
      </Card>

      <TsinghuaPanel />

      {usage && (
        <Card className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold text-slate-800">本月用量 · {usage.month}</h2>
            <div className="text-sm text-slate-500">
              {usage.total_tokens.toLocaleString()} tokens · 约 {usage.total_cost} {usage.currency}
            </div>
          </div>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-slate-400">
              <tr>
                <th className="py-1.5">档位</th>
                <th>模型</th>
                <th className="text-right">调用</th>
                <th className="text-right">输入</th>
                <th className="text-right">输出</th>
                <th className="text-right">估算费用</th>
              </tr>
            </thead>
            <tbody>
              {usage.items.map((i) => (
                <tr key={`${i.tier}-${i.model}`} className="border-t border-slate-100 tabular-nums">
                  <td className="py-1.5">{TIER_INFO[i.tier as TierKey]?.name ?? i.tier}</td>
                  <td className="text-slate-500">{i.model}</td>
                  <td className="text-right">{i.calls}</td>
                  <td className="text-right">{i.prompt_tokens.toLocaleString()}</td>
                  <td className="text-right">{i.completion_tokens.toLocaleString()}</td>
                  <td className="text-right">{i.cost}</td>
                </tr>
              ))}
              {!usage.items.length && (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-xs text-slate-400">本月还没有调用</td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

"use client";
import { create } from "zustand";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import type { TBlock, TMode, TStatus } from "@/lib/types";

const MODE_KEY = "alp-trans-mode";

interface TransState {
  mode: TMode;
  /** documentId → 翻译状态 */
  status: Record<number, TStatus>;
  /** documentId → 页码 → 该页的块（含译文） */
  pages: Record<number, Record<number, TBlock[]>>;
  /** documentId → 因没有可用模型等原因无法自动翻译的提示 */
  blocked: Record<number, string>;

  init: () => void;
  setMode: (m: TMode) => void;
  loadStatus: (docId: number) => Promise<void>;
  /** 用户正在看这些页：取回译文；缺的页让后台优先翻译 */
  ensurePages: (docId: number, pages: number[]) => void;
  startAll: (docId: number) => Promise<void>;
  cancel: (docId: number) => Promise<void>;
  clear: (docId: number) => Promise<void>;
  retry: (docId: number) => void;
}

// 每个文档当前“被关注”的页，以及轮询定时器（不放进 state，避免无谓重渲染）
const wanted: Record<number, Set<number>> = {};
const requested: Record<number, Set<number>> = {};
const timers: Record<number, ReturnType<typeof setInterval>> = {};
let debounce: ReturnType<typeof setTimeout> | null = null;

export const useTranslation = create<TransState>((set, get) => {
  const fetchPages = async (docId: number) => {
    const pages = [...(wanted[docId] ?? [])];
    const r = await api.get<{ status: TStatus; pages: Record<string, TBlock[]> }>(
      `/api/documents/${docId}/translation?pages=${pages.join(",")}`,
    );
    set((s) => ({
      status: { ...s.status, [docId]: r.status },
      pages: {
        ...s.pages,
        [docId]: { ...s.pages[docId], ...Object.fromEntries(Object.entries(r.pages).map(([p, b]) => [Number(p), b])) },
      },
    }));
    return r;
  };

  const pendingIn = (docId: number) =>
    [...(wanted[docId] ?? [])].some((p) => (get().pages[docId]?.[p] ?? []).some((b) => b.translatable && !b.translation));

  const poll = (docId: number) => {
    if (timers[docId]) return;
    timers[docId] = setInterval(async () => {
      try {
        const r = await fetchPages(docId);
        // 任务结束（或出错）且没有可继续的内容，就停止轮询
        if (!r.status.running && (r.status.state === "error" || !pendingIn(docId))) {
          clearInterval(timers[docId]);
          delete timers[docId];
        }
      } catch {
        clearInterval(timers[docId]);
        delete timers[docId];
      }
    }, 1500);
  };

  const request = async (docId: number, pages: number[] | null, priority: boolean) => {
    try {
      const st = await api.post<TStatus>(`/api/documents/${docId}/translate`, { pages, priority });
      set((s) => ({ status: { ...s.status, [docId]: st }, blocked: { ...s.blocked, [docId]: "" } }));
      poll(docId);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "无法启动翻译";
      set((s) => ({ blocked: { ...s.blocked, [docId]: msg } }));
    }
  };

  return {
    mode: "orig",
    status: {},
    pages: {},
    blocked: {},

    init: () => {
      try {
        const m = localStorage.getItem(MODE_KEY);
        if (m === "orig" || m === "both" || m === "trans") set({ mode: m });
      } catch {
        /* ignore */
      }
    },

    setMode: (mode) => {
      set({ mode });
      try {
        localStorage.setItem(MODE_KEY, mode);
      } catch {
        /* ignore */
      }
    },

    loadStatus: async (docId) => {
      try {
        const r = await api.get<{ status: TStatus }>(`/api/documents/${docId}/translation`);
        set((s) => ({ status: { ...s.status, [docId]: r.status } }));
        if (r.status.running) poll(docId);
      } catch {
        /* 后端未运行时静默 */
      }
    },

    ensurePages: (docId, pages) => {
      if (!pages.length) return;
      wanted[docId] = new Set(pages);
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(async () => {
        try {
          await fetchPages(docId);
        } catch {
          return;
        }
        const missing = [...(wanted[docId] ?? [])].filter(
          (p) => (get().pages[docId]?.[p] ?? []).some((b) => b.translatable && !b.translation) && !(requested[docId]?.has(p)),
        );
        if (missing.length) {
          requested[docId] ??= new Set();
          missing.forEach((p) => requested[docId].add(p));
          await request(docId, missing, true);
        } else if (pendingIn(docId) || get().status[docId]?.running) poll(docId);
      }, 250);
    },

    startAll: async (docId) => {
      await request(docId, null, false);
      const st = get().status[docId];
      if (st && !st.running && st.state === "done") toast("整份文档都已翻译完成");
    },

    cancel: async (docId) => {
      const st = await api.post<TStatus>(`/api/documents/${docId}/translate/cancel`);
      set((s) => ({ status: { ...s.status, [docId]: st } }));
    },

    clear: async (docId) => {
      await api.del(`/api/documents/${docId}/translation`);
      delete requested[docId];
      set((s) => ({ pages: { ...s.pages, [docId]: {} } }));
      await get().loadStatus(docId);
      if (wanted[docId]?.size) await fetchPages(docId);
    },

    // 出错或被拦截后重试：清掉“已请求过”的记录，重新请求当前关注的页
    retry: (docId) => {
      delete requested[docId];
      set((s) => ({ blocked: { ...s.blocked, [docId]: "" } }));
      get().ensurePages(docId, [...(wanted[docId] ?? [])]);
    },
  };
});

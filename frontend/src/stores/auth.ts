"use client";
import { create } from "zustand";
import { API_BASE, ApiError, api } from "@/lib/api";

interface AuthStatus {
  required: boolean;
  authenticated: boolean;
  blocked: boolean;
}

interface AuthState extends AuthStatus {
  ready: boolean;
  error: string;
  busy: boolean;
  check: () => Promise<void>;
  require: () => void;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set, get) => ({
  required: false,
  authenticated: true,
  blocked: false,
  ready: false,
  error: "",
  busy: false,

  check: async () => {
    try {
      const s = await api.get<AuthStatus>("/api/auth/status");
      set({ required: s.required, authenticated: s.authenticated, blocked: s.blocked, ready: true });
    } catch {
      // 后端没起来：不挡着页面，让各页面自己显示“无法连接后端”
      set({ ready: true });
    }
  },

  // 任何接口返回“需要登录”（例如 Cookie 过期）时调用
  require: () => set({ required: true, authenticated: false, ready: true }),

  login: async (password) => {
    set({ busy: true, error: "" });
    try {
      await api.post("/api/auth/login", { password });
      set({ authenticated: true, required: true, busy: false });
      return true;
    } catch (e) {
      set({ busy: false, error: e instanceof ApiError ? e.message : "登录失败" });
      return false;
    }
  },

  logout: async () => {
    await fetch(`${API_BASE}/api/auth/logout`, { method: "POST" }).catch(() => undefined);
    set({ authenticated: false });
    void get().check();
  },
}));

"use client";
import { create } from "zustand";
import { DEFAULT_APPEARANCE, FONTS, READ_FONTS, STORAGE_KEY, THEMES, RADII, SIZES, PDF_INVERT, resolveTheme, type Appearance, type Mode } from "@/lib/appearance";

interface AppearanceState extends Appearance {
  /** 实际生效的主题 id（theme 为 "auto" 时由系统明暗决定） */
  resolvedTheme: string;
  mode: Mode;
  ready: boolean;
  init: () => void;
  set: (patch: Partial<Appearance>) => void;
  reset: () => void;
}

const valid = <T extends { id: string }>(list: T[], id: unknown, fallback: string) =>
  typeof id === "string" && list.some((x) => x.id === id) ? id : fallback;

function sanitize(raw: Partial<Appearance> | null): Appearance {
  const d = DEFAULT_APPEARANCE;
  const r = raw ?? {};
  return {
    theme: r.theme === "auto" ? "auto" : valid(THEMES, r.theme, d.theme),
    uiFont: valid(FONTS, r.uiFont, d.uiFont),
    readFont: valid(READ_FONTS, r.readFont, d.readFont),
    size: valid(SIZES, r.size, d.size) as Appearance["size"],
    radius: valid(RADII, r.radius, d.radius) as Appearance["radius"],
    pdfInvert: valid(PDF_INVERT, r.pdfInvert, d.pdfInvert) as Appearance["pdfInvert"],
  };
}

function load(): Appearance {
  try {
    return sanitize(JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"));
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

const systemDark = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches;

/** 把外观写到 <html> 的 data-* 属性上，CSS 据此切换颜色 / 字体 / 字号 / 圆角。 */
function apply(a: Appearance, animate: boolean) {
  const t = resolveTheme(a.theme, systemDark());
  const h = document.documentElement;
  if (animate) {
    h.classList.add("theme-anim");
    window.setTimeout(() => h.classList.remove("theme-anim"), 350);
  }
  h.dataset.theme = t.id;
  h.dataset.mode = t.mode;
  h.dataset.fontUi = a.uiFont;
  h.dataset.fontRead = a.readFont;
  h.dataset.size = a.size;
  h.dataset.radius = a.radius;
  h.dataset.pdf = a.pdfInvert === "on" || (a.pdfInvert === "auto" && t.mode === "dark") ? "inv" : "norm";
  return t;
}

export const useAppearance = create<AppearanceState>((set, get) => ({
  ...DEFAULT_APPEARANCE,
  resolvedTheme: DEFAULT_APPEARANCE.theme,
  mode: "light",
  ready: false,

  init: () => {
    if (get().ready) return;
    const a = load();
    const t = apply(a, false);
    set({ ...a, resolvedTheme: t.id, mode: t.mode, ready: true });
    // “跟随系统”时，系统明暗变化要实时跟上
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      const cur = get();
      if (cur.theme !== "auto") return;
      const t2 = apply(cur, true);
      set({ resolvedTheme: t2.id, mode: t2.mode });
    });
  },

  set: (patch) => {
    const next = sanitize({ ...pick(get()), ...patch });
    const t = apply(next, patch.theme !== undefined);
    set({ ...next, resolvedTheme: t.id, mode: t.mode });
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* 隐私模式等情况下无法持久化，本次会话仍然生效 */
    }
  },

  reset: () => get().set({ ...DEFAULT_APPEARANCE }),
}));

function pick(s: Appearance): Appearance {
  return { theme: s.theme, uiFont: s.uiFont, readFont: s.readFont, size: s.size, radius: s.radius, pdfInvert: s.pdfInvert };
}

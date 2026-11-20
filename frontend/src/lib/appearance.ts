/**
 * 外观系统的元数据。实际的颜色/字体定义在 globals.css（按 data-* 属性切换），
 * 这里只描述“有哪些选项”以及用于预览的静态色块；首屏防闪脚本也由这里生成，避免两份定义对不上。
 */

export type Mode = "light" | "dark";

export interface ThemeDef {
  id: string;
  name: string;
  desc: string;
  mode: Mode;
  /** 仅用于设置菜单里的预览色块（不影响实际渲染） */
  preview: { page: string; surface: string; accent: string; accent2: string; text: string };
}

export const THEMES: ThemeDef[] = [
  { id: "indigo", name: "靛蓝", desc: "默认 · 清爽专注", mode: "light", preview: { page: "#f4f5fb", surface: "#ffffff", accent: "#4f46e5", accent2: "#8b5cf6", text: "#1e293b" } },
  { id: "ocean", name: "海盐", desc: "冷静的蓝青色", mode: "light", preview: { page: "#f1f7fa", surface: "#ffffff", accent: "#0284c7", accent2: "#06b6d4", text: "#16323f" } },
  { id: "forest", name: "森林", desc: "护眼的绿", mode: "light", preview: { page: "#f2f7f3", surface: "#fdfefd", accent: "#059669", accent2: "#65a30d", text: "#1a3226" } },
  { id: "sakura", name: "樱粉", desc: "柔和的粉玫瑰", mode: "light", preview: { page: "#fbf3f6", surface: "#fffdfe", accent: "#db2777", accent2: "#c026d3", text: "#3b1a2a" } },
  { id: "sunset", name: "落日", desc: "温暖的橙", mode: "light", preview: { page: "#fbf5ef", surface: "#fffdfa", accent: "#ea580c", accent2: "#e11d48", text: "#3a2412" } },
  { id: "paper", name: "纸墨", desc: "米黄纸张 · 适合长时间阅读", mode: "light", preview: { page: "#f3ecdd", surface: "#fbf7ec", accent: "#9a5b13", accent2: "#b45309", text: "#3b3226" } },
  { id: "midnight", name: "暗夜", desc: "深色 · 靛蓝点缀", mode: "dark", preview: { page: "#0f1117", surface: "#181b24", accent: "#6366f1", accent2: "#a78bfa", text: "#e5e7ee" } },
  { id: "deepsea", name: "深海", desc: "深色 · 青蓝点缀", mode: "dark", preview: { page: "#0b1418", surface: "#121e24", accent: "#0ea5e9", accent2: "#2dd4bf", text: "#dfeaee" } },
  { id: "graphite", name: "石墨", desc: "深色 · 琥珀点缀", mode: "dark", preview: { page: "#141311", surface: "#1d1b18", accent: "#f59e0b", accent2: "#f97316", text: "#ece7df" } },
];

export const AUTO_THEME = { id: "auto", name: "跟随系统", desc: "按系统明暗在「靛蓝」与「暗夜」间切换" };
export const AUTO_LIGHT = "indigo";
export const AUTO_DARK = "midnight";

export interface FontDef {
  id: string;
  name: string;
  desc: string;
  /** 预览用的 font-family（与 globals.css 中的 --f-* 保持一致） */
  stack: string;
}

export const FONTS: FontDef[] = [
  { id: "sans", name: "现代黑体", desc: "Inter + 思源黑体 · 默认", stack: '"Inter Variable","Noto Sans SC Variable",system-ui,sans-serif' },
  { id: "system", name: "系统字体", desc: "微软雅黑 / 苹方 · 无需加载", stack: 'system-ui,"Segoe UI","PingFang SC","Microsoft YaHei UI","Microsoft YaHei",sans-serif' },
  { id: "serif", name: "思源宋体", desc: "书卷气 · 适合阅读", stack: '"Noto Serif SC Variable","Songti SC",SimSun,serif' },
  { id: "wenkai", name: "霞鹜文楷", desc: "温润的楷体 · 适合做笔记", stack: '"LXGW WenKai","Kaiti SC",KaiTi,serif' },
  { id: "kuaile", name: "站酷快乐体", desc: "活泼圆润 · 标题感强", stack: '"ZCOOL KuaiLe","Noto Sans SC Variable",system-ui,sans-serif' },
  { id: "mono", name: "等宽字体", desc: "JetBrains Mono · 代码风", stack: '"JetBrains Mono","Noto Sans SC Variable",ui-monospace,monospace' },
];

/** 阅读字体比界面字体多一个“跟随界面” */
export const READ_FONTS: FontDef[] = [
  { id: "ui", name: "跟随界面", desc: "与界面字体相同", stack: "inherit" },
  ...FONTS,
];

export interface Choice<T extends string> {
  id: T;
  name: string;
}

export type SizeId = "sm" | "md" | "lg" | "xl";
export const SIZES: Choice<SizeId>[] = [
  { id: "sm", name: "小" },
  { id: "md", name: "标准" },
  { id: "lg", name: "大" },
  { id: "xl", name: "特大" },
];

export type RadiusId = "sharp" | "md" | "round";
export const RADII: Choice<RadiusId>[] = [
  { id: "sharp", name: "方正" },
  { id: "md", name: "标准" },
  { id: "round", name: "圆润" },
];

export type PdfInvertId = "auto" | "on" | "off";
export const PDF_INVERT: Choice<PdfInvertId>[] = [
  { id: "auto", name: "跟随主题" },
  { id: "on", name: "始终反色" },
  { id: "off", name: "不反色" },
];

export interface Appearance {
  theme: string; // THEMES[].id 或 "auto"
  uiFont: string;
  readFont: string;
  size: SizeId;
  radius: RadiusId;
  pdfInvert: PdfInvertId;
}

export const DEFAULT_APPEARANCE: Appearance = {
  theme: "indigo",
  uiFont: "sans",
  readFont: "ui",
  size: "md",
  radius: "md",
  pdfInvert: "auto",
};

export const STORAGE_KEY = "alp-appearance";

export function resolveTheme(theme: string, systemDark: boolean): ThemeDef {
  const id = theme === "auto" ? (systemDark ? AUTO_DARK : AUTO_LIGHT) : theme;
  return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

/**
 * 首屏防闪脚本：在 React 水合之前就把 data-* 属性设好，避免先闪一下默认主题。
 * 内容只包含本文件里的常量，不含任何用户输入。
 */
export function bootScript(): string {
  const dark = Object.fromEntries(THEMES.filter((t) => t.mode === "dark").map((t) => [t.id, 1]));
  const d = DEFAULT_APPEARANCE;
  return `(function(){try{
var s=JSON.parse(localStorage.getItem(${JSON.stringify(STORAGE_KEY)})||"{}"),h=document.documentElement,D=${JSON.stringify(dark)};
var t=s.theme||${JSON.stringify(d.theme)};
if(t==="auto")t=matchMedia("(prefers-color-scheme: dark)").matches?${JSON.stringify(AUTO_DARK)}:${JSON.stringify(AUTO_LIGHT)};
var dk=!!D[t],p=s.pdfInvert||${JSON.stringify(d.pdfInvert)};
h.dataset.theme=t;h.dataset.mode=dk?"dark":"light";
h.dataset.fontUi=s.uiFont||${JSON.stringify(d.uiFont)};h.dataset.fontRead=s.readFont||${JSON.stringify(d.readFont)};
h.dataset.size=s.size||${JSON.stringify(d.size)};h.dataset.radius=s.radius||${JSON.stringify(d.radius)};
h.dataset.pdf=(p==="on"||(p==="auto"&&dk))?"inv":"norm";
}catch(e){}})();`;
}

"use client";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Check, Palette, RotateCcw } from "lucide-react";
import { useAppearance } from "@/stores/appearance";
import { AUTO_THEME, FONTS, PDF_INVERT, RADII, READ_FONTS, SIZES, THEMES, type FontDef, type ThemeDef } from "@/lib/appearance";

function Swatch({ t }: { t: ThemeDef }) {
  const p = t.preview;
  return (
    <div className="h-12 overflow-hidden rounded-lg border border-black/5" style={{ background: p.page }}>
      <div className="m-1.5 h-9 rounded-md p-1.5 shadow-sm" style={{ background: p.surface }}>
        <div className="h-1.5 w-8 rounded-full" style={{ background: p.accent }} />
        <div className="mt-1 h-1 w-12 rounded-full" style={{ background: p.text, opacity: 0.25 }} />
        <div className="mt-1.5 flex gap-1">
          <span className="h-1.5 w-3 rounded-full" style={{ background: p.accent }} />
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: p.accent2 }} />
        </div>
      </div>
    </div>
  );
}

function AutoSwatch() {
  const light = THEMES[0].preview;
  const dark = THEMES.find((t) => t.id === "midnight")!.preview;
  return (
    <div className="h-12 overflow-hidden rounded-lg border border-black/5" style={{ background: `linear-gradient(135deg, ${light.page} 50%, ${dark.page} 50%)` }}>
      <div className="flex h-full items-center justify-center gap-1.5">
        <span className="h-4 w-4 rounded-full" style={{ background: light.accent }} />
        <span className="h-4 w-4 rounded-full" style={{ background: dark.accent }} />
      </div>
    </div>
  );
}

function Seg<T extends string>({ value, options, onChange }: { value: T; options: { id: T; name: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg bg-slate-100 p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          className={clsx("rounded-md px-2.5 py-1 transition", value === o.id ? "bg-surface font-medium text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700")}
        >
          {o.name}
        </button>
      ))}
    </div>
  );
}

function FontGrid({ fonts, value, onPick }: { fonts: FontDef[]; value: string; onPick: (id: string) => void }) {
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {fonts.map((f) => {
        const active = f.id === value;
        return (
          <button
            key={f.id}
            onClick={() => onPick(f.id)}
            className={clsx("rounded-xl border px-3 py-2 text-left transition", active ? "border-indigo-400 bg-indigo-50" : "border-slate-200 hover:bg-slate-50")}
          >
            <div className="flex items-center gap-1 text-sm text-slate-800" style={{ fontFamily: f.stack === "inherit" ? undefined : f.stack }}>
              <span className="truncate">{f.name}</span>
              {active && <Check size={13} className="shrink-0 text-indigo-600" />}
            </div>
            <div className="mt-0.5 truncate text-[10.5px] text-slate-400">{f.desc}</div>
            {f.stack !== "inherit" && (
              <div className="mt-1 truncate text-[11px] text-slate-500" style={{ fontFamily: f.stack }}>
                知识的冒险 Aa 123
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-baseline gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</h4>
        {hint && <span className="text-[10.5px] text-slate-400">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

/** 顶栏的「外观」菜单：换肤、界面字体、阅读字体、字号、圆角、PDF 夜间反色。 */
export function AppearanceMenu() {
  const a = useAppearance();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const themeBtn = (id: string, name: string, desc: string, swatch: React.ReactNode) => {
    const active = a.theme === id;
    return (
      <button
        key={id}
        onClick={() => a.set({ theme: id })}
        className={clsx("rounded-xl p-1.5 text-left transition", active ? "bg-indigo-50 ring-2 ring-indigo-500" : "hover:bg-slate-100")}
      >
        {swatch}
        <div className="mt-1 flex items-center gap-1 text-xs font-medium text-slate-700">
          {name}
          {active && <Check size={12} className="text-indigo-600" />}
        </div>
        <div className="line-clamp-1 text-[10px] text-slate-400">{desc}</div>
      </button>
    );
  };

  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={clsx("rounded-md p-1.5 transition", open ? "bg-indigo-50 text-indigo-600" : "text-slate-400 hover:bg-slate-100 hover:text-slate-600")}
        title="外观：主题、字体、字号"
        aria-label="外观设置"
        aria-expanded={open}
      >
        <Palette size={17} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 max-h-[calc(100vh-5rem)] w-[min(92vw,420px)] space-y-5 overflow-y-auto rounded-2xl border border-slate-200 bg-surface p-4 shadow-2xl scroll-thin">
          <Section title="主题" hint={a.theme === "auto" ? `当前：${THEMES.find((t) => t.id === a.resolvedTheme)?.name}` : undefined}>
            <div className="grid grid-cols-3 gap-1.5">
              {THEMES.map((t) => themeBtn(t.id, t.name, t.desc, <Swatch t={t} />))}
              {themeBtn(AUTO_THEME.id, AUTO_THEME.name, AUTO_THEME.desc, <AutoSwatch />)}
            </div>
          </Section>
          <Section title="界面字体" hint="菜单、按钮、标题">
            <FontGrid fonts={FONTS} value={a.uiFont} onPick={(id) => a.set({ uiFont: id })} />
          </Section>
          <Section title="阅读字体" hint="AI 回答、笔记、闪卡正文">
            <FontGrid fonts={READ_FONTS} value={a.readFont} onPick={(id) => a.set({ readFont: id })} />
          </Section>
          <div className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-3">
            <span className="text-xs text-slate-500">字号</span>
            <Seg value={a.size} options={SIZES} onChange={(size) => a.set({ size })} />
            <span className="text-xs text-slate-500">圆角</span>
            <Seg value={a.radius} options={RADII} onChange={(radius) => a.set({ radius })} />
            <span className="text-xs text-slate-500" title="深色主题下把 PDF 页面反色成夜间阅读">
              PDF 夜间
            </span>
            <Seg value={a.pdfInvert} options={PDF_INVERT} onChange={(pdfInvert) => a.set({ pdfInvert })} />
          </div>
          <div className="flex items-center justify-between border-t border-slate-100 pt-3 text-[11px] text-slate-400">
            <span>设置保存在本浏览器</span>
            <button onClick={a.reset} className="flex items-center gap-1 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100">
              <RotateCcw size={12} /> 恢复默认
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

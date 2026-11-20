"use client";
import confetti from "canvas-confetti";

const MUTE_KEY = "alp-muted";

export function isMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setMuted(m: boolean) {
  try {
    localStorage.setItem(MUTE_KEY, m ? "1" : "0");
  } catch {
    /* ignore */
  }
}

let ctx: AudioContext | null = null;

/** Web Audio 合成的轻音效，无需音频文件。 */
export function beep(kind: "good" | "bad" | "flip" | "level") {
  if (isMuted() || typeof window === "undefined") return;
  try {
    ctx ??= new AudioContext();
    const notes: Record<string, number[]> = {
      good: [660, 880],
      bad: [220, 180],
      flip: [520],
      level: [523, 659, 784, 1047],
    };
    notes[kind].forEach((f, i) => {
      const o = ctx!.createOscillator();
      const g = ctx!.createGain();
      o.type = kind === "bad" ? "sawtooth" : "sine";
      o.frequency.value = f;
      const t = ctx!.currentTime + i * 0.08;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.08, t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
      o.connect(g).connect(ctx!.destination);
      o.start(t);
      o.stop(t + 0.2);
    });
  } catch {
    /* 浏览器不支持时忽略 */
  }
}

/** 把任意 CSS 颜色（含 oklch / var()）转成 #rrggbb：先让浏览器算出最终色，再用 1 像素画布取像素。 */
function cssColorToHex(css: string): string | null {
  try {
    const el = document.createElement("span");
    el.style.color = css;
    document.body.appendChild(el);
    const resolved = getComputedStyle(el).color;
    el.remove();
    const c = document.createElement("canvas");
    c.width = c.height = 1;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.fillStyle = resolved;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
  } catch {
    return null;
  }
}

/** 当前主题的庆祝色板（主色 / 辅色 / 点缀色），取不到时退回固定色。 */
function themeColors(): string[] {
  const vars = ["--color-indigo-400", "--color-indigo-500", "--color-violet-400", "--color-amber-400", "--color-emerald-400"];
  const got = vars.map((v) => cssColorToHex(`var(${v})`)).filter((x): x is string => !!x);
  return got.length >= 3 ? got : ["#6366f1", "#8b5cf6", "#f59e0b", "#10b981"];
}

export function celebrate(kind: "level" | "achievement" | "streak") {
  if (typeof window === "undefined") return;
  beep("level");
  const colors = themeColors();
  const opts =
    kind === "level"
      ? { particleCount: 160, spread: 90, origin: { y: 0.6 }, colors }
      : kind === "streak"
        ? { particleCount: 80, spread: 60, origin: { y: 0.2 }, colors: ["#f97316", "#facc15", "#ef4444"] } // 火焰色，固定
        : { particleCount: 60, spread: 70, origin: { y: 0.3 }, colors };
  confetti(opts);
}

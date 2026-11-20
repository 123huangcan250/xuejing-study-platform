"use client";
import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";
import clsx from "clsx";
import { X } from "lucide-react";

type Variant = "primary" | "ghost" | "outline" | "danger" | "soft";

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-indigo-600 text-white hover:bg-indigo-500 shadow-sm",
        variant === "soft" && "bg-indigo-50 text-indigo-700 hover:bg-indigo-100",
        variant === "ghost" && "text-slate-600 hover:bg-slate-100",
        variant === "outline" && "border border-slate-200 bg-surface text-slate-700 hover:bg-slate-50",
        variant === "danger" && "text-rose-600 hover:bg-rose-50",
        className,
      )}
    />
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" onMouseDown={onClose}>
      <div
        className={clsx("w-full rounded-2xl bg-surface shadow-2xl max-h-[90vh] flex flex-col", width)}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-3.5">
          <h3 className="font-semibold text-slate-800">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100" aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <div className="overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("rounded-2xl border border-slate-200/80 bg-surface shadow-sm", className)}>{children}</div>;
}

export function ProgressBar({ value, max, className, color = "bg-indigo-500" }: { value: number; max: number; className?: string; color?: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className={clsx("h-2 w-full overflow-hidden rounded-full bg-slate-100", className)}>
      <div className={clsx("h-full rounded-full transition-all duration-700", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export const inputCls =
  "w-full rounded-lg border border-slate-200 bg-surface px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100";

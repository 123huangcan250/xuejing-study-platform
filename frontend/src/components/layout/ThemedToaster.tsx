"use client";
import { useEffect } from "react";
import { Toaster } from "sonner";
import { useAppearance } from "@/stores/appearance";

/** 提示条跟随主题明暗；同时在这里初始化外观（读取 localStorage、监听系统明暗变化）。 */
export function ThemedToaster() {
  const mode = useAppearance((s) => s.mode);
  const init = useAppearance((s) => s.init);
  useEffect(() => init(), [init]);
  return <Toaster position="bottom-right" richColors closeButton theme={mode} />;
}

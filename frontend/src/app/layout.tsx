import type { Metadata } from "next";
// 字体：全部自托管（npm 包），按 unicode-range 分片，只有页面实际用到的字才会下载
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-sc";
import "@fontsource-variable/noto-serif-sc";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "lxgw-wenkai-webfont/lxgwwenkai-regular.css";
import "lxgw-wenkai-webfont/lxgwwenkai-bold.css";
import "@fontsource/zcool-kuaile";
import "katex/dist/katex.min.css";
import "@xyflow/react/dist/style.css";
import "./globals.css";
import { AppHeader } from "@/components/layout/AppHeader";
import { ThemedToaster } from "@/components/layout/ThemedToaster";
import { AuthGate } from "@/components/layout/AuthGate";
import { bootScript } from "@/lib/appearance";

export const metadata: Metadata = {
  title: "学境 · AI 课程学习与游戏化复习",
  description: "以课程世界为中心的阅读、非线性探索画布、FSRS 复习与游戏化激励平台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // data-* 属性由首屏脚本在水合前写入（见 lib/appearance.ts），所以 html 需要 suppressHydrationWarning
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: bootScript() }} />
      </head>
      <body className="flex h-screen flex-col overflow-hidden antialiased">
        <AppHeader />
        <main className="min-h-0 flex-1 overflow-auto">
          <AuthGate>{children}</AuthGate>
        </main>
        <ThemedToaster />
      </body>
    </html>
  );
}

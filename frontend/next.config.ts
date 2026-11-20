import type { NextConfig } from "next";

/**
 * 单一入口：浏览器只访问前端（3400），/api/* 由 src/middleware.ts 转发给只监听本机的后端。
 * 这样远程访问（Tailscale Serve → 3400）只需要暴露一个端口，后端和它的密钥永远不直接对外。
 */
const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR || ".next", // 测试时可以构建到别的目录，不影响正在运行的开发服务器
  compress: false, // 不压缩，避免代理缓冲 SSE 流式回答
  experimental: {
    proxyTimeout: 10 * 60 * 1000, // 默认 30 秒会截断慢模型的回答、整份翻译导出等长请求
  },
};

export default nextConfig;

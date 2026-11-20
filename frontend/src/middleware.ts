import { NextResponse, type NextRequest } from "next/server";

/**
 * /api/* 转发给只监听本机的后端。
 * Next 自带的 rewrites 会把 Host 头改成后端地址，后端就分不清请求是从本机还是远程来的；
 * 这里改为在中间件里转发，并把浏览器看到的原始 Host 写进 x-alp-host（每次都覆盖，客户端伪造的会被丢弃）。
 */
const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8400";

export function middleware(req: NextRequest) {
  const headers = new Headers(req.headers);
  headers.set("x-alp-host", req.headers.get("host") ?? "");
  return NextResponse.rewrite(new URL(req.nextUrl.pathname + req.nextUrl.search, BACKEND), { request: { headers } });
}

export const config = { matcher: "/api/:path*" };

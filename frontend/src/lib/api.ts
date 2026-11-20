// 默认同源（由 next.config.ts 把 /api 转发给后端），所以远程访问时也能工作；需要直连别的后端时再设 NEXT_PUBLIC_API_BASE
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "").replace(/\/$/, "");

/** 后端要求登录时发出的事件，登录门（AuthGate）监听它 */
export const AUTH_EVENT = "alp-auth-required";

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(`无法连接后端，请确认已运行 scripts/dev.ps1（远程访问时请确认宿舍电脑在线）`, 0);
  }
  if (!res.ok) {
    let msg = res.statusText;
    let code = "";
    try {
      const j = await res.json();
      code = typeof j.code === "string" ? j.code : "";
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* 非 JSON 错误体 */
    }
    // 只有后端明确说“需要登录”才弹登录门（云盘的“需要提取码”也是 401，不能混淆）
    if (res.status === 401 && code === "auth_required" && typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
    throw new ApiError(msg, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export const fileUrl = (documentId: number) => `${API_BASE}/api/documents/${documentId}/file`;

export interface SSEHandlers {
  onEvent: (e: { type: string; [k: string]: unknown }) => void;
  signal?: AbortSignal;
}

/** POST + text/event-stream 读取（EventSource 不支持 POST，所以手动解析）。 */
export async function postSSE(path: string, { onEvent, signal }: SSEHandlers): Promise<void> {
  const res = await fetch(API_BASE + path, { method: "POST", signal });
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    let code = "";
    try {
      const j = await res.json();
      msg = j.detail ?? msg;
      code = typeof j.code === "string" ? j.code : "";
    } catch {
      /* ignore */
    }
    if (res.status === 401 && code === "auth_required" && typeof window !== "undefined") window.dispatchEvent(new Event(AUTH_EVENT));
    throw new ApiError(msg, res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data: "));
      if (line) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch {
          /* 忽略坏帧 */
        }
      }
    }
  }
}

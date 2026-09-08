const DEFAULT_HANSER_API_BASE_URL = "http://127.0.0.1:8765";

export const hanserUserId = process.env.HANSER_USER_ID ?? "local-user";

export function hanserApiUrl(path: string): URL {
  const baseUrl =
    process.env.HANSER_API_BASE_URL ?? DEFAULT_HANSER_API_BASE_URL;
  return new URL(path.replace(/^\//, ""), `${baseUrl.replace(/\/$/, "")}/`);
}

export function hanserFetch(path: string, init?: RequestInit) {
  return fetch(hanserApiUrl(path), {
    ...init,
    cache: "no-store",
  });
}

export async function readHanserError(response: Response): Promise<string> {
  const payload = (await response.json().catch(() => null)) as {
    detail?: string | { message?: string };
  } | null;
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }
  if (payload?.detail && typeof payload.detail.message === "string") {
    return payload.detail.message;
  }
  return `Hanser 后端请求失败（HTTP ${response.status}）`;
}

export async function hanserProxyError(response: Response) {
  const cause = await readHanserError(response);
  return Response.json(
    {
      cause,
      code: response.status >= 500 ? "offline:chat" : "bad_request:api",
    },
    { status: response.status }
  );
}

export type HanserChatResponse = {
  text: string;
  request_id?: string;
  trace_id?: string;
  status?: "ok" | "degraded";
  degraded_reasons?: string[];
  sources?: unknown[];
};

export type HanserConversation = {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type HanserConversationDetail = HanserConversation & {
  messages: Array<{
    id: string;
    role: "user" | "assistant" | "system";
    content: string;
    created_at: string;
    turn_index: number;
  }>;
};

export type ErrorCode = "bad_request:api" | "offline:chat";

const fallbackMessages: Record<ErrorCode, string> = {
  "bad_request:api": "请求内容无法处理，请检查后重试。",
  "offline:chat": "无法连接 Hanser 后端，请确认桌面服务已经启动。",
};

export class ChatbotError extends Error {
  readonly code: ErrorCode;

  constructor(code: ErrorCode, cause?: string) {
    super(cause || fallbackMessages[code]);
    this.code = code;
  }
}

import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  generateId,
} from "ai";
import {
  type HanserChatResponse,
  hanserFetch,
  hanserUserId,
  readHanserError,
} from "@/lib/hanser-client";
import type { ChatMessage } from "@/lib/types";
import { postRequestBodySchema } from "./schema";

export const maxDuration = 600;

function textFromParts(parts: Array<Record<string, unknown>>) {
  return parts
    .filter((part) => part.type === "text" && typeof part.text === "string")
    .map((part) => String(part.text))
    .join("")
    .trim();
}

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const parsed = postRequestBodySchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { cause: "当前桌面版只接受文字消息。", code: "bad_request:api" },
      { status: 400 }
    );
  }

  const sourceMessage =
    parsed.data.message ??
    [...(parsed.data.messages ?? [])]
      .reverse()
      .find((message) => message.role === "user");
  const text = sourceMessage ? textFromParts(sourceMessage.parts) : "";
  if (!sourceMessage || !text) {
    return Response.json(
      { cause: "没有可重试的文字消息。", code: "bad_request:api" },
      { status: 400 }
    );
  }

  const stream = createUIMessageStream<ChatMessage>({
    execute: async ({ writer }) => {
      writer.write({
        data: {
          message: "Hanser 正在组织回复…",
          modelId: "hanser/agent",
          modelName: "Hanser Agent",
          phase: "waiting",
        },
        transient: true,
        type: "data-waiting-status",
      });

      const response = await hanserFetch("/v1/chat", {
        body: JSON.stringify({
          conversation_id: parsed.data.id,
          message: text,
          persona_settings: {
            adult_innuendo_opt_in:
              parsed.data.personaSettings?.adult_innuendo_opt_in ?? false,
          },
          request_id: sourceMessage.id,
          user_id: hanserUserId,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
        signal: request.signal,
      });
      if (!response.ok) {
        throw new Error(await readHanserError(response));
      }

      const result = (await response.json()) as HanserChatResponse;
      const textPartId = generateId();
      writer.write({ id: textPartId, type: "text-start" });
      writer.write({ delta: result.text, id: textPartId, type: "text-delta" });
      writer.write({ id: textPartId, type: "text-end" });
      writer.write({
        data: {
          degradedReasons: result.degraded_reasons ?? [],
          postTurnRetryId: result.post_turn_retry_id,
          postTurnStatus: result.post_turn_status ?? "completed",
          replyId: result.reply_id,
          requestId: result.request_id,
          sourceCount: result.sources?.length ?? 0,
          status: result.status ?? "ok",
          traceId: result.trace_id,
        },
        type: "data-hanser-meta",
      });
    },
    generateId,
    onError(error) {
      return error instanceof Error
        ? error.message
        : "Hanser Agent 暂时无法完成这次回复。";
    },
  });

  return createUIMessageStreamResponse({ stream });
}

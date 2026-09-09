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
import { type PostRequestBody, postRequestBodySchema } from "./schema";

export const maxDuration = 600;

function messageText(message: NonNullable<PostRequestBody["message"]>) {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("")
    .trim();
}

export async function POST(request: Request) {
  const parsed = postRequestBodySchema.safeParse(await request.json());
  if (!parsed.success || !parsed.data.message) {
    return Response.json(
      { cause: "当前本地接入只接受新的文本消息。", code: "bad_request:api" },
      { status: 400 }
    );
  }

  const { id, message, outputPreferences } = parsed.data;
  const text = messageText(message);
  if (!text || message.parts.some((part) => part.type === "file")) {
    return Response.json(
      { cause: "Hanser Agent 当前只支持文本输入。", code: "bad_request:api" },
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
          conversation_id: id,
          message: text,
          output_preferences: {
            dynamic_live2d: outputPreferences?.dynamic_live2d ?? false,
            offline_performance:
              outputPreferences?.offline_performance ?? false,
            speech: outputPreferences?.speech ?? false,
            text: true,
          },
          request_id: message.id,
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
          replyId: result.reply_id,
          requestId: result.request_id,
          sourceCount: result.sources?.length ?? 0,
          speech: result.speech,
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

export function DELETE() {
  return Response.json(
    {
      cause: "本地版暂不开放删除会话，以免破坏已建立的记忆来源。",
      code: "bad_request:api",
    },
    { status: 405 }
  );
}

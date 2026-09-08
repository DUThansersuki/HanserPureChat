import type { NextRequest } from "next/server";
import {
  hanserFetch,
  type HanserConversation,
  hanserProxyError,
  hanserUserId,
} from "@/lib/hanser-client";

type HistoryResponse = {
  conversations: HanserConversation[];
  has_more: boolean;
};

export async function GET(request: NextRequest) {
  const limit = Math.min(
    Math.max(
      Number.parseInt(request.nextUrl.searchParams.get("limit") ?? "20", 10),
      1
    ),
    50
  );
  const parameters = new URLSearchParams({
    limit: String(limit),
    user_id: hanserUserId,
  });
  const endingBefore = request.nextUrl.searchParams.get("ending_before");
  if (endingBefore) {
    parameters.set("ending_before", endingBefore);
  }

  const response = await hanserFetch(`/v1/conversations?${parameters}`);
  if (!response.ok) {
    return hanserProxyError(response);
  }
  const payload = (await response.json()) as HistoryResponse;
  return Response.json({
    chats: payload.conversations.map((conversation) => ({
      createdAt: conversation.created_at,
      id: conversation.id,
      title: conversation.title,
      userId: conversation.user_id,
      visibility: "private" as const,
    })),
    hasMore: payload.has_more,
  });
}

export function DELETE() {
  return Response.json(
    {
      cause: "本地版暂不开放批量删除会话。",
      code: "bad_request:api",
    },
    { status: 405 }
  );
}

import {
  hanserFetch,
  type HanserConversationDetail,
  hanserProxyError,
  hanserUserId,
} from "@/lib/hanser-client";

export async function GET(request: Request) {
  const chatId = new URL(request.url).searchParams.get("chatId");
  if (!chatId) {
    return Response.json(
      { cause: "chatId required", code: "bad_request:api" },
      { status: 400 }
    );
  }

  const parameters = new URLSearchParams({ user_id: hanserUserId });
  const response = await hanserFetch(
    `/v1/conversations/${encodeURIComponent(chatId)}?${parameters}`
  );
  if (response.status === 404) {
    return Response.json({
      isReadonly: false,
      messages: [],
      userId: hanserUserId,
      visibility: "private",
    });
  }
  if (!response.ok) {
    return hanserProxyError(response);
  }

  const conversation = (await response.json()) as HanserConversationDetail;
  return Response.json({
    isReadonly: false,
    messages: conversation.messages.map((message) => ({
      id: message.id,
      metadata: { createdAt: message.created_at },
      parts: [{ text: message.content, type: "text" as const }],
      role: message.role,
    })),
    userId: conversation.user_id,
    visibility: "private",
  });
}

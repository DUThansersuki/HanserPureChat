import { hanserFetch, hanserProxyError, hanserUnavailableError, hanserUserId } from "@/lib/hanser-client";

export async function POST(_request: Request, context: { params: Promise<{ replyId: string }> }) {
  const { replyId } = await context.params;
  const response = await hanserFetch(`/v1/replies/${encodeURIComponent(replyId)}/recover-memory?user_id=${encodeURIComponent(hanserUserId)}`, { method: "POST" }).catch(() => null);
  if (!response) return hanserUnavailableError();
  if (!response.ok) return hanserProxyError(response);
  return Response.json(await response.json());
}

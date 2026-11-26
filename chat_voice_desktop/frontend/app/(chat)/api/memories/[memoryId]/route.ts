import { hanserFetch, hanserProxyError, hanserUnavailableError, hanserUserId } from "@/lib/hanser-client";

async function change(request: Request, context: { params: Promise<{ memoryId: string }> }) {
  const { memoryId } = await context.params;
  const response = await hanserFetch(`/v1/memories/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(hanserUserId)}`, {
    method: request.method,
    headers: { "Content-Type": "application/json" },
    body: request.method === "PATCH" ? JSON.stringify(await request.json()) : undefined,
  }).catch(() => null);
  if (!response) return hanserUnavailableError();
  if (!response.ok) return hanserProxyError(response);
  return Response.json(await response.json());
}
export const PATCH = change;
export const DELETE = change;

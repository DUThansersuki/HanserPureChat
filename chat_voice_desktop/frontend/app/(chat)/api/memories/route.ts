import { hanserFetch, hanserProxyError, hanserUnavailableError, hanserUserId } from "@/lib/hanser-client";

export async function GET() {
  const response = await hanserFetch(`/v1/memories?user_id=${encodeURIComponent(hanserUserId)}`).catch(() => null);
  if (!response) return hanserUnavailableError();
  if (!response.ok) return hanserProxyError(response);
  return Response.json(await response.json());
}

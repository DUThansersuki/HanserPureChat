import { hanserFetch, hanserProxyError } from "@/lib/hanser-client";
import { ownerQuery } from "../../_proxy";

export async function GET(
  request: Request,
  context: { params: Promise<{ artifactId: string }> }
) {
  const { artifactId } = await context.params;
  const range = request.headers.get("range");
  const response = await hanserFetch(
    ownerQuery(`/v1/voice/artifacts/${encodeURIComponent(artifactId)}`),
    { headers: range ? { Range: range } : undefined, signal: request.signal }
  );
  if (!response.ok) {
    return hanserProxyError(response);
  }
  const headers = new Headers();
  for (const key of [
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
  ]) {
    const value = response.headers.get(key);
    if (value) {
      headers.set(key, value);
    }
  }
  return new Response(response.body, { headers, status: response.status });
}

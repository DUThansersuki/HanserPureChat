import { hanserFetch, hanserProxyError } from "@/lib/hanser-client";
import { ownerQuery } from "../../../_proxy";

export async function GET(
  request: Request,
  context: { params: Promise<{ jobId: string }> }
) {
  const { jobId } = await context.params;
  const after = new URL(request.url).searchParams.get("after") ?? "0";
  const headers: HeadersInit = {};
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }
  const response = await hanserFetch(
    ownerQuery(
      `/v1/voice/jobs/${encodeURIComponent(jobId)}/events?after=${encodeURIComponent(after)}`
    ),
    { headers, signal: request.signal }
  );
  if (!response.ok) {
    return hanserProxyError(response);
  }
  return new Response(response.body, {
    headers: {
      "Cache-Control": "no-cache",
      "Content-Type": "text/event-stream",
    },
    status: response.status,
  });
}

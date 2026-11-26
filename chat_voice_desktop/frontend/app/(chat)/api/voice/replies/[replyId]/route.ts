import { ownerQuery, proxyJson } from "../../_proxy";

export async function GET(
  _request: Request,
  context: { params: Promise<{ replyId: string }> }
) {
  const { replyId } = await context.params;
  return proxyJson(
    ownerQuery(`/v1/voice/replies/${encodeURIComponent(replyId)}`),
    "GET"
  );
}

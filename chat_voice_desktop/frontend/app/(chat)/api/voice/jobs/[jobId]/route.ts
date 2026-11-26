import { ownerQuery, proxyJson } from "../../_proxy";

export async function GET(
  _request: Request,
  context: { params: Promise<{ jobId: string }> }
) {
  const { jobId } = await context.params;
  return proxyJson(
    ownerQuery(`/v1/voice/jobs/${encodeURIComponent(jobId)}`),
    "GET"
  );
}

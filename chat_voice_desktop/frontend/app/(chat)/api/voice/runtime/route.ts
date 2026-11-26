import { ownerQuery, proxyJson } from "../_proxy";

export async function GET() {
  return proxyJson(ownerQuery("/v1/voice/runtime"), "GET");
}

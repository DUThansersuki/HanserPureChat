import { hanserUserId } from "@/lib/hanser-client";
import { proxyJson } from "../../voice/_proxy";

export async function POST(request: Request) {
  const body = (await request.json()) as Record<string, unknown>;
  return proxyJson("/v1/performance/visual-plans", "POST", {
    ...body,
    user_id: hanserUserId,
  });
}

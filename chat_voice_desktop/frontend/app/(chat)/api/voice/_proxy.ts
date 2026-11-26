import {
  hanserFetch,
  hanserProxyError,
  hanserUserId,
} from "@/lib/hanser-client";

export async function proxyJson(
  path: string,
  method: "GET" | "POST",
  body?: unknown
) {
  const response = await hanserFetch(path, {
    body: body === undefined ? undefined : JSON.stringify(body),
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    method,
  });
  if (!response.ok) {
    return hanserProxyError(response);
  }
  return Response.json(await response.json(), { status: response.status });
}

export function ownerQuery(path: string) {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}user_id=${encodeURIComponent(hanserUserId)}`;
}

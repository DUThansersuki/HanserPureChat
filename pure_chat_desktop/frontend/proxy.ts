import type { NextRequest } from "next/server";

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/ping") {
    return new Response("pong", { status: 200 });
  }
}

export const config = {
  matcher: ["/ping"],
};

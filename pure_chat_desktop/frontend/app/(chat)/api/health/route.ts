import { hanserFetch, readHanserError } from "@/lib/hanser-client";

export async function GET() {
  try {
    const response = await hanserFetch("/health");
    if (!response.ok) {
      return Response.json(
        { message: await readHanserError(response), ok: false },
        { status: response.status }
      );
    }
    const health = (await response.json()) as {
      chat_ready?: boolean;
      ok?: boolean;
      persona_package?: string;
    };
    return Response.json({
      chat_ready: health.chat_ready === true,
      ok: health.ok === true,
      persona_package: health.persona_package,
    });
  } catch {
    return Response.json(
      { message: "无法连接 Hanser 后端", ok: false },
      { status: 503 }
    );
  }
}

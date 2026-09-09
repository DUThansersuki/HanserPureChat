import type { VoiceJob, VoiceJobEvent } from "./contracts";

const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

async function checked(response: Response) {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      cause?: string;
    } | null;
    throw new Error(
      payload?.cause ?? `语音服务请求失败（HTTP ${response.status}）`
    );
  }
  return response;
}

export async function createVoiceJob(replyId: string, signal: AbortSignal) {
  const response = await checked(
    await fetch(`${basePath}/api/voice/jobs`, {
      body: JSON.stringify({
        mode: "interactive",
        rendition_id: "default",
        reply_id: replyId,
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
      signal,
    })
  );
  return (await response.json()) as VoiceJob;
}

export async function cancelVoiceJob(jobId: string) {
  await fetch(
    `${basePath}/api/voice/jobs/${encodeURIComponent(jobId)}/cancel`,
    {
      method: "POST",
    }
  );
}

export async function reportPlayback(
  jobId: string,
  payload: Record<string, unknown>
) {
  await fetch(
    `${basePath}/api/voice/jobs/${encodeURIComponent(jobId)}/playback`,
    {
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    }
  );
}

export async function readVoiceEvents(
  jobId: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: VoiceJobEvent) => void
) {
  const response = await checked(
    await fetch(
      `${basePath}/api/voice/jobs/${encodeURIComponent(jobId)}/events?after=${after}`,
      { headers: { Accept: "text/event-stream" }, signal }
    )
  );
  if (!response.body) {
    throw new Error("语音事件流不可用");
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = "";
  while (true) {
    // biome-ignore lint/performance/noAwaitInLoops: SSE chunks must be consumed in wire order.
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    pending += value;
    let boundary = pending.indexOf("\n\n");
    while (boundary >= 0) {
      const block = pending.slice(0, boundary);
      pending = pending.slice(boundary + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (data) {
        onEvent(JSON.parse(data) as VoiceJobEvent);
      }
      boundary = pending.indexOf("\n\n");
    }
  }
}

export async function loadAudioArtifact(
  artifactId: string,
  context: AudioContext,
  signal: AbortSignal
) {
  const response = await checked(
    await fetch(
      `${basePath}/api/voice/artifacts/${encodeURIComponent(artifactId)}`,
      { signal }
    )
  );
  return context.decodeAudioData(await response.arrayBuffer());
}

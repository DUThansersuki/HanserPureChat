"use client";

import { useState } from "react";
import useSWR from "swr";
import type { HanserMetaData } from "@/lib/types";

type Evidence = { sources: Array<{ source_id: string; chunk_index?: number; filename: string; text: string }>; degraded_reasons: string[]; post_turn_status: "completed" | "pending_retry"; post_turn_retry_id?: string };

export function ReplyEvidence({ metadata }: { metadata?: HanserMetaData }) {
  const [open, setOpen] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string>();
  const { data, error, mutate } = useSWR<Evidence>(open && metadata?.replyId ? `/api/replies/${encodeURIComponent(metadata.replyId)}/evidence` : null, async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error("来源暂时无法读取，请稍后重试");
    return response.json();
  });
  if (!metadata?.replyId) return null;
  const reasons = data?.degraded_reasons ?? metadata.degradedReasons;
  const degraded = reasons.some((reason) => !reason.startsWith("signal_conflict:") && !reason.startsWith("planner_signal_unavailable:") && reason !== "post_turn_pending_retry");
  return (
    <details className="text-xs text-muted-foreground" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer">{metadata.sourceCount ? `参考来源（${metadata.sourceCount}）` : "回复信息"}{degraded ? " · 有补充说明" : ""}</summary>
      {error ? <p role="status" className="pt-2">{error.message}</p> : null}
      {open && !data && !error ? <p className="pt-2">正在读取…</p> : null}
      {data?.sources.map((source, index) => <div key={`${source.source_id}-${index}`} className="mt-3 rounded border p-3">
        <p className="font-medium">{source.filename}{source.chunk_index != null ? ` · 片段 ${source.chunk_index + 1}` : ""}</p>
        <blockquote className="mt-2 whitespace-pre-wrap break-words leading-relaxed">{source.text}</blockquote>
      </div>)}
      {data?.sources.length === 0 ? <p className="pt-2">本次回复没有引用检索来源。</p> : null}
      {data?.post_turn_status === "completed" && metadata.postTurnStatus === "pending_retry" ? <p className="pt-2">记忆整理已恢复。</p> : null}
      {data?.post_turn_retry_id ? <button className="mt-2 rounded border px-2 py-1" disabled={recovering} onClick={async () => {
        setRecovering(true); setRecoveryError(undefined);
        try {
          const response = await fetch(`/api/replies/${encodeURIComponent(metadata.replyId!)}/recover-memory`, { method: "POST" });
          if (!response.ok) throw new Error("这次记忆整理仍未恢复，可稍后重试。");
          await mutate(await response.json());
        } catch (failure) { setRecoveryError(failure instanceof Error ? failure.message : "恢复失败"); }
        finally { setRecovering(false); }
      }}>{recovering ? "正在恢复…" : "恢复记忆整理"}</button> : null}
      {recoveryError ? <p role="status" className="pt-2">{recoveryError}</p> : null}
      {degraded ? <p className="pt-2">本次回复采用了部分替代处理；可稍后继续聊天。</p> : null}
    </details>
  );
}

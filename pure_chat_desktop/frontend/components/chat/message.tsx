"use client";

import { useEffect, useState } from "react";
import type { ChatMessage } from "@/lib/types";
import { cn, sanitizeText } from "@/lib/utils";
import { MessageContent, MessageResponse } from "../ai-elements/message";
import { PureMessageActions } from "./message-actions";

const WAITING_MESSAGES = [
  "小天使正在推开车门...",
  "主播正在刮胡子...",
  "憨憨正在练习舞剑...",
  "烤箱正在烧烤...",
  "正在把头伸出洗衣机...",
] as const;

function WaitingText() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(
      () => setIndex((value) => (value + 1) % WAITING_MESSAGES.length),
      2550
    );
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="min-h-[calc(15px*1.65)] text-[15px] leading-[1.65]" role="status">
      <span className="thinking-shimmer">{WAITING_MESSAGES[index]}</span>
    </div>
  );
}

export function PreviewMessage({
  canRetry,
  isLoading,
  message,
  onRetry,
}: {
  canRetry: boolean;
  isLoading: boolean;
  message: ChatMessage;
  onRetry: () => void;
}) {
  const textParts = message.parts.filter((part) => part.type === "text");
  const pendingPostTurn = message.parts.some(
    (part) =>
      part.type === "data-hanser-meta" &&
      part.data.postTurnStatus === "pending_retry"
  );
  const isAssistant = message.role === "assistant";
  const content = isLoading && textParts.length === 0 ? (
    <WaitingText />
  ) : (
    <>
      {textParts.map((part, index) => (
        <MessageContent
          className={cn("text-[15px] leading-[1.65]", {
            "w-fit max-w-[min(78%,56ch)] overflow-hidden break-words rounded-lg bg-[var(--message-user)] px-4 py-2.5 text-secondary-foreground":
              message.role === "user",
          })}
          key={`${message.id}-text-${index}`}
        >
          <MessageResponse>{sanitizeText(part.text)}</MessageResponse>
        </MessageContent>
      ))}
      {pendingPostTurn ? (
        <div className="text-[11px] text-amber-600">
          回复已保存，本轮记忆整理仍在等待后端重试
        </div>
      ) : null}
      <PureMessageActions
        message={message}
        onRetry={canRetry ? onRetry : undefined}
      />
    </>
  );

  return (
    <div
      className="group/message w-full animate-[message-in_0.2s_cubic-bezier(0.4,0,0.2,1)]"
      data-role={message.role}
    >
      <div
        className={cn(
          isAssistant ? "flex items-start gap-3" : "flex flex-col items-end gap-2"
        )}
      >
        {isAssistant ? (
          <span className="mt-[0.18rem] w-2 shrink-0 text-[10px] text-[var(--hanser-gold)]">
            ✦
          </span>
        ) : null}
        {isAssistant ? (
          <div className="flex min-w-0 flex-1 flex-col gap-3 border-l border-[color:var(--hanser-rust)]/35 pl-4">
            {content}
          </div>
        ) : (
          content
        )}
      </div>
    </div>
  );
}

export const ThinkingMessage = () => (
  <div className="group/message w-full" data-role="assistant">
    <div className="flex items-start gap-3">
      <span className="mt-[0.18rem] w-2 shrink-0 text-[10px] text-[var(--hanser-gold)]">
        ✦
      </span>
      <div className="border-l border-[color:var(--hanser-rust)]/35 pl-4">
        <WaitingText />
      </div>
    </div>
  </div>
);

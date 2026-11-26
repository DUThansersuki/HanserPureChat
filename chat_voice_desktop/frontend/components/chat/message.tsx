"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import type { ChatMessage } from "@/lib/types";
import { cn, sanitizeText } from "@/lib/utils";
import { MessageContent, MessageResponse } from "../ai-elements/message";
import { PureMessageActions } from "./message-actions";
import { ReplyEvidence } from "./reply-evidence";

function WaitingText() {
  const [takingLonger, setTakingLonger] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setTakingLonger(true), 10_000);
    return () => window.clearTimeout(timer);
  }, []);
  return (
    <div className="min-h-[25px] text-[14px] leading-[25px] text-muted-foreground" role="status">
      <span>{takingLonger ? "这次稍久一点，可以继续等或停止" : "正在回复…"}</span>
      {!takingLonger ? <span aria-hidden="true" className="ml-1 inline-flex gap-0.5"><span className="thinking-dot">·</span><span className="thinking-dot [animation-delay:250ms]">·</span><span className="thinking-dot [animation-delay:500ms]">·</span></span> : null}
    </div>
  );
}

function CharacterAvatar() {
  return (
    <Image
      alt="Hanser"
      className="mt-0.5 size-9 shrink-0 rounded-full object-cover"
      height={36}
      src="/images/hanser-avatar.jpg"
      unoptimized
      width={36}
    />
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
          className={cn("text-[15px] leading-[25px]", {
            "w-fit max-w-[min(85%,60ch)] break-words rounded-[12px] bg-[var(--message-user)] px-4 py-2.5 text-foreground":
              message.role === "user",
          })}
          key={`${message.id}-text-${index}`}
        >
          <MessageResponse>{sanitizeText(part.text)}</MessageResponse>
        </MessageContent>
      ))}
      {pendingPostTurn ? (
        <div className="text-[11px] text-amber-600">
          回复已保存，记忆整理待恢复
        </div>
      ) : null}
      {isAssistant ? <ReplyEvidence metadata={message.parts.find((part) => part.type === "data-hanser-meta")?.data} /> : null}
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
          <CharacterAvatar />
        ) : null}
        {isAssistant ? (
          <div className="flex min-w-0 flex-1 flex-col gap-3 pt-1">
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
      <CharacterAvatar />
      <div className="pt-1">
        <WaitingText />
      </div>
    </div>
  </div>
);

"use client";

import { PauseIcon, PlayIcon, RefreshCcwIcon, SquareIcon, Volume2Icon } from "lucide-react";
import { toast } from "sonner";
import { useCopyToClipboard } from "usehooks-ts";
import { useVoice } from "@/components/voice/voice-provider";
import type { ChatMessage } from "@/lib/types";
import { MessageAction, MessageActions } from "../ai-elements/message";
import { CopyIcon } from "./icons";

export function PureMessageActions({
  message,
  onRetry,
}: {
  message: ChatMessage;
  onRetry?: () => void;
}) {
  const [, copyToClipboard] = useCopyToClipboard();
  const { activeReplyId, error, interrupt, pause, replay, resynthesize, resume, state } = useVoice();
  const voiceMetadata = message.parts.find(
    (part) => part.type === "data-hanser-meta"
  );
  const replyId = voiceMetadata?.data.replyId;
  const isCurrentVoice = Boolean(replyId && replyId === activeReplyId);
  const isPlaying = isCurrentVoice && state === "PLAYING";
  const isPaused = isCurrentVoice && (state === "PAUSED" || state === "WAITING_USER_GESTURE");
  const isPreparing = isCurrentVoice && ["PREPARING", "BUFFERING", "READY"].includes(state);
  const voiceStatus = isPlaying ? "正在播放" : isPaused ? "已暂停" : isPreparing ? (state === "BUFFERING" ? "正在缓冲" : "正在准备") : "播放语音";
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

  return (
    <MessageActions className="-ml-0.5">
      {message.role === "assistant" &&
      replyId &&
      voiceMetadata?.data.speech?.status === "eligible" ? (
        <MessageAction
          aria-label={isPlaying ? "暂停这条语音" : isPaused ? "继续这条语音" : isPreparing ? "停止这条语音" : "播放这条语音"}
          className="h-7 gap-1.5 px-1.5 text-[12px] text-muted-foreground"
          size="sm"
          onClick={() => {
            const action = isPlaying ? (pause(), Promise.resolve()) : isPaused ? resume() : isPreparing ? interrupt() : replay(replyId);
            action.catch((error: unknown) => {
              toast.error(error instanceof Error ? error.message : "语音重播失败");
            });
          }}
          tooltip={isPlaying ? "暂停语音" : isPaused ? "继续语音" : isPreparing ? "停止语音" : "播放语音"}
        >
          {isPlaying ? <PauseIcon className="size-3.5" /> : isPaused ? <PlayIcon className="size-3.5" /> : isPreparing ? <SquareIcon className="size-3.5" /> : <Volume2Icon className="size-3.5" />}
          <span>{voiceStatus}</span>
        </MessageAction>
      ) : null}
      {replyId && voiceMetadata?.data.speech?.status === "eligible" && !isPlaying && !isPaused && !isPreparing ? (
        <MessageAction aria-label="重新合成这条语音" tooltip="重新合成原回复语音" onClick={() => void resynthesize(replyId).catch((failure: unknown) => toast.error(failure instanceof Error ? failure.message : "语音合成失败"))}>
          <RefreshCcwIcon className="size-3.5" />
          <span>重新合成</span>
        </MessageAction>
      ) : null}
      {isCurrentVoice && error ? <span role="status" className="text-xs text-muted-foreground">{error}</span> : null}
      {isPlaying || isPaused ? (
        <MessageAction aria-label="停止这条语音" onClick={() => void interrupt().catch((error: unknown) => toast.error(error instanceof Error ? error.message : "语音停止失败"))} tooltip="停止语音">
          <SquareIcon className="size-3.5" />
        </MessageAction>
      ) : null}
      {onRetry ? (
        <MessageAction className="opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100" onClick={onRetry} tooltip="恢复回复">
          <RefreshCcwIcon className="size-3.5" />
        </MessageAction>
      ) : null}
      <MessageAction
        className="opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100"
        onClick={async () => {
          if (!text) {
            toast.error("没有可复制的文本");
            return;
          }
          await copyToClipboard(text);
          toast.success("已复制");
        }}
        tooltip="复制"
      >
        <CopyIcon />
      </MessageAction>
    </MessageActions>
  );
}

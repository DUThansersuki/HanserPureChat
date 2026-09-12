import equal from "fast-deep-equal";
import { RefreshCcwIcon, Volume2Icon } from "lucide-react";
import { memo, useCallback } from "react";
import { toast } from "sonner";
import { useCopyToClipboard } from "usehooks-ts";
import { useVoice } from "@/components/voice/voice-provider";
import type { Vote } from "@/lib/db/schema";
import type { ChatMessage } from "@/lib/types";
import {
  MessageAction as Action,
  MessageActions as Actions,
} from "../ai-elements/message";
import { CopyIcon, PencilEditIcon } from "./icons";

export function PureMessageActions({
  message,
  isLoading,
  onEdit,
  onRetry,
}: {
  chatId: string;
  message: ChatMessage;
  vote: Vote | undefined;
  isLoading: boolean;
  onEdit?: () => void;
  onRetry?: () => void;
}) {
  const [, copyToClipboard] = useCopyToClipboard();
  const { canReplay, replay } = useVoice();
  const voiceMetadata = message.parts.find(
    (part) => part.type === "data-hanser-meta"
  );
  const replyId = voiceMetadata?.data.replyId;
  const hasReplayableVoice = Boolean(replyId && canReplay(replyId));
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();
  const handleCopy = useCallback(async () => {
    if (!text) {
      toast.error("没有可复制的文本");
      return;
    }
    await copyToClipboard(text);
    toast.success("已复制");
  }, [copyToClipboard, text]);
  const handleReplay = useCallback(() => {
    if (!replyId) {
      return;
    }
    replay(replyId).catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : "语音重播失败");
    });
  }, [replay, replyId]);

  if (isLoading) {
    return null;
  }

  return (
    <Actions
      className={
        message.role === "user"
          ? "-mr-0.5 justify-end opacity-0 transition-opacity duration-150 group-hover/message:opacity-100 group-focus-within/message:opacity-100 [&_button]:rounded-sm [&_button]:bg-transparent"
          : "-ml-0.5 opacity-0 transition-opacity duration-150 group-hover/message:opacity-100 group-focus-within/message:opacity-100 [&_button]:rounded-sm [&_button]:bg-transparent"
      }
    >
      {message.role === "user" && onEdit ? (
        <Action onClick={onEdit} tooltip="编辑">
          <PencilEditIcon />
        </Action>
      ) : null}
      {message.role === "assistant" && onRetry ? (
        <Action onClick={onRetry} tooltip="重新生成">
          <RefreshCcwIcon className="size-3.5" />
        </Action>
      ) : null}
      {message.role === "assistant" &&
      voiceMetadata?.data.speech?.status === "eligible" ? (
        <Action
          disabled={!hasReplayableVoice}
          onClick={handleReplay}
          tooltip={hasReplayableVoice ? "重新播放语音与动作" : "语音准备中"}
        >
          <Volume2Icon className="size-3.5" />
        </Action>
      ) : null}
      <Action onClick={handleCopy} tooltip="复制">
        <CopyIcon />
      </Action>
    </Actions>
  );
}

export const MessageActions = memo(
  PureMessageActions,
  (previous, next) =>
    equal(previous.vote, next.vote) &&
    previous.isLoading === next.isLoading &&
    previous.onRetry === next.onRetry &&
    previous.message.id === next.message.id
);

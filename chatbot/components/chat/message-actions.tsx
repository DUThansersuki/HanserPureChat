import equal from "fast-deep-equal";
import { memo, useCallback } from "react";
import { toast } from "sonner";
import { useCopyToClipboard } from "usehooks-ts";
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
}: {
  chatId: string;
  message: ChatMessage;
  vote: Vote | undefined;
  isLoading: boolean;
  onEdit?: () => void;
}) {
  const [, copyToClipboard] = useCopyToClipboard();
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

  if (isLoading) {
    return null;
  }

  return (
    <Actions
      className={
        message.role === "user"
          ? "-mr-0.5 justify-end opacity-0 transition-opacity duration-150 group-hover/message:opacity-100"
          : "-ml-0.5 opacity-0 transition-opacity duration-150 group-hover/message:opacity-100"
      }
    >
      {message.role === "user" && onEdit ? (
        <Action onClick={onEdit} tooltip="编辑">
          <PencilEditIcon />
        </Action>
      ) : null}
      <Action onClick={handleCopy} tooltip="复制">
        <CopyIcon />
      </Action>
    </Actions>
  );
}

export const MessageActions = memo(PureMessageActions, (previous, next) => {
  return (
    equal(previous.vote, next.vote) &&
    previous.isLoading === next.isLoading &&
    previous.message.id === next.message.id
  );
});

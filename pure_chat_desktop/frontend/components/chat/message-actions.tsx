"use client";

import { RefreshCcwIcon } from "lucide-react";
import { toast } from "sonner";
import { useCopyToClipboard } from "usehooks-ts";
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
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

  return (
    <MessageActions className="-ml-0.5 opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100">
      {onRetry ? (
        <MessageAction onClick={onRetry} tooltip="重试">
          <RefreshCcwIcon className="size-3.5" />
        </MessageAction>
      ) : null}
      <MessageAction
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

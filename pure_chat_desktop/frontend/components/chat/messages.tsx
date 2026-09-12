import type { UseChatHelpers } from "@ai-sdk/react";
import { ArrowDownIcon } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { useMessages } from "@/hooks/use-messages";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Greeting } from "./greeting";
import { PreviewMessage, ThinkingMessage } from "./message";

type MessagesProps = {
  chatId: string;
  status: UseChatHelpers<ChatMessage>["status"];
  messages: ChatMessage[];
  regenerate: UseChatHelpers<ChatMessage>["regenerate"];
  isLoading?: boolean;
};

export function Messages({
  chatId,
  isLoading,
  messages,
  regenerate,
  status,
}: MessagesProps) {
  const { containerRef, endRef, isAtBottom, scrollToBottom, reset } =
    useMessages({ status });
  const previousChatId = useRef(chatId);

  useEffect(() => {
    if (previousChatId.current !== chatId) {
      previousChatId.current = chatId;
      reset();
    }
  }, [chatId, reset]);

  const handleScrollToBottom = useCallback(() => {
    scrollToBottom("smooth");
  }, [scrollToBottom]);

  return (
    <div className="relative flex-1 bg-background">
      {messages.length === 0 && !isLoading ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-end">
          <div className="mx-auto w-full max-w-3xl px-4 pb-7">
            <Greeting />
          </div>
        </div>
      ) : null}
      <div
        className={cn(
          "absolute inset-0 touch-pan-y overflow-y-auto",
          messages.length > 0 ? "bg-background" : "bg-transparent"
        )}
        ref={containerRef}
      >
        <div className="mx-auto flex min-h-full min-w-0 max-w-3xl flex-col gap-8 px-2 py-8 md:px-4">
          {messages.map((message, index) => (
            <PreviewMessage
              canRetry={
                index === messages.length - 1 &&
                message.role === "assistant" &&
                status !== "submitted" &&
                status !== "streaming"
              }
              isLoading={status === "streaming" && index === messages.length - 1}
              key={message.id}
              message={message}
              onRetry={() => regenerate()}
            />
          ))}
          {status === "submitted" && messages.at(-1)?.role !== "assistant" ? (
            <ThinkingMessage />
          ) : null}
          <div className="min-h-6 min-w-6 shrink-0" ref={endRef} />
        </div>
      </div>
      <button
        aria-label="滚动到底部"
        className={cn(
          "absolute bottom-4 left-1/2 z-10 flex h-7 -translate-x-1/2 items-center rounded-full border border-border/50 bg-card px-3.5 text-[10px]",
          isAtBottom ? "pointer-events-none opacity-0" : "opacity-100"
        )}
        onClick={handleScrollToBottom}
        type="button"
      >
        <ArrowDownIcon className="size-3 text-muted-foreground" />
      </button>
    </div>
  );
}

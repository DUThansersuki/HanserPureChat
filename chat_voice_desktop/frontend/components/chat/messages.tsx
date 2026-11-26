import type { UseChatHelpers } from "@ai-sdk/react";
import { ArrowDownIcon } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { useMessages } from "@/hooks/use-messages";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Greeting } from "./greeting";
import { PreviewMessage, ThinkingMessage } from "./message";
import { SuggestedActions } from "./suggested-actions";

type MessagesProps = {
  chatId: string;
  status: UseChatHelpers<ChatMessage>["status"];
  messages: ChatMessage[];
  regenerate: UseChatHelpers<ChatMessage>["regenerate"];
  isLoading?: boolean;
  onSuggestion: (value: string) => void;
  showSuggestions: boolean;
};

export function Messages({
  chatId,
  isLoading,
  messages,
  regenerate,
  status,
  onSuggestion,
  showSuggestions,
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

  if (messages.length === 0 && !isLoading) {
    return (
      <div className="empty-chat-region min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-[760px] flex-col justify-center gap-5 px-4 py-6 md:px-6">
          <Greeting />
          {showSuggestions ? <div className="empty-chat-topics"><SuggestedActions onSelect={onSuggestion} /></div> : null}
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div
        className="absolute inset-0 touch-pan-y overflow-y-auto"
        ref={containerRef}
      >
        <div className="mx-auto flex min-h-full min-w-0 max-w-[760px] flex-col gap-6 px-4 py-6 md:px-6">
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

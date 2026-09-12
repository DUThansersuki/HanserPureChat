"use client";

import type { UseChatHelpers } from "@ai-sdk/react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { ChatMessage } from "@/lib/types";
import { SuggestedActions } from "./suggested-actions";

type ChatInputProps = {
  chatId: string;
  input: string;
  setInput: (value: string) => void;
  status: UseChatHelpers<ChatMessage>["status"];
  stop: UseChatHelpers<ChatMessage>["stop"];
  regenerate: UseChatHelpers<ChatMessage>["regenerate"];
  messages: ChatMessage[];
  sendMessage: UseChatHelpers<ChatMessage>["sendMessage"];
  isLoading: boolean;
};

export function ChatInput({
  chatId,
  input,
  isLoading,
  messages,
  regenerate,
  sendMessage,
  setInput,
  status,
  stop,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const draftKey = `hanser-pure-chat-draft:${chatId}`;

  useEffect(() => {
    setInput(window.localStorage.getItem(draftKey) ?? "");
    textareaRef.current?.focus();
  }, [draftKey, setInput]);

  useEffect(() => {
    if (input) {
      window.localStorage.setItem(draftKey, input);
    } else {
      window.localStorage.removeItem(draftKey);
    }
  }, [draftKey, input]);

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text || isLoading) {
      return;
    }
    if (status !== "ready" && status !== "error") {
      toast.error("请等待当前回复完成，或先停止生成");
      return;
    }
    window.history.pushState({}, "", `/chat/${chatId}`);
    sendMessage({ parts: [{ text, type: "text" }], role: "user" });
    window.localStorage.removeItem(draftKey);
    setInput("");
    textareaRef.current?.focus();
  }, [chatId, draftKey, input, isLoading, sendMessage, setInput, status]);

  return (
    <div className="flex w-full flex-col gap-3">
      {status === "error" ? (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-red-500/20 bg-red-500/5 px-3 py-2 text-[12px] text-red-600">
          <span>回复没有完成，上一条消息仍然保留。</span>
          <button
            className="font-medium underline underline-offset-2"
            onClick={() => regenerate()}
            type="button"
          >
            重试
          </button>
        </div>
      ) : null}
      {!isLoading && messages.length === 0 ? (
        <SuggestedActions chatId={chatId} sendMessage={sendMessage} />
      ) : null}
      <div className="rounded-[10px] border border-border/55 bg-card p-3 focus-within:border-[var(--hanser-rust)]/65 focus-within:shadow-[var(--shadow-composer-focus)]">
        <textarea
          className="max-h-[180px] min-h-[72px] w-full resize-none bg-transparent px-1 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground/45"
          disabled={isLoading}
          maxLength={12_000}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={isLoading ? "正在加载对话…" : "写点什么……"}
          ref={textareaRef}
          value={input}
        />
        <div className="flex items-center justify-between gap-2 pt-2">
          <span className="px-1 text-[12px] text-muted-foreground/55">
            Enter 发送 · Shift+Enter 换行
          </span>
          {status === "submitted" || status === "streaming" ? (
            <Button
              aria-label="停止"
              className="h-8 w-8 rounded-lg bg-[var(--hanser-rust)] text-white"
              onClick={() => stop()}
              size="icon-sm"
              type="button"
            >
              <SquareIcon className="size-3" />
            </Button>
          ) : (
            <Button
              aria-label="发送"
              className="h-8 w-8 rounded-lg bg-[var(--hanser-rust)] text-white disabled:bg-muted disabled:text-muted-foreground/25"
              disabled={isLoading || !input.trim()}
              onClick={submit}
              size="icon-sm"
              type="button"
            >
              <ArrowUpIcon className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}


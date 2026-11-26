"use client";

import type { UseChatHelpers } from "@ai-sdk/react";
import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { type RefObject, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { ChatMessage } from "@/lib/types";

type ChatInputProps = {
  chatId: string;
  input: string;
  setInput: (value: string) => void;
  status: UseChatHelpers<ChatMessage>["status"];
  stop: UseChatHelpers<ChatMessage>["stop"];
  regenerate: UseChatHelpers<ChatMessage>["regenerate"];
  textareaRef: RefObject<HTMLTextAreaElement>;
  sendMessage: UseChatHelpers<ChatMessage>["sendMessage"];
  isLoading: boolean;
};

export function ChatInput({
  chatId,
  input,
  isLoading,
  textareaRef,
  regenerate,
  sendMessage,
  setInput,
  status,
  stop,
}: ChatInputProps) {
  const draftKey = `hanser-chat-voice:draft:${chatId}`;

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

  const resizeTextarea = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(180, Math.max(36, textarea.scrollHeight))}px`;
  }, [textareaRef]);

  useEffect(resizeTextarea, [input, resizeTextarea]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    let previousWidth = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth !== previousWidth) {
        previousWidth = textarea.clientWidth;
        resizeTextarea();
      }
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [resizeTextarea, textareaRef]);

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
      <div className="chat-composer rounded-[14px] border border-[var(--brand-border)] bg-card px-3 py-2 focus-within:border-ring focus-within:shadow-[var(--shadow-composer-focus)]">
        <textarea
          className="block max-h-[180px] min-h-[36px] w-full resize-none overflow-y-auto bg-transparent px-1 py-1 text-[15px] leading-[25px] outline-none placeholder:text-muted-foreground"
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
        <div className="flex items-center justify-between gap-2 pt-1">
          <span className="px-1 text-[12px] text-muted-foreground">
            Enter 发送 · Shift+Enter 换行
          </span>
          {status === "submitted" || status === "streaming" ? (
            <Button
              aria-label="停止"
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
              onClick={() => stop()}
              size="icon-sm"
              type="button"
            >
              <SquareIcon className="size-3" />
            </Button>
          ) : (
            <Button
              aria-label="发送"
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:bg-muted disabled:text-muted-foreground"
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

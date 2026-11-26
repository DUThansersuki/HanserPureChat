"use client";

import { useCallback, useEffect, useRef } from "react";
import { useActiveChat } from "@/hooks/use-active-chat";
import { ChatHeader } from "./chat-header";
import { ChatInput } from "./chat-input";
import { Messages } from "./messages";

export function ChatShell() {
  const {
    chatId,
    input,
    isLoading,
    loadError,
    messages,
    regenerate,
    reloadChat,
    sendMessage,
    setInput,
    status,
    stop,
  } = useActiveChat();
  const stopRef = useRef(stop);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  stopRef.current = stop;
  const selectSuggestion = useCallback((suggestion: string) => {
    setInput(suggestion);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      textarea?.focus();
      textarea?.setSelectionRange(suggestion.length, suggestion.length);
    });
  }, [setInput]);

  const previousChatId = useRef(chatId);
  useEffect(() => {
    if (previousChatId.current !== chatId) {
      previousChatId.current = chatId;
      stopRef.current();
    }
  }, [chatId]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-background">
      <ChatHeader />
      <main className="chat-surface relative isolate flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
        <div aria-hidden="true" className="chat-background" />
        {loadError ? (
          <div className="flex items-center justify-center gap-2 border-b border-red-500/20 bg-red-500/5 px-3 py-2 text-[12px] text-red-600">
            <span>这段对话暂时没加载出来。</span>
            <button
              className="font-medium underline underline-offset-2"
              onClick={reloadChat}
              type="button"
            >
              重试
            </button>
          </div>
        ) : null}
        <Messages
          chatId={chatId}
          isLoading={isLoading}
          messages={messages}
          regenerate={regenerate}
          status={status}
          onSuggestion={selectSuggestion}
          showSuggestions={!input.trim()}
        />
        <div className="sticky bottom-0 z-10 mx-auto flex w-full max-w-[760px] px-4 pb-4 md:px-6 md:pb-5">
          <ChatInput
            chatId={chatId}
            input={input}
            isLoading={isLoading || Boolean(loadError)}
            textareaRef={textareaRef}
            regenerate={regenerate}
            sendMessage={sendMessage}
            setInput={setInput}
            status={status}
            stop={stop}
          />
        </div>
      </main>
    </div>
  );
}

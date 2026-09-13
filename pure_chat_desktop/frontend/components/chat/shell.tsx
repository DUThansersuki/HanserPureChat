"use client";

import { useEffect, useRef } from "react";
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
  stopRef.current = stop;

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
      <main className="relative flex min-h-0 flex-1 flex-col overflow-hidden bg-background md:rounded-tl-[12px] md:border-l md:border-t md:border-border/40">
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
        />
        <div className="sticky bottom-0 z-10 mx-auto flex w-full max-w-3xl bg-background px-2 pb-3 md:px-4 md:pb-4">
          <ChatInput
            chatId={chatId}
            input={input}
            isLoading={isLoading || Boolean(loadError)}
            messages={messages}
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

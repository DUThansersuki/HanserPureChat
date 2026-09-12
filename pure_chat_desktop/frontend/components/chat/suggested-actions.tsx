"use client";

import type { UseChatHelpers } from "@ai-sdk/react";
import { suggestions } from "@/lib/constants";
import type { ChatMessage } from "@/lib/types";

export function SuggestedActions({
  chatId,
  sendMessage,
}: {
  chatId: string;
  sendMessage: UseChatHelpers<ChatMessage>["sendMessage"];
}) {
  return (
    <div className="flex w-full flex-col gap-0.5">
      {suggestions.map((suggestion) => (
        <button
          className="h-10 w-full border-l border-transparent px-3 text-left text-[15px] text-muted-foreground transition-colors hover:border-[var(--hanser-rust)]/45 hover:text-foreground"
          key={suggestion}
          onClick={() => {
            window.history.pushState({}, "", `/chat/${chatId}`);
            sendMessage({
              parts: [{ text: suggestion, type: "text" }],
              role: "user",
            });
          }}
          type="button"
        >
          <span className="mr-3 text-[var(--hanser-gold)]/70">✦</span>
          {suggestion}
        </button>
      ))}
    </div>
  );
}

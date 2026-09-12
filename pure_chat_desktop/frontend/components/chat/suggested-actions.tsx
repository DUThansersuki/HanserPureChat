"use client";

import type { UseChatHelpers } from "@ai-sdk/react";
import { memo, useCallback } from "react";
import { suggestions } from "@/lib/constants";
import type { ChatMessage } from "@/lib/types";
import { Suggestion } from "../ai-elements/suggestion";
import type { VisibilityType } from "./visibility-selector";

type SuggestedActionsProps = {
  chatId: string;
  sendMessage: UseChatHelpers<ChatMessage>["sendMessage"];
  selectedVisibilityType: VisibilityType;
};

function PureSuggestedActions({ chatId, sendMessage }: SuggestedActionsProps) {
  const suggestedActions = suggestions;
  const handleSuggestionClick = useCallback(
    (suggestion: string) => {
      window.history.pushState(
        {},
        "",
        `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/chat/${chatId}`
      );
      sendMessage({
        parts: [{ text: suggestion, type: "text" }],
        role: "user",
      });
    },
    [chatId, sendMessage]
  );

  return (
    <div
      className="flex w-full flex-col gap-0.5"
      data-testid="suggested-actions"
    >
      {suggestedActions.map((suggestedAction) => (
        <div key={suggestedAction}>
          <Suggestion
            className="h-10 w-full justify-start gap-3 rounded-none border-0 border-transparent border-l bg-transparent px-3 text-left text-[15px] text-muted-foreground transition-colors duration-150 hover:border-[var(--hanser-rust)]/45 hover:bg-transparent hover:text-foreground"
            onClick={handleSuggestionClick}
            suggestion={suggestedAction}
            variant="ghost"
          >
            <span
              aria-hidden="true"
              className="text-[12px] text-[var(--hanser-gold)]/70"
            >
              ✦
            </span>
            <span>{suggestedAction}</span>
          </Suggestion>
        </div>
      ))}
    </div>
  );
}

export const SuggestedActions = memo(
  PureSuggestedActions,
  (prevProps, nextProps) => {
    if (prevProps.chatId !== nextProps.chatId) {
      return false;
    }
    if (prevProps.selectedVisibilityType !== nextProps.selectedVisibilityType) {
      return false;
    }

    return true;
  }
);

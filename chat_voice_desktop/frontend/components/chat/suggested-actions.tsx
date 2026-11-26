"use client";

import { suggestions } from "@/lib/constants";

export function SuggestedActions({
  onSelect,
}: {
  onSelect: (value: string) => void;
}) {
  return (
    <div className="flex w-full flex-wrap gap-2" aria-label="聊天话题建议">
      {suggestions.map((suggestion) => (
        <button
          className="min-h-8 rounded-lg border border-[var(--brand-border)] bg-[var(--brand-surface)] px-3 py-1 text-left text-[13px] text-primary transition-colors hover:border-ring hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
          key={suggestion}
          onClick={() => onSelect(suggestion)}
          type="button"
        >
          {suggestion}
        </button>
      ))}
    </div>
  );
}

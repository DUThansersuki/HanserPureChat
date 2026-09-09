"use client";

import { PanelLeftIcon } from "lucide-react";
import { memo } from "react";
import { Button } from "@/components/ui/button";
import { useSidebar } from "@/components/ui/sidebar";
import { VoiceControls } from "@/components/voice/voice-controls";
import type { VisibilityType } from "./visibility-selector";

function PureChatHeader(_props: {
  chatId: string;
  selectedVisibilityType: VisibilityType;
  isReadonly: boolean;
}) {
  const { state, toggleSidebar, isMobile } = useSidebar();

  if (state === "collapsed" && !isMobile) {
    return null;
  }

  return (
    <header className="sticky top-0 flex h-14 items-center gap-2 bg-sidebar px-3">
      <Button
        className="md:hidden"
        onClick={toggleSidebar}
        size="icon-sm"
        variant="ghost"
      >
        <PanelLeftIcon className="size-4" />
      </Button>
      <span className="text-sm font-medium text-sidebar-foreground">
        Hanser Agent
      </span>
      <VoiceControls />
      <span className="inline-flex items-center gap-1.5 rounded-full border border-sidebar-border px-2 py-1 text-[11px] text-sidebar-foreground/60">
        <span className="size-1.5 rounded-full bg-emerald-500" />
        本地会话
      </span>
    </header>
  );
}

export const ChatHeader = memo(PureChatHeader);

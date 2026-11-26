"use client";

import { PanelLeftIcon } from "lucide-react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import { useSidebar } from "@/components/ui/sidebar";
import { VoiceControls } from "@/components/voice/voice-controls";
import { cn } from "@/lib/utils";
import { ChatSettings } from "./chat-settings";

type Health = { chat_ready?: boolean; ok: boolean };

export function ChatHeader() {
  const { toggleSidebar } = useSidebar();
  const { data: health, error } = useSWR<Health>("/api/health", (url: string) =>
    fetch(url).then((response) => {
      if (!response.ok) throw new Error("backend unavailable");
      return response.json();
    })
  );
  const ready = health?.ok === true && health.chat_ready !== false && !error;
  const label = error ? "暂时无法连接" : ready ? "" : health?.chat_ready === false ? "角色配置尚未就绪" : "连接中";

  return (
    <header className="sticky top-0 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background px-3 md:px-5">
      <Button aria-label="打开侧栏" data-sidebar-open-trigger className="min-[900px]:hidden" onClick={toggleSidebar} size="icon-sm" variant="ghost">
        <PanelLeftIcon className="size-4" />
      </Button>
      <span aria-hidden="true" className="h-5 w-1 shrink-0 rounded-full bg-[var(--hanser-gold)]" />
      <span className="min-w-0 truncate text-[15px] font-semibold text-primary">小憨同学</span>
      <span className="ml-auto inline-flex min-w-0 items-center gap-1.5 truncate text-[12px] text-muted-foreground" role="status">
        {label ? <span className={cn("size-1.5 shrink-0 rounded-full", error ? "bg-destructive" : "bg-amber-600")} /> : null}
        {label}
      </span>
      <VoiceControls />
      <ChatSettings />
    </header>
  );
}

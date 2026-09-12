"use client";

import { PanelLeftIcon, SlidersHorizontalIcon } from "lucide-react";
import useSWR from "swr";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSidebar } from "@/components/ui/sidebar";
import { useActiveChat } from "@/hooks/use-active-chat";
import { cn } from "@/lib/utils";

type Health = {
  chat_ready?: boolean;
  mode?: string;
  ok: boolean;
  persona_package?: string;
};

export function ChatHeader() {
  const { state, toggleSidebar, isMobile } = useSidebar();
  const { adultInnuendoOptIn, setAdultInnuendoOptIn } = useActiveChat();
  const { data: health, error } = useSWR<Health>("/api/health", (url: string) =>
    fetch(url).then((response) => {
      if (!response.ok) {
        throw new Error("backend unavailable");
      }
      return response.json();
    })
  );
  const ready = health?.ok === true && health.chat_ready !== false && !error;
  const label = error
    ? "暂时离线"
    : ready
      ? "在这里"
      : health?.chat_ready === false
        ? "Persona 未就绪"
        : "连接中";

  return (
    <header className="sticky top-0 flex h-14 shrink-0 items-center gap-2 border-b border-sidebar-border/50 bg-sidebar px-3">
      <Button
        aria-label="打开侧栏"
        className={cn(
          "md:hidden",
          state === "collapsed" && !isMobile && "md:inline-flex"
        )}
        onClick={toggleSidebar}
        size="icon-sm"
        variant="ghost"
      >
        <PanelLeftIcon className="size-4" />
      </Button>
      <span className="min-w-0 truncate text-[15px] font-medium tracking-[0.04em] text-sidebar-foreground">
        小憨同学 <span className="text-sidebar-foreground/55">/</span> 纯聊天
      </span>
      <span className="ml-auto inline-flex items-center gap-1.5 text-[12px] text-sidebar-foreground/70">
        <span
          className={cn(
            "size-1.5 rounded-full",
            ready ? "bg-emerald-500" : error ? "bg-red-500" : "bg-amber-300"
          )}
        />
        {label}
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button aria-label="聊天设置" size="icon-sm" variant="ghost">
            <SlidersHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel>聊天设置</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuCheckboxItem
            checked={adultInnuendoOptIn}
            onCheckedChange={setAdultInnuendoOptIn}
            onSelect={(event) => event.preventDefault()}
          >
            我已成年，允许轻度双关
          </DropdownMenuCheckboxItem>
          <p className="px-2 py-2 text-[11px] leading-relaxed text-muted-foreground">
            仅开放非露骨、低强度表达；明确要求停止时仍会关闭。
          </p>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}

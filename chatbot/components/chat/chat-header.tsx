"use client";

import { PanelLeftIcon, SlidersHorizontalIcon } from "lucide-react";
import { type ChangeEvent, memo, useCallback } from "react";
import useSWR from "swr";
import type { StageState } from "@/components/live2d/mmd-stage";
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
import { VoiceControls } from "@/components/voice/voice-controls";
import { useVoice } from "@/components/voice/voice-provider";
import { useActiveChat } from "@/hooks/use-active-chat";
import { cn } from "@/lib/utils";
import type { VisibilityType } from "./visibility-selector";

function PureChatHeader(_props: {
  chatId: string;
  selectedVisibilityType: VisibilityType;
  isReadonly: boolean;
  live2dState: StageState;
}) {
  const { state, toggleSidebar, isMobile } = useSidebar();
  const {
    enabled: voiceEnabled,
    setVolume,
    state: voiceState,
    volume,
  } = useVoice();
  const { adultInnuendoOptIn, setAdultInnuendoOptIn } = useActiveChat();
  const { data: health, error: healthError } = useSWR<{
    chat_ready?: boolean;
    ok: boolean;
    persona_package?: string;
  }>(
    `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/health`,
    (url: string) =>
      fetch(url).then((response) => {
        if (!response.ok) {
          throw new Error("backend unavailable");
        }
        return response.json();
      }),
    {
      refreshInterval: 15_000,
      revalidateOnFocus: true,
    }
  );
  const isHealthy =
    health?.ok === true && health.chat_ready !== false && !healthError;
  const healthLabel = healthError
    ? "后端离线"
    : health?.chat_ready === false
      ? "Persona 未就绪"
      : health
        ? "Chat 已就绪"
        : "连接中";
  const live2dLabel =
    _props.live2dState === "ready"
      ? "L2D 已就绪"
      : _props.live2dState === "error"
        ? "L2D 加载失败"
        : "L2D 加载中";
  const live2dRequired = process.env.NEXT_PUBLIC_HANSER_CHAT_ONLY !== "1";
  const presenceReady =
    isHealthy && (!live2dRequired || _props.live2dState === "ready");
  const presenceFailed =
    Boolean(healthError) || (live2dRequired && _props.live2dState === "error");
  const presenceLabel = presenceFailed
    ? "暂时离线"
    : presenceReady
      ? "在这里"
      : "连接中";
  const handleVolumeChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      setVolume(Number(event.currentTarget.value));
    },
    [setVolume]
  );

  return (
    <header className="sticky top-0 flex h-14 items-center gap-2 border-sidebar-border/50 border-b bg-sidebar px-3">
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
      <span className="text-[13px] font-medium text-sidebar-foreground tracking-[0.04em]">
        HANSER <span className="text-sidebar-foreground/35">/</span> 夜航通讯
      </span>
      <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-sidebar-foreground/55">
        <span
          className={cn(
            "size-1.5 rounded-full",
            presenceReady
              ? "bg-emerald-500"
              : presenceFailed
                ? "bg-red-500"
                : "status-breathe bg-[var(--hanser-gold)]"
          )}
        />
        {presenceLabel}
      </span>
      {process.env.NEXT_PUBLIC_HANSER_CHAT_ONLY === "1" ? null : (
        <VoiceControls />
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label="聊天设置"
            className="ml-auto"
            size="icon-sm"
            variant="ghost"
          >
            <SlidersHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72">
          <DropdownMenuLabel>聊天设置</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuLabel className="font-normal text-[11px] text-muted-foreground">
            运行状态
          </DropdownMenuLabel>
          <div className="space-y-1 px-2 pb-2 text-[11px]">
            <div className="flex items-center justify-between gap-4">
              <span className="text-muted-foreground">Chat</span>
              <span>{healthLabel}</span>
            </div>
            {live2dRequired ? (
              <div className="flex items-center justify-between gap-4">
                <span className="text-muted-foreground">Live2D</span>
                <span>{live2dLabel}</span>
              </div>
            ) : null}
            {live2dRequired ? (
              <div className="flex items-center justify-between gap-4">
                <span className="text-muted-foreground">Voice</span>
                <span>
                  {voiceEnabled ? voiceState.toLowerCase() : "文字模式"}
                </span>
              </div>
            ) : null}
          </div>
          <DropdownMenuSeparator />
          {process.env.NEXT_PUBLIC_HANSER_CHAT_ONLY === "1" ? null : (
            <>
              <div className="space-y-2 px-2 py-2">
                <div className="flex items-center justify-between text-[12px]">
                  <span>全局音量</span>
                  <span className="tabular-nums text-muted-foreground">
                    {Math.round(volume * 100)}%
                  </span>
                </div>
                <input
                  aria-label="全局音量"
                  className="h-1.5 w-full cursor-pointer accent-foreground"
                  max="1"
                  min="0"
                  onChange={handleVolumeChange}
                  step="0.01"
                  type="range"
                  value={volume}
                />
                <p className="text-[11px] text-muted-foreground">
                  同时控制文字语音和歌唱场景
                </p>
              </div>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuCheckboxItem
            checked={adultInnuendoOptIn}
            onCheckedChange={setAdultInnuendoOptIn}
          >
            <div className="flex flex-col gap-0.5">
              <span>成年人轻度双关</span>
              <span className="font-normal text-[11px] text-muted-foreground">
                确认已成年，并从下一条消息起允许偶发的非露骨双关
              </span>
            </div>
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
            {health?.chat_ready === false
              ? "Persona v2 配置未就绪"
              : health?.persona_package === "persona-v2-production"
                ? "Persona v2 已启用"
                : "Persona 状态未知"}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}

export const ChatHeader = memo(PureChatHeader);

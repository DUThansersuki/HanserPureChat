"use client";

import { SlidersHorizontalIcon } from "lucide-react";
import { Popover } from "radix-ui";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useSidebar } from "@/components/ui/sidebar";
import { useVoice } from "@/components/voice/voice-provider";
import { useActiveChat } from "@/hooks/use-active-chat";
import { MemoryManager } from "./memory-manager";

function SettingsFields() {
  const { adultInnuendoOptIn, setAdultInnuendoOptIn } = useActiveChat();
  const { availability, enabled, error, setVolume, state, volume } = useVoice();
  const preferenceDescriptionId = useId();
  const volumeId = useId();
  const voiceLabel = !enabled
    ? "语音回复已关闭"
    : availability === "loading"
      ? "正在准备语音"
      : availability === "unavailable"
        ? "语音暂时不可用，文字聊天仍可继续"
        : ({
            IDLE: "语音回复已开启", READY: "语音已准备好",
            PREPARING: "正在准备这条语音", BUFFERING: "正在缓冲",
            WAITING_USER_GESTURE: "点击播放语音", PLAYING: "正在播放",
            PAUSED: "已暂停", FINISHED: "播放完毕", INTERRUPTED: "已停止",
            FAILED: "这条语音播放失败，文字聊天仍可继续",
          })[state];

  return (
    <div className="space-y-5 text-[13px]">
      <MemoryManager />
      <fieldset className="min-w-0 space-y-3">
        <legend className="mb-3 text-[12px] font-medium text-muted-foreground">语音回复</legend>
        <p className="text-muted-foreground" role="status">{voiceLabel}</p>
        <div className="flex items-center gap-3">
          <label htmlFor={volumeId} className="shrink-0">音量</label>
          <input id={volumeId} aria-label="语音音量" className="min-w-0 flex-1 accent-primary" max="1" min="0" onChange={(event) => setVolume(Number(event.currentTarget.value))} step="0.01" type="range" value={volume} />
          <span className="w-9 text-right tabular-nums">{Math.round(volume * 100)}%</span>
        </div>
        {error && (availability === "unavailable" || state === "FAILED") ? (
          <details className="text-[12px] text-muted-foreground">
            <summary className="cursor-pointer">查看语音错误详情</summary>
            <p className="pt-2 break-words">{error}</p>
          </details>
        ) : null}
        <p className="text-[12px] text-muted-foreground">顶栏开关控制后续回复；关闭时也会停止当前播放。</p>
      </fieldset>
      <fieldset className="min-w-0 space-y-3 border-t border-border pt-4">
        <legend className="px-1 text-[12px] font-medium text-muted-foreground">聊天偏好</legend>
        <label className="flex cursor-pointer items-start gap-2.5">
          <input aria-describedby={preferenceDescriptionId} checked={adultInnuendoOptIn} className="mt-0.5 size-4 shrink-0 accent-primary" onChange={(event) => setAdultInnuendoOptIn(event.currentTarget.checked)} type="checkbox" />
          <span>我已成年，允许轻度双关</span>
        </label>
        <p id={preferenceDescriptionId} className="text-[12px] leading-relaxed text-muted-foreground">仅开放非露骨、低强度表达；明确要求停止时仍会关闭。</p>
      </fieldset>
    </div>
  );
}

export function ChatSettings() {
  const { isMobile } = useSidebar();
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const descriptionId = useId();
  const trigger = (
    <Button aria-label="聊天设置" className="text-foreground hover:bg-secondary aria-expanded:bg-secondary" size="icon-sm" variant="ghost">
      <SlidersHorizontalIcon className="size-4" />
    </Button>
  );
  const close = <Button className="mt-5 w-full" onClick={() => setOpen(false)} variant="secondary">关闭设置</Button>;

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>{trigger}</SheetTrigger>
        <SheetContent className="w-[min(360px,calc(100vw-24px))]! overflow-y-auto p-5" showCloseButton={false}>
          <SheetHeader className="p-0 pb-5">
            <SheetTitle>聊天设置</SheetTitle>
            <SheetDescription>调整语音音量与聊天偏好。</SheetDescription>
          </SheetHeader>
          <SettingsFields />
          {close}
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen} modal>
      <Popover.Trigger asChild>{trigger}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" aria-labelledby={titleId} aria-describedby={descriptionId} className="z-50 max-h-[calc(100dvh-6rem)] w-[340px] overflow-y-auto rounded-[14px] border border-border bg-popover p-5 text-popover-foreground shadow-[var(--shadow-float)]" sideOffset={8}>
          <h2 id={titleId} className="mb-1 text-[15px] font-semibold">聊天设置</h2>
          <p id={descriptionId} className="mb-5 text-[12px] text-muted-foreground">调整语音音量与聊天偏好。</p>
          <SettingsFields />
          {close}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

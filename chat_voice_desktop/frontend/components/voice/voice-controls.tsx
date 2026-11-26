"use client";

import {
  PauseIcon,
  PlayIcon,
  RefreshCcwIcon,
  SquareIcon,
  Volume2Icon,
  VolumeXIcon,
} from "lucide-react";
import { useCallback } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useVoice } from "./voice-provider";

export function VoiceControls() {
  const { availability, enabled, error, interrupt, pause, refreshRuntime, resume, setEnabled, state } =
    useVoice();
  const paused = state === "PAUSED" || state === "WAITING_USER_GESTURE";
  const togglePause = useCallback(() => {
    if (paused) {
      resume().catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "语音继续失败");
      });
    } else {
      pause();
    }
  }, [pause, paused, resume]);
  const toggleEnabled = useCallback(() => {
    setEnabled(!enabled);
  }, [enabled, setEnabled]);
  const stop = useCallback(() => {
    interrupt().catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : "语音停止失败");
    });
  }, [interrupt]);

  return (
    <div className="flex items-center gap-1">
      {enabled && availability !== "ready" && (
        <span className="text-xs text-muted-foreground" title={error} role="status">
          {availability === "loading" ? "语音初始化中" : "语音不可用"}
        </span>
      )}
      {enabled && availability === "unavailable" && (
        <Button
          aria-label="重试语音"
          onClick={() => void refreshRuntime()}
          size="icon-sm"
          title={error ? `重试语音：${error}` : "重试语音"}
          variant="ghost"
        >
          <RefreshCcwIcon className="size-4" />
        </Button>
      )}
      {enabled && ["PLAYING", "BUFFERING", "PAUSED", "WAITING_USER_GESTURE"].includes(state) && (
        <>
          <Button
            aria-label={paused ? "继续语音" : "暂停语音"}
            onClick={togglePause}
            size="icon-sm"
            title={paused ? "继续语音" : "暂停语音"}
            variant="ghost"
          >
            {paused ? (
              <PlayIcon className="size-4" />
            ) : (
              <PauseIcon className="size-4" />
            )}
          </Button>
          <Button
            aria-label="停止语音"
            onClick={stop}
            size="icon-sm"
            title="停止语音"
            variant="ghost"
          >
            <SquareIcon className="size-3.5" />
          </Button>
        </>
      )}
      <Button
        aria-label={enabled ? "关闭语音回复" : "开启语音回复"}
        aria-pressed={enabled}
        className="h-8 w-auto gap-1.5 px-2 text-foreground hover:bg-secondary"
        onClick={toggleEnabled}
        size="icon-sm"
        title={
          availability === "unavailable"
            ? "语音当前不可用；仍可保留后续回复语音偏好"
            : enabled
              ? "关闭后续回复语音"
              : "启用后续回复语音"
        }
        variant="ghost"
      >
        {enabled ? (
          <Volume2Icon className="size-4" />
        ) : (
          <VolumeXIcon className="size-4" />
        )}
        <span className="hidden text-[12px] min-[900px]:inline">语音回复</span>
      </Button>
    </div>
  );
}

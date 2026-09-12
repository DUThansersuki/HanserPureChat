"use client";

import { PauseIcon, PlayIcon, Volume2Icon, VolumeXIcon } from "lucide-react";
import { useCallback } from "react";
import { Button } from "@/components/ui/button";
import { useVoice } from "./voice-provider";

export function VoiceControls() {
  const { enabled, pause, resume, setEnabled, state } = useVoice();
  const paused = state === "PAUSED";
  const togglePause = useCallback(() => {
    if (paused) {
      resume().catch(() => undefined);
    } else {
      pause();
    }
  }, [pause, paused, resume]);
  const toggleEnabled = useCallback(() => {
    setEnabled(!enabled);
  }, [enabled, setEnabled]);

  return (
    <div className="flex items-center gap-1">
      {enabled && ["PLAYING", "BUFFERING", "PAUSED"].includes(state) && (
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
      )}
      <Button
        aria-pressed={enabled}
        onClick={toggleEnabled}
        size="icon-sm"
        title={enabled ? "关闭后续回复语音" : "启用后续回复语音"}
        variant="ghost"
      >
        {enabled ? (
          <Volume2Icon className="size-4" />
        ) : (
          <VolumeXIcon className="size-4" />
        )}
      </Button>
    </div>
  );
}

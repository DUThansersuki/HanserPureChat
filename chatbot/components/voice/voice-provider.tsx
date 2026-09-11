"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { HanserMetaData } from "@/lib/types";
import type { PlaybackSample, PlaybackState } from "@/lib/voice/contracts";
import { PlaybackController } from "@/lib/voice/playback-controller";

type VoiceContextValue = {
  enabled: boolean;
  state: PlaybackState;
  setEnabled: (enabled: boolean) => void;
  pause: () => void;
  resume: () => Promise<void>;
  replay: (replyId: string) => Promise<void>;
  canReplay: (replyId: string) => boolean;
  interrupt: () => Promise<void>;
  acceptReply: (metadata: HanserMetaData) => void;
  samplePerformance: () => PlaybackSample | undefined;
};

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const controller = useRef<PlaybackController | null>(null);
  controller.current ??= new PlaybackController();
  const [enabled, setEnabledState] = useState(false);
  const [state, setState] = useState<PlaybackState>("IDLE");
  const [replayableReplyIds, setReplayableReplyIds] = useState<
    ReadonlySet<string>
  >(new Set());

  useEffect(() => {
    setEnabledState(
      window.localStorage.getItem("hanser-speech-enabled") === "true"
    );
    return controller.current?.subscribe((nextState, _position, replyIds) => {
      setState(nextState);
      setReplayableReplyIds((current) => {
        if (
          current.size === replyIds.length &&
          replyIds.every((replyId) => current.has(replyId))
        ) {
          return current;
        }
        return new Set(replyIds);
      });
    });
  }, []);

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next);
    window.localStorage.setItem("hanser-speech-enabled", String(next));
    if (next) {
      controller.current?.resume().catch(() => undefined);
    } else {
      controller.current?.interrupt().catch(() => undefined);
    }
  }, []);

  const acceptReply = useCallback(
    (metadata: HanserMetaData) => {
      if (
        enabled &&
        metadata.replyId &&
        metadata.speech?.status === "eligible"
      ) {
        controller.current?.start(metadata.replyId).catch(() => undefined);
      }
    },
    [enabled]
  );

  const value = useMemo<VoiceContextValue>(
    () => ({
      acceptReply,
      canReplay: (replyId) => replayableReplyIds.has(replyId),
      enabled,
      interrupt: () => controller.current?.interrupt() ?? Promise.resolve(),
      pause: () => controller.current?.pause(),
      replay: (replyId) =>
        controller.current?.replay(replyId) ?? Promise.resolve(),
      resume: () => controller.current?.resume() ?? Promise.resolve(),
      samplePerformance: () => controller.current?.samplePerformance(),
      setEnabled,
      state,
    }),
    [acceptReply, enabled, replayableReplyIds, setEnabled, state]
  );

  return (
    <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>
  );
}

export function useVoice() {
  const context = useContext(VoiceContext);
  if (!context) {
    throw new Error("useVoice must be used within VoiceProvider");
  }
  return context;
}

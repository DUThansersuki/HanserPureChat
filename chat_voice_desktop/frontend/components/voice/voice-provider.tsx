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
import { toast } from "sonner";
import type { HanserMetaData } from "@/lib/types";
import type { PlaybackState } from "@/lib/voice/contracts";
import { PlaybackController } from "@/lib/voice/playback-controller";

type VoiceContextValue = {
  availability: "loading" | "ready" | "unavailable";
  enabled: boolean;
  error?: string;
  volume: number;
  state: PlaybackState;
  activeReplyId?: string;
  setEnabled: (enabled: boolean) => void;
  setVolume: (volume: number) => void;
  pause: () => void;
  resume: () => Promise<void>;
  replay: (replyId: string) => Promise<void>;
  resynthesize: (replyId: string) => Promise<void>;
  canReplay: (replyId: string) => boolean;
  interrupt: () => Promise<void>;
  acceptReply: (metadata: HanserMetaData) => void;
  refreshRuntime: () => Promise<void>;
};

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function VoiceProvider({ children }: { children: ReactNode }) {
  const controller = useRef<PlaybackController | null>(null);
  controller.current ??= new PlaybackController();
  const enabledRef = useRef(false);
  const pendingReplyEpoch = useRef(0);
  const readinessPromise = useRef<Promise<void> | null>(null);
  const readinessAborter = useRef<AbortController | null>(null);
  const [enabled, setEnabledState] = useState(false);
  const [availability, setAvailability] = useState<
    "loading" | "ready" | "unavailable"
  >("loading");
  const [error, setError] = useState<string>();
  const [volume, setVolumeState] = useState(1);
  const [state, setState] = useState<PlaybackState>("IDLE");
  const [activeReplyId, setActiveReplyId] = useState<string>();
  const [replayableReplyIds, setReplayableReplyIds] = useState<
    ReadonlySet<string>
  >(new Set());

  useEffect(() => {
    let activeController = controller.current;
    if (!activeController || activeController.isDisposed()) {
      activeController = new PlaybackController();
      controller.current = activeController;
    }
    const savedEnabled =
      window.localStorage.getItem("hanser-chat-voice:speech-enabled") === "true";
    enabledRef.current = savedEnabled;
    setEnabledState(savedEnabled);
    const storedVolume = Number(
      window.localStorage.getItem("hanser-chat-voice:master-volume") ?? "1"
    );
    const initialVolume = Number.isFinite(storedVolume)
      ? Math.max(0, Math.min(1, storedVolume))
      : 1;
    setVolumeState(initialVolume);
    activeController.setVolume(initialVolume);
    const unsubscribe = activeController.subscribe(
      (nextState, _position, replyIds, replyId, playbackError) => {
        setState(nextState);
        setError(playbackError);
        setActiveReplyId(replyId);
        setReplayableReplyIds((current) => {
          if (
            current.size === replyIds.length &&
            replyIds.every((replyId) => current.has(replyId))
          ) {
            return current;
          }
          return new Set(replyIds);
        });
      }
    );
    return () => {
      unsubscribe();
      pendingReplyEpoch.current += 1;
      readinessAborter.current?.abort();
      readinessAborter.current = null;
      readinessPromise.current = null;
      activeController.dispose();
    };
  }, []);

  const ensureVoiceReady = useCallback(() => {
    if (readinessPromise.current) return readinessPromise.current;
    const aborter = new AbortController();
    readinessAborter.current = aborter;
    const attempt = (async () => {
      await window.hanserDesktop?.startVoice();
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        if (aborter.signal.aborted) throw new DOMException("语音启动已取消", "AbortError");
        const processState = await window.hanserDesktop?.voiceProcessState();
        aborter.signal.throwIfAborted();
        if (processState === "exited") {
          throw new Error("语音进程已退出，请重试或查看日志。");
        }
        const response = await fetch("/api/voice/runtime", {
          cache: "no-store",
          signal: aborter.signal,
        });
        aborter.signal.throwIfAborted();
        if (response.ok) {
          const runtime = (await response.json()) as { voice_ready?: boolean };
          aborter.signal.throwIfAborted();
          if (runtime.voice_ready) return;
        } else if (response.status !== 503) {
          throw new Error(`语音服务状态检查失败（HTTP ${response.status}）`);
        }
        await new Promise((resolve) => setTimeout(resolve, 2_000));
      }
      throw new Error("语音模型初始化超时，请重试或查看日志。");
    })();
    readinessPromise.current = attempt;
    const clearAttempt = () => {
      if (readinessPromise.current === attempt) readinessPromise.current = null;
      if (readinessAborter.current === aborter) readinessAborter.current = null;
    };
    void attempt.then(clearAttempt, clearAttempt);
    return attempt;
  }, []);

  const refreshRuntime = useCallback(async () => {
    setAvailability("loading");
    setError(undefined);
    try {
      await ensureVoiceReady();
      setAvailability("ready");
    } catch (runtimeError) {
      if (runtimeError instanceof DOMException && runtimeError.name === "AbortError") return;
      setAvailability("unavailable");
      setError(runtimeError instanceof Error ? runtimeError.message : "语音服务不可用");
    }
  }, [ensureVoiceReady]);

  useEffect(() => {
    if (enabledRef.current) void refreshRuntime();
  }, [refreshRuntime]);

  const setEnabled = useCallback((next: boolean) => {
    enabledRef.current = next;
    setEnabledState(next);
    window.localStorage.setItem(
      "hanser-chat-voice:speech-enabled",
      String(next)
    );
    if (next) {
      void refreshRuntime();
      controller.current?.resume().catch((resumeError: unknown) => {
        const message =
          resumeError instanceof Error ? resumeError.message : "语音恢复失败";
        setError(message);
        toast.error(message);
      });
    } else {
      pendingReplyEpoch.current += 1;
      readinessAborter.current?.abort();
      readinessAborter.current = null;
      readinessPromise.current = null;
      controller.current?.interrupt().catch((interruptError: unknown) => {
        const message =
          interruptError instanceof Error
            ? interruptError.message
            : "语音停止失败";
        setError(message);
        toast.error(message);
      });
    }
  }, [refreshRuntime]);

  const setVolume = useCallback((next: number) => {
    const normalized = Math.max(0, Math.min(1, next));
    setVolumeState(normalized);
    controller.current?.setVolume(normalized);
    window.localStorage.setItem(
      "hanser-chat-voice:master-volume",
      String(normalized)
    );
  }, []);

  const acceptReply = useCallback(
    (metadata: HanserMetaData) => {
      if (enabledRef.current && metadata.replyId && metadata.speech?.status === "eligible") {
        const replyEpoch = ++pendingReplyEpoch.current;
        void (async () => {
          await ensureVoiceReady().catch((error: unknown) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              setAvailability("unavailable");
            }
            throw error;
          });
          if (replyEpoch !== pendingReplyEpoch.current || !enabledRef.current) return;
          setAvailability("ready");
          await controller.current?.start(metadata.replyId!);
        })().catch((startError: unknown) => {
          if (replyEpoch !== pendingReplyEpoch.current || !enabledRef.current) return;
          const message =
            startError instanceof Error ? startError.message : "语音生成失败";
          setError(message);
          toast.error(message);
        });
      }
    },
    [ensureVoiceReady]
  );

  const interrupt = useCallback(() => {
    pendingReplyEpoch.current += 1;
    if (readinessAborter.current && enabledRef.current) {
      setAvailability("unavailable");
      setError("语音准备已停止，可以重试。");
    }
    readinessAborter.current?.abort();
    readinessAborter.current = null;
    readinessPromise.current = null;
    return controller.current?.interrupt() ?? Promise.resolve();
  }, []);

  const replay = useCallback(async (replyId: string) => {
    setError(undefined);
    try {
      await controller.current?.replay(replyId, async (signal) => {
        await ensureVoiceReady();
        if (!signal.aborted) setAvailability("ready");
      });
    } catch (replayError) {
      setError(replayError instanceof Error ? replayError.message : "语音播放失败");
      throw replayError;
    }
  }, [ensureVoiceReady]);

  const resynthesize = useCallback(async (replyId: string) => {
    const replyEpoch = ++pendingReplyEpoch.current;
    setError(undefined);
    await ensureVoiceReady();
    if (replyEpoch !== pendingReplyEpoch.current) return;
    setAvailability("ready");
    await controller.current?.start(replyId, true);
  }, [ensureVoiceReady]);

  const value = useMemo<VoiceContextValue>(
    () => ({
      acceptReply,
      availability,
      activeReplyId,
      canReplay: (replyId) => replayableReplyIds.has(replyId),
      enabled,
      error,
      interrupt,
      pause: () => controller.current?.pause(),
      replay,
      resynthesize,
      refreshRuntime,
      resume: () => controller.current?.resume() ?? Promise.resolve(),
      setEnabled,
      setVolume,
      state,
      volume,
    }),
    [
      acceptReply,
      availability,
      activeReplyId,
      enabled,
      error,
      interrupt,
      replay,
      resynthesize,
      replayableReplyIds,
      setEnabled,
      setVolume,
      state,
      volume,
      refreshRuntime,
    ]
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

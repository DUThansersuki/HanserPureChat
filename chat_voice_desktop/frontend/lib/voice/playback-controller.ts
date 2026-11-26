import {
  cancelVoiceJob,
  createVoiceJob,
  getVoiceJob,
  getReplyVoiceJob,
  loadAudioArtifact,
  readVoiceEvents,
  reportPlayback,
} from "./client";
import type {
  PlaybackPosition,
  PlaybackState,
  SegmentPackage,
  VoiceJob,
  VoiceJobEvent,
} from "./contracts";
import { enqueueUniqueSegment } from "./segment-queue";

const MAX_CACHED_AUDIO_BUFFERS = 24;
const MAX_REPLAYABLE_REPLIES = 20;

type Listener = (
  state: PlaybackState,
  position: PlaybackPosition | undefined,
  replayableReplyIds: readonly string[],
  activeReplyId: string | undefined,
  error?: string
) => void;

export class PlaybackController {
  private state: PlaybackState = "IDLE";
  private epoch = 0;
  private jobId: string | undefined;
  private aborter: AbortController | undefined;
  private context: AudioContext | undefined;
  private source: AudioBufferSourceNode | undefined;
  private gain: GainNode | undefined;
  private queue: SegmentPackage[] = [];
  private terminal = false;
  private readonly listeners = new Set<Listener>();
  private wake: (() => void) | undefined;
  private resumeWaiter: (() => void) | undefined;
  private sourceWaiter: ((result: "ended" | "paused") => void) | undefined;
  private currentPosition: PlaybackPosition | undefined;
  private currentSegment: SegmentPackage | undefined;
  private audioOffsetSamples = 0;
  private audioStartedAt = 0;
  private playbackId = crypto.randomUUID();
  private replyId: string | undefined;
  private readonly collectedSegments = new Map<string, SegmentPackage>();
  private readonly replayableReplies = new Map<string, SegmentPackage[]>();
  private readonly audioBuffers = new Map<string, AudioBuffer>();
  private readonly seenSegmentIds = new Set<string>();
  private volume = 1;
  private disposed = false;
  private error: string | undefined;

  isDisposed() {
    return this.disposed;
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.state, this.currentPosition, [
      ...this.replayableReplies.keys(),
    ], this.replyId, this.error);
    return () => {
      this.listeners.delete(listener);
    };
  }

  sample(): PlaybackPosition | undefined {
    if (
      this.currentPosition?.phase === "audio" &&
      this.state === "PLAYING" &&
      this.context
    ) {
      const elapsed = Math.max(
        0,
        this.context.currentTime - this.audioStartedAt
      );
      return {
        ...this.currentPosition,
        sampleOffset:
          this.audioOffsetSamples +
          Math.round(
            elapsed * (this.currentSegment?.timeline.sample_rate ?? 48_000)
          ),
      };
    }
    return this.currentPosition ? { ...this.currentPosition } : undefined;
  }

  canReplay(replyId: string) {
    return this.replayableReplies.has(replyId);
  }

  setVolume(volume: number) {
    this.volume = Math.max(0, Math.min(1, volume));
    if (this.context && this.gain) {
      const now = this.context.currentTime;
      this.gain.gain.cancelScheduledValues(now);
      this.gain.gain.setTargetAtTime(this.volume, now, 0.015);
    }
  }

  async start(replyId: string, resynthesize = false) {
    await this.interrupt();
    this.epoch += 1;
    const runEpoch = this.epoch;
    this.aborter = new AbortController();
    this.queue = [];
    this.seenSegmentIds.clear();
    this.terminal = false;
    this.replyId = replyId;
    this.error = undefined;
    if (resynthesize) this.replayableReplies.delete(replyId);
    this.collectedSegments.clear();
    this.playbackId = crypto.randomUUID();
    this.setState("PREPARING");
    try {
      const job = await createVoiceJob(replyId, this.aborter.signal, resynthesize ? crypto.randomUUID() : undefined);
      if (runEpoch !== this.epoch) {
        return;
      }
      this.jobId = job.job_id;
      this.rememberSegments(job.ready_segments);
      for (const segment of job.ready_segments) {
        enqueueUniqueSegment(this.queue, this.seenSegmentIds, segment);
      }
      this.applySnapshotStatus(job.status, job.terminal_reason);
      if (runEpoch !== this.epoch) return;
      this.context ??= new AudioContext({ sampleRate: 48_000 });
      this.pump(runEpoch).catch((error: unknown) => {
        if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
          void this.failAndCancel(error);
        }
      });
      let sequence = job.last_sequence;
      while (runEpoch === this.epoch && !this.terminal) {
        // biome-ignore lint/performance/noAwaitInLoops: reconnects must preserve SSE event order.
        await readVoiceEvents(
          job.job_id,
          sequence,
          this.aborter.signal,
          (event) => {
            sequence = Math.max(sequence, event.sequence);
            this.receiveEvent(event, runEpoch);
          }
        );
        if (runEpoch !== this.epoch || this.terminal) {
          break;
        }
        const snapshot = await getVoiceJob(job.job_id, this.aborter.signal);
        sequence = Math.max(sequence, snapshot.last_sequence);
        for (const segment of snapshot.ready_segments) {
          this.rememberSegment(segment);
          enqueueUniqueSegment(this.queue, this.seenSegmentIds, segment);
        }
        this.applySnapshotStatus(snapshot.status, snapshot.terminal_reason);
        this.wake?.();
        this.wake = undefined;
      }
    } catch (error) {
      if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
        await this.failAndCancel(error);
        throw error;
      }
    }
  }

  async replay(replyId: string, prepare?: (signal: AbortSignal) => Promise<void>) {
    if (this.replyId === replyId && this.state === "PREPARING") return;
    const previousCancellation = this.interrupt();
    const runEpoch = ++this.epoch;
    const aborter = new AbortController();
    this.aborter = aborter;
    this.queue = [];
    this.seenSegmentIds.clear();
    this.terminal = true;
    this.replyId = replyId;
    this.error = undefined;
    this.jobId = undefined;
    this.playbackId = crypto.randomUUID();
    this.setState("PREPARING");
    try {
      await previousCancellation;
      if (runEpoch !== this.epoch) return;
      await prepare?.(aborter.signal);
      if (runEpoch !== this.epoch) return;
      let segments = this.replayableReplies.get(replyId);
      if (!segments) {
        const link = await getReplyVoiceJob(replyId, aborter.signal);
        if (runEpoch !== this.epoch) return;
        const job = await getVoiceJob(link.job_id, aborter.signal);
        if (runEpoch !== this.epoch) return;
        if (job.status !== "completed" || job.partial) {
          throw new Error(`这条语音未完整合成（${job.terminal_reason ?? job.status}），请重新合成`);
        }
        if (job.ready_segments.length > 0) {
          segments = [...job.ready_segments].sort((a, b) => a.index - b.index);
          this.replayableReplies.set(replyId, segments);
        }
      }
      if (!segments) throw new Error("历史音频已不可用，可选择重新合成");
      for (const segment of segments) {
        enqueueUniqueSegment(this.queue, this.seenSegmentIds, segment);
      }
      this.context ??= new AudioContext({ sampleRate: 48_000 });
      await this.context.resume();
      if (runEpoch !== this.epoch) return;
      this.setState("READY");
      this.pump(runEpoch).catch((error: unknown) => {
        if (runEpoch === this.epoch && !aborter.signal.aborted) void this.failAndCancel(error);
      });
    } catch (error) {
      if (runEpoch === this.epoch && !aborter.signal.aborted) {
        this.stopRun("FAILED", error instanceof Error ? error.message : "语音重播失败");
        throw error;
      }
    }
  }

  async resume() {
    if (!this.context) {
      return;
    }
    await this.context.resume();
    if (this.state === "PAUSED" || this.state === "WAITING_USER_GESTURE") {
      this.setState("READY");
      this.resumeWaiter?.();
      this.resumeWaiter = undefined;
    }
  }

  pause() {
    if (this.state !== "PLAYING" && this.state !== "BUFFERING") {
      return;
    }
    if (
      this.currentPosition?.phase === "audio" &&
      this.context &&
      this.source
    ) {
      const elapsed = Math.max(
        0,
        this.context.currentTime - this.audioStartedAt
      );
      this.audioOffsetSamples += Math.round(
        elapsed * (this.currentSegment?.timeline.sample_rate ?? 48_000)
      );
      this.source.onended = null;
      this.source.stop();
      this.source = undefined;
      this.sourceWaiter?.("paused");
      this.sourceWaiter = undefined;
    }
    this.setState("PAUSED");
  }

  async interrupt() {
    const previousJob = this.terminal ? undefined : this.jobId;
    const interruptEpoch = ++this.epoch;
    this.aborter?.abort();
    this.aborter = undefined;
    this.fadeAndStop();
    this.sourceWaiter?.("paused");
    this.sourceWaiter = undefined;
    this.resumeWaiter?.();
    this.resumeWaiter = undefined;
    this.wake?.();
    this.wake = undefined;
    this.queue = [];
    this.terminal = true;
    this.jobId = undefined;
    this.currentPosition = undefined;
    this.currentSegment = undefined;
    this.replyId = undefined;
    if (previousJob) {
      this.setState("INTERRUPTED");
      try {
        await cancelVoiceJob(previousJob);
      } catch (error) {
        if (interruptEpoch === this.epoch) {
          this.setState("FAILED");
          throw error;
        }
      }
    } else {
      this.setState("IDLE");
    }
  }

  dispose() {
    this.disposed = true;
    this.epoch += 1;
    this.aborter?.abort();
    this.aborter = undefined;
    this.fadeAndStop();
    this.sourceWaiter?.("paused");
    this.sourceWaiter = undefined;
    this.resumeWaiter?.();
    this.resumeWaiter = undefined;
    this.wake?.();
    this.wake = undefined;
    this.queue = [];
    this.seenSegmentIds.clear();
    this.collectedSegments.clear();
    this.replayableReplies.clear();
    this.audioBuffers.clear();
    this.listeners.clear();
    const { context } = this;
    this.context = undefined;
    context?.close().catch((error: unknown) => {
      console.error("AudioContext close failed", error);
    });
  }

  private receiveEvent(event: VoiceJobEvent, runEpoch: number) {
    if (runEpoch !== this.epoch) {
      return;
    }
    if (event.type === "segment.ready" && event.segment) {
      this.rememberSegment(event.segment);
      enqueueUniqueSegment(this.queue, this.seenSegmentIds, event.segment);
      this.wake?.();
      this.wake = undefined;
      return;
    }
    if (event.type === "turn.completed") {
      this.terminal = true;
      this.publishReplay();
      this.wake?.();
      this.wake = undefined;
    } else if (event.type === "turn.failed") {
      this.stopRun("FAILED", `语音未完整合成（${event.reason ?? "生成失败"}），可重新合成`);
    } else if (event.type === "turn.cancelled") {
      this.stopRun("INTERRUPTED", "语音合成已取消，可重新合成");
    }
  }

  private applySnapshotStatus(status: VoiceJob["status"], reason?: string) {
    if (status === "completed") {
      this.terminal = true;
      this.publishReplay();
    } else if (status === "failed") {
      this.stopRun("FAILED", `语音未完整合成（${reason ?? "生成失败"}），可重新合成`);
    } else if (status === "cancelled") {
      this.stopRun("INTERRUPTED", "语音合成已取消，可重新合成");
    }
  }

  private async pump(runEpoch: number) {
    while (runEpoch === this.epoch) {
      if (this.queue.length === 0) {
        if (this.terminal) {
          if (this.state !== "FAILED" && this.state !== "INTERRUPTED") {
            this.currentPosition = undefined;
            this.currentSegment = undefined;
            this.setState("FINISHED");
          }
          return;
        }
        this.setState(this.state === "PREPARING" ? "PREPARING" : "BUFFERING");
        // biome-ignore lint/performance/noAwaitInLoops: queue consumption is deliberately sequential.
        await new Promise<void>((resolve) => {
          this.wake = resolve;
        });
        continue;
      }
      const segment = this.queue.shift();
      if (!segment || !this.context || !this.aborter) {
        return;
      }
      let buffer = this.takeCachedAudioBuffer(segment.audio.artifact_id);
      if (!buffer) {
        buffer = await loadAudioArtifact(
          segment.audio.artifact_id,
          this.context,
          this.aborter.signal
        );
        this.cacheAudioBuffer(segment.audio.artifact_id, buffer);
      }
      if (runEpoch !== this.epoch) {
        return;
      }
      await this.ensureContextReady(runEpoch);
      if (runEpoch !== this.epoch) return;
      await this.playSegment(segment, buffer, runEpoch);
      if (runEpoch !== this.epoch) {
        return;
      }
      await this.waitGap(segment, runEpoch);
    }
  }

  private async ensureContextReady(runEpoch: number) {
    if (this.state === "PAUSED") {
      await new Promise<void>((resolve) => {
        this.resumeWaiter = resolve;
      });
    }
    if (runEpoch !== this.epoch) return;
    if (!this.context || this.context.state === "running") {
      if (this.state !== "PAUSED") {
        this.setState("READY");
      }
      return;
    }
    this.setState("WAITING_USER_GESTURE");
    await new Promise<void>((resolve) => {
      this.resumeWaiter = resolve;
    });
  }

  private async playSegment(
    segment: SegmentPackage,
    buffer: AudioBuffer,
    runEpoch: number
  ) {
    this.audioOffsetSamples = 0;
    this.currentSegment = segment;
    while (
      runEpoch === this.epoch &&
      this.audioOffsetSamples < segment.audio.sample_count
    ) {
      if (this.state === "PAUSED") {
        // biome-ignore lint/performance/noAwaitInLoops: resuming must preserve the current sample offset.
        await new Promise<void>((resolve) => {
          this.resumeWaiter = resolve;
        });
      }
      if (!this.context || runEpoch !== this.epoch) {
        return;
      }
      const source = this.context.createBufferSource();
      const gain = this.context.createGain();
      source.buffer = buffer;
      source.connect(gain).connect(this.context.destination);
      const now = this.context.currentTime;
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(this.volume, now + 0.012);
      this.source = source;
      this.gain = gain;
      this.audioStartedAt = now;
      this.currentPosition = {
        epoch: runEpoch,
        phase: "audio",
        sampleOffset: this.audioOffsetSamples,
        segmentId: segment.segment_id,
        timelineRevision: segment.timeline.revision,
      };
      this.setState("PLAYING");
      const playback = new Promise<"ended" | "paused">((resolve) => {
        this.sourceWaiter = resolve;
        source.onended = () => resolve("ended");
        source.start(0, this.audioOffsetSamples / segment.audio.sample_rate);
      });
      await this.receipt(segment, "started", "audio", this.audioOffsetSamples);
      const result = await playback;
      this.sourceWaiter = undefined;
      if (this.source === source) {
        this.source = undefined;
        this.gain = undefined;
      }
      if (result === "ended") {
        this.audioOffsetSamples = segment.audio.sample_count;
      }
    }
    if (runEpoch === this.epoch) {
      await this.receipt(
        segment,
        "completed",
        "audio",
        segment.audio.sample_count
      );
    }
  }

  private async waitGap(segment: SegmentPackage, runEpoch: number) {
    this.currentSegment = segment;
    const total = segment.timeline.pause_after_samples;
    let consumed = 0;
    this.currentPosition = {
      epoch: runEpoch,
      phase: "gap",
      sampleOffset: 0,
      segmentId: segment.segment_id,
      timelineRevision: segment.timeline.revision,
    };
    while (consumed < total && runEpoch === this.epoch) {
      if (this.state === "PAUSED") {
        // biome-ignore lint/performance/noAwaitInLoops: presentation gaps pause and resume serially.
        await new Promise<void>((resolve) => {
          this.resumeWaiter = resolve;
        });
        continue;
      }
      this.setState("PLAYING");
      const chunk = Math.min(960, total - consumed);
      await new Promise((resolve) =>
        setTimeout(resolve, (chunk / segment.timeline.sample_rate) * 1000)
      );
      consumed += chunk;
      if (this.currentPosition) {
        this.currentPosition.sampleOffset = consumed;
      }
    }
    if (runEpoch === this.epoch) {
      await this.receipt(segment, "completed", "gap", consumed);
    }
  }

  private receipt(
    segment: SegmentPackage,
    status: "started" | "completed",
    phase: "audio" | "gap",
    sampleOffset: number
  ) {
    if (!this.jobId) {
      return Promise.resolve();
    }
    return reportPlayback(this.jobId, {
      epoch: this.epoch,
      event_id: crypto.randomUUID(),
      phase,
      playback_id: this.playbackId,
      sample_offset: sampleOffset,
      segment_id: segment.segment_id,
      status,
    });
  }

  private stopRun(state: "FAILED" | "INTERRUPTED", error: string) {
    ++this.epoch;
    this.terminal = true;
    this.aborter?.abort();
    this.fadeAndStop();
    this.sourceWaiter?.("paused");
    this.sourceWaiter = undefined;
    this.resumeWaiter?.();
    this.resumeWaiter = undefined;
    this.wake?.();
    this.wake = undefined;
    this.queue = [];
    this.currentPosition = undefined;
    this.currentSegment = undefined;
    this.error = error;
    this.setState(state);
    this.emit();
  }

  private async failAndCancel(error: unknown) {
    const jobId = this.terminal ? undefined : this.jobId;
    this.stopRun("FAILED", error instanceof Error ? error.message : "语音播放失败");
    const failureEpoch = this.epoch;
    if (jobId) {
      try {
        await cancelVoiceJob(jobId);
      } catch (cancelError) {
        if (failureEpoch === this.epoch) {
          this.error += `；任务取消失败：${cancelError instanceof Error ? cancelError.message : String(cancelError)}`;
          this.emit();
        }
      }
    }
  }

  private fadeAndStop() {
    if (!this.context || !this.source) {
      return;
    }
    const now = this.context.currentTime;
    const { gain } = this;
    gain?.gain.cancelScheduledValues(now);
    if (gain) {
      gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(0, now + 0.08);
    }
    this.source.onended = null;
    this.source.stop(now + 0.08);
    this.source = undefined;
    this.gain = undefined;
  }

  private setState(state: PlaybackState) {
    if (this.state === state) {
      return;
    }
    this.state = state;
    this.emit();
  }

  private emit() {
    for (const listener of this.listeners) {
      listener(this.state, this.currentPosition, [
        ...this.replayableReplies.keys(),
      ], this.replyId, this.error);
    }
  }

  private rememberSegment(segment: SegmentPackage) {
    this.collectedSegments.set(segment.segment_id, segment);
  }

  private rememberSegments(segments: SegmentPackage[]) {
    for (const segment of segments) {
      this.rememberSegment(segment);
    }
  }

  private publishReplay() {
    if (!(this.replyId && this.collectedSegments.size > 0)) {
      return;
    }
    this.replayableReplies.delete(this.replyId);
    this.replayableReplies.set(
      this.replyId,
      [...this.collectedSegments.values()].sort((a, b) => a.index - b.index)
    );
    while (this.replayableReplies.size > MAX_REPLAYABLE_REPLIES) {
      const oldestReplyId = this.replayableReplies.keys().next().value;
      if (oldestReplyId === undefined) {
        break;
      }
      this.replayableReplies.delete(oldestReplyId);
    }
    this.emit();
  }

  private takeCachedAudioBuffer(artifactId: string) {
    const buffer = this.audioBuffers.get(artifactId);
    if (buffer) {
      this.audioBuffers.delete(artifactId);
      this.audioBuffers.set(artifactId, buffer);
    }
    return buffer;
  }

  private cacheAudioBuffer(artifactId: string, buffer: AudioBuffer) {
    this.audioBuffers.set(artifactId, buffer);
    while (this.audioBuffers.size > MAX_CACHED_AUDIO_BUFFERS) {
      const oldestArtifactId = this.audioBuffers.keys().next().value;
      if (oldestArtifactId === undefined) {
        break;
      }
      this.audioBuffers.delete(oldestArtifactId);
    }
  }
}

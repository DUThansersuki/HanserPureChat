import {
  cancelVoiceJob,
  createVoiceJob,
  getVoiceJob,
  loadAudioArtifact,
  readVoiceEvents,
  reportPlayback,
} from "./client";
import type {
  PlaybackPosition,
  PlaybackSample,
  PlaybackState,
  SegmentPackage,
  VoiceJob,
  VoiceJobEvent,
} from "./contracts";

type Listener = (
  state: PlaybackState,
  position: PlaybackPosition | undefined,
  replayableReplyIds: readonly string[]
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

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.state, this.currentPosition, [
      ...this.replayableReplies.keys(),
    ]);
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

  samplePerformance(): PlaybackSample | undefined {
    const position = this.sample();
    if (!(position && this.currentSegment)) {
      return;
    }
    return { position, segment: this.currentSegment };
  }

  canReplay(replyId: string) {
    return this.replayableReplies.has(replyId);
  }

  async start(replyId: string) {
    await this.interrupt();
    this.epoch += 1;
    const runEpoch = this.epoch;
    this.aborter = new AbortController();
    this.queue = [];
    this.terminal = false;
    this.replyId = replyId;
    this.collectedSegments.clear();
    this.playbackId = crypto.randomUUID();
    this.setState("PREPARING");
    try {
      const job = await createVoiceJob(replyId, this.aborter.signal);
      if (runEpoch !== this.epoch) {
        return;
      }
      this.jobId = job.job_id;
      this.rememberSegments(job.ready_segments);
      this.queue.push(...job.ready_segments.sort((a, b) => a.index - b.index));
      this.applySnapshotStatus(job.status);
      this.context ??= new AudioContext({ sampleRate: 48_000 });
      this.pump(runEpoch).catch(() => {
        if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
          this.terminal = true;
          this.setState("FAILED");
          this.aborter?.abort();
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
          if (
            !this.queue.some((item) => item.segment_id === segment.segment_id)
          ) {
            this.queue.push(segment);
          }
        }
        this.queue.sort((a, b) => a.index - b.index);
        this.applySnapshotStatus(snapshot.status);
        this.wake?.();
        this.wake = undefined;
      }
    } catch (error) {
      if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
        this.setState("FAILED");
        throw error;
      }
    }
  }

  async replay(replyId: string) {
    const segments = this.replayableReplies.get(replyId);
    if (!segments) {
      throw new Error("语音尚未准备就绪");
    }
    await this.interrupt();
    this.epoch += 1;
    const runEpoch = this.epoch;
    this.aborter = new AbortController();
    this.queue = [...segments];
    this.terminal = true;
    this.replyId = replyId;
    this.jobId = undefined;
    this.playbackId = crypto.randomUUID();
    this.context ??= new AudioContext({ sampleRate: 48_000 });
    await this.context.resume();
    this.setState("READY");
    this.pump(runEpoch).catch(() => {
      if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
        this.setState("FAILED");
      }
    });
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
    this.terminal = true;
    this.jobId = undefined;
    this.currentPosition = undefined;
    this.currentSegment = undefined;
    this.replyId = undefined;
    if (previousJob) {
      this.setState("INTERRUPTED");
      await cancelVoiceJob(previousJob).catch(() => undefined);
    } else {
      this.setState("IDLE");
    }
  }

  private receiveEvent(event: VoiceJobEvent, runEpoch: number) {
    if (runEpoch !== this.epoch) {
      return;
    }
    if (event.type === "segment.ready" && event.segment) {
      this.rememberSegment(event.segment);
      if (
        !this.queue.some(
          (item) => item.segment_id === event.segment?.segment_id
        )
      ) {
        this.queue.push(event.segment);
        this.queue.sort((a, b) => a.index - b.index);
      }
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
      this.terminal = true;
      this.currentPosition = undefined;
      this.currentSegment = undefined;
      this.setState("FAILED");
      this.wake?.();
      this.wake = undefined;
    } else if (event.type === "turn.cancelled") {
      this.terminal = true;
      this.setState("INTERRUPTED");
      this.wake?.();
      this.wake = undefined;
    }
  }

  private applySnapshotStatus(status: VoiceJob["status"]) {
    if (status === "completed") {
      this.terminal = true;
      this.publishReplay();
    } else if (status === "failed") {
      this.terminal = true;
      this.queue = [];
      this.currentPosition = undefined;
      this.currentSegment = undefined;
      this.setState("FAILED");
    } else if (status === "cancelled") {
      this.terminal = true;
      this.queue = [];
      this.setState("INTERRUPTED");
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
      let buffer = this.audioBuffers.get(segment.audio.artifact_id);
      if (!buffer) {
        buffer = await loadAudioArtifact(
          segment.audio.artifact_id,
          this.context,
          this.aborter.signal
        );
        this.audioBuffers.set(segment.audio.artifact_id, buffer);
      }
      if (runEpoch !== this.epoch) {
        return;
      }
      await this.ensureContextReady();
      await this.playSegment(segment, buffer, runEpoch);
      if (runEpoch !== this.epoch) {
        return;
      }
      await this.waitGap(segment, runEpoch);
    }
  }

  private async ensureContextReady() {
    if (this.state === "PAUSED") {
      await new Promise<void>((resolve) => {
        this.resumeWaiter = resolve;
      });
    }
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
      gain.gain.linearRampToValueAtTime(1, now + 0.012);
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
      this.receipt(segment, "started", "audio", this.audioOffsetSamples).catch(
        () => undefined
      );
      const result = await new Promise<"ended" | "paused">((resolve) => {
        this.sourceWaiter = resolve;
        source.onended = () => resolve("ended");
        source.start(0, this.audioOffsetSamples / segment.audio.sample_rate);
      });
      this.sourceWaiter = undefined;
      if (result === "ended") {
        this.audioOffsetSamples = segment.audio.sample_count;
      }
    }
    if (runEpoch === this.epoch) {
      this.receipt(
        segment,
        "completed",
        "audio",
        segment.audio.sample_count
      ).catch(() => undefined);
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
        this.emit();
      }
    }
    if (runEpoch === this.epoch) {
      this.receipt(segment, "completed", "gap", consumed).catch(
        () => undefined
      );
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
    this.state = state;
    this.emit();
  }

  private emit() {
    for (const listener of this.listeners) {
      listener(this.state, this.currentPosition, [
        ...this.replayableReplies.keys(),
      ]);
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
    this.replayableReplies.set(
      this.replyId,
      [...this.collectedSegments.values()].sort((a, b) => a.index - b.index)
    );
    this.emit();
  }
}

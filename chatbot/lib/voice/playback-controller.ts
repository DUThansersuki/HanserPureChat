import {
  cancelVoiceJob,
  createVoiceJob,
  loadAudioArtifact,
  readVoiceEvents,
  reportPlayback,
} from "./client";
import type {
  PlaybackPosition,
  PlaybackState,
  SegmentPackage,
  VoiceJobEvent,
} from "./contracts";

type Listener = (state: PlaybackState, position?: PlaybackPosition) => void;

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
  private audioOffsetSamples = 0;
  private audioStartedAt = 0;
  private playbackId = crypto.randomUUID();

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.state, this.currentPosition);
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
        sampleOffset: this.audioOffsetSamples + Math.round(elapsed * 48_000),
      };
    }
    return this.currentPosition ? { ...this.currentPosition } : undefined;
  }

  async start(replyId: string) {
    await this.interrupt();
    this.epoch += 1;
    const runEpoch = this.epoch;
    this.aborter = new AbortController();
    this.queue = [];
    this.terminal = false;
    this.playbackId = crypto.randomUUID();
    this.setState("PREPARING");
    try {
      const job = await createVoiceJob(replyId, this.aborter.signal);
      if (runEpoch !== this.epoch) {
        return;
      }
      this.jobId = job.job_id;
      this.queue.push(...job.ready_segments.sort((a, b) => a.index - b.index));
      this.context ??= new AudioContext({ sampleRate: 48_000 });
      this.pump(runEpoch).catch(() => {
        if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
          this.terminal = true;
          this.setState("FAILED");
          this.aborter?.abort();
        }
      });
      await readVoiceEvents(
        job.job_id,
        job.last_sequence,
        this.aborter.signal,
        (event) => this.receiveEvent(event, runEpoch)
      );
    } catch (error) {
      if (runEpoch === this.epoch && !this.aborter?.signal.aborted) {
        this.setState("FAILED");
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
      this.audioOffsetSamples += Math.round(elapsed * 48_000);
      this.source.onended = null;
      this.source.stop();
      this.source = undefined;
      this.sourceWaiter?.("paused");
      this.sourceWaiter = undefined;
    }
    this.setState("PAUSED");
  }

  async interrupt() {
    const previousJob = this.jobId;
    this.epoch += 1;
    this.aborter?.abort();
    this.aborter = undefined;
    this.fadeAndStop();
    this.resumeWaiter?.();
    this.resumeWaiter = undefined;
    this.wake?.();
    this.wake = undefined;
    this.queue = [];
    this.terminal = true;
    this.jobId = undefined;
    this.currentPosition = undefined;
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
      this.wake?.();
      this.wake = undefined;
    } else if (event.type === "turn.failed") {
      this.terminal = true;
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

  private async pump(runEpoch: number) {
    while (runEpoch === this.epoch) {
      if (this.queue.length === 0) {
        if (this.terminal) {
          if (this.state !== "FAILED" && this.state !== "INTERRUPTED") {
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
      const buffer = await loadAudioArtifact(
        segment.audio.artifact_id,
        this.context,
        this.aborter.signal
      );
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
    if (!this.context || this.context.state === "running") {
      this.setState("READY");
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
      this.source = source;
      this.gain = gain;
      this.audioStartedAt = this.context.currentTime;
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
        setTimeout(resolve, (chunk / 48_000) * 1000)
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
      listener(this.state, this.currentPosition);
    }
  }
}

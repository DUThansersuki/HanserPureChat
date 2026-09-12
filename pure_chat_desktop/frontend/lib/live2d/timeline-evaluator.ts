import type { PlaybackPosition, SegmentPackage } from "../voice/contracts";

export type Live2DFrame = {
  mouthOpen: number;
  expressionPreset: string;
  expressionWeight: number;
  motion: string;
};

type AmplitudeCue = { start_sample: number; end_sample: number; value: number };

export function evaluateAudioFrame(
  segment: SegmentPackage & {
    timeline: SegmentPackage["timeline"] & {
      mouth?: AmplitudeCue[];
      expression?: Record<string, unknown>;
    };
  },
  position: PlaybackPosition
): Live2DFrame {
  const expression = segment.timeline.expression ?? {};
  const sampleRate = segment.timeline.sample_rate;
  const elapsedMs = (position.sampleOffset / sampleRate) * 1000;
  const remainingMs =
    ((segment.audio.sample_count - position.sampleOffset) / sampleRate) * 1000;
  const attackMs = Math.max(0, Number(expression.attack_ms ?? 160));
  const releaseMs = Math.max(0, Number(expression.release_ms ?? 240));
  const attack = attackMs ? Math.min(1, elapsedMs / attackMs) : 1;
  const release = releaseMs ? Math.min(1, remainingMs / releaseMs) : 1;
  const expressionEnvelope = Math.max(0, Math.min(attack, release));
  const mouthOpen =
    position.phase === "audio"
      ? ((segment.timeline.mouth ?? []).find(
          (cue) =>
            cue.start_sample <= position.sampleOffset &&
            position.sampleOffset < cue.end_sample
        )?.value ?? 0)
      : 0;
  return {
    expressionPreset: String(expression.preset ?? "neutral"),
    expressionWeight: Number(expression.weight ?? 0) * expressionEnvelope,
    motion: String(expression.motion ?? "none"),
    mouthOpen,
  };
}

export function evaluatePresentationFrame(
  plan: {
    preset: string;
    weight: number;
    motion: string;
    attack_ms: number;
    hold_ms: number;
    release_ms: number;
  },
  elapsedMs: number
): Live2DFrame {
  const attackEnd = plan.attack_ms;
  const holdEnd = attackEnd + plan.hold_ms;
  const releaseEnd = holdEnd + plan.release_ms;
  let envelope = 0;
  if (elapsedMs < attackEnd) {
    envelope = attackEnd ? elapsedMs / attackEnd : 1;
  } else if (elapsedMs < holdEnd) {
    envelope = 1;
  } else if (elapsedMs < releaseEnd) {
    envelope = plan.release_ms
      ? 1 - (elapsedMs - holdEnd) / plan.release_ms
      : 0;
  }
  return {
    expressionPreset: plan.preset,
    expressionWeight: Math.max(0, Math.min(1, plan.weight * envelope)),
    motion: plan.motion,
    mouthOpen: 0,
  };
}

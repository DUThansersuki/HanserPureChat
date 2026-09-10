export const MMD_MOTION_NAMES = ["idle", "greet", "nod", "curious"] as const;

export type MmdMotionName = (typeof MMD_MOTION_NAMES)[number];

export type BoneRotation = readonly [x: number, y: number, z: number];

export type MmdPose = {
  bones: Record<string, BoneRotation>;
  morphs: Record<string, number>;
};

export const MMD_MOTION_LABELS: Record<MmdMotionName, string> = {
  curious: "好奇歪头",
  greet: "挥手问候",
  idle: "待机呼吸",
  nod: "点头回应",
};

export const MMD_MOTION_DURATION_MS: Record<MmdMotionName, number> = {
  curious: 3200,
  greet: 3600,
  idle: Number.POSITIVE_INFINITY,
  nod: 2800,
};

const TAU = Math.PI * 2;

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function smootherStep(value: number) {
  const t = clamp01(value);
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function actionEnvelope(elapsedMs: number, durationMs: number) {
  const attack = smootherStep(elapsedMs / 420);
  const release = smootherStep((durationMs - elapsedMs) / 560);
  return Math.min(attack, release);
}

function blinkWeight(elapsedMs: number) {
  const phase = elapsedMs % 4300;
  if (phase < 110) {
    return smootherStep(phase / 110);
  }
  if (phase < 180) {
    return 1;
  }
  if (phase < 320) {
    return 1 - smootherStep((phase - 180) / 140);
  }
  return 0;
}

export function sampleMmdPose(
  motion: MmdMotionName,
  elapsedMs: number
): MmdPose {
  const idleTime = elapsedMs / 1000;
  const breath = Math.sin(idleTime * 1.35);
  const sway = Math.sin(idleTime * 0.72);
  const bones: Record<string, BoneRotation> = {
    上半身: [0.018 * breath, 0, 0.018 * sway],
    上半身2: [0.012 * breath, 0.012 * sway, 0],
    右ひじ: [0, 0, 0.04],
    右腕: [0, 0.08, 0.9],
    左ひじ: [0, 0, -0.04],
    左腕: [0, -0.08, -0.9],
    頭: [0.008 * breath, 0.018 * sway, -0.008 * sway],
  };
  const morphs: Record<string, number> = {
    まばたき: blinkWeight(elapsedMs),
  };

  if (motion === "idle") {
    return { bones, morphs };
  }

  const duration = MMD_MOTION_DURATION_MS[motion];
  const progress = clamp01(elapsedMs / duration);
  const envelope = actionEnvelope(elapsedMs, duration);

  if (motion === "greet") {
    const wave = Math.sin(progress * TAU * 4.5);
    bones.上半身 = [0.01 * breath, -0.06 * envelope, 0.035 * envelope];
    bones.頭 = [-0.03 * envelope, 0.08 * envelope, -0.08 * envelope];
    bones.右腕 = [
      -0.15 * envelope,
      0.08 + (0.02 - 0.08) * envelope,
      0.9 + (0.58 - 0.9) * envelope + 0.035 * wave * envelope,
    ];
    bones.右ひじ = [
      0,
      0.05 * wave * envelope,
      0.04 + (-2.25 - 0.04) * envelope,
    ];
    bones.右手首 = [0, 0.12 * wave * envelope, 0.32 * wave * envelope];
    morphs.にこり = 0.55 * envelope;
    morphs.にやり = 0.3 * envelope;
  } else if (motion === "nod") {
    const nod = Math.sin(progress * TAU * 2) * envelope;
    bones.頭 = [0.18 * Math.max(0, nod) - 0.08 * Math.max(0, -nod), 0, 0];
    bones.首 = [0.055 * nod, 0, 0];
    morphs.にこり = 0.42 * envelope;
  } else {
    const settle = 0.9 + 0.1 * Math.sin(progress * Math.PI);
    bones.上半身 = [0.01 * breath, -0.035 * envelope, -0.055 * envelope];
    bones.上半身2 = [0, -0.045 * envelope, -0.055 * envelope];
    bones.頭 = [-0.025 * envelope, 0.12 * envelope, 0.2 * envelope * settle];
    morphs.困る = 0.28 * envelope;
    morphs.にこり = 0.16 * envelope;
  }

  return { bones, morphs };
}

export function isMmdMotionComplete(motion: MmdMotionName, elapsedMs: number) {
  return elapsedMs >= MMD_MOTION_DURATION_MS[motion];
}

export const MMD_MOTION_NAMES = ["idle", "greet", "curious", "peace"] as const;

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
  peace: "比耶 Wink",
};

export const MMD_MOTION_DURATION_MS: Record<MmdMotionName, number> = {
  curious: 3200,
  greet: 3600,
  idle: Number.POSITIVE_INFINITY,
  peace: 3800,
};

export const MMD_IDLE_ACTION_SEQUENCE = ["greet", "curious", "peace"] as const;

export const MMD_IDLE_ACTION_INTERVAL_MS = [7000, 9000, 8000] as const;

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
  elapsedMs: number,
  idleElapsedMs = elapsedMs
): MmdPose {
  const idleTime = idleElapsedMs / 1000;
  const breath = Math.sin(idleTime * 1.35);
  const sway = Math.sin(idleTime * 0.72);
  const bones: Record<string, BoneRotation> = {
    上半身: [0.018 * breath, 0, 0.018 * sway],
    上半身2: [0.012 * breath, 0.012 * sway, 0],
    右ひじ: [-0.035, 0.018, -0.105],
    右手捩: [0.018, 0.012, -0.014],
    右手首: [0.018, -0.012, 0.026],
    右肩: [0.012, -0.008, 0.028],
    右腕: [-0.042, 0.055, 0.84],
    // This PMX uses the same delta axes on both sides: negative Z folds the
    // left forearm back toward the torso instead of away from it.
    左ひじ: [0, 0, -1.35],
    左手捩: [-0.16, 0, 0],
    左手首: [-0.05, 0.05, -0.12],
    左肩: [0, 0, -0.02],
    左腕: [0, -0.18, -0.1],
    頭: [0.008 * breath, 0.018 * sway, -0.008 * sway],
  };
  const morphs: Record<string, number> = {
    にこり: 0.16 + 0.018 * breath,
    にやり: 0.07 + 0.01 * breath,
    まばたき: blinkWeight(idleElapsedMs),
  };

  if (motion === "idle") {
    return { bones, morphs };
  }

  const duration = MMD_MOTION_DURATION_MS[motion];
  const progress = clamp01(elapsedMs / duration);
  const envelope = actionEnvelope(elapsedMs, duration);
  const relaxedSmile = morphs.にこり;
  const relaxedMouthSmile = morphs.にやり;

  if (motion === "greet") {
    const wave = Math.sin(progress * TAU * 4.5);
    const relaxedElbow = bones.右ひじ;
    const relaxedTwist = bones.右手捩;
    const relaxedWrist = bones.右手首;
    const relaxedArm = bones.右腕;
    const relaxedUpperBody = bones.上半身;
    const relaxedHead = bones.頭;
    bones.上半身 = [
      relaxedUpperBody[0] + (0.01 * breath - relaxedUpperBody[0]) * envelope,
      relaxedUpperBody[1] + (-0.06 - relaxedUpperBody[1]) * envelope,
      relaxedUpperBody[2] + (0.035 - relaxedUpperBody[2]) * envelope,
    ];
    bones.頭 = [
      relaxedHead[0] + (-0.03 - relaxedHead[0]) * envelope,
      relaxedHead[1] + (0.08 - relaxedHead[1]) * envelope,
      relaxedHead[2] + (-0.08 - relaxedHead[2]) * envelope,
    ];
    bones.右腕 = [
      relaxedArm[0] + (-0.15 - relaxedArm[0]) * envelope,
      relaxedArm[1] + (0.02 - relaxedArm[1]) * envelope,
      relaxedArm[2] +
        (0.58 - relaxedArm[2]) * envelope +
        0.035 * wave * envelope,
    ];
    bones.右ひじ = [
      relaxedElbow[0] * (1 - envelope),
      relaxedElbow[1] * (1 - envelope),
      relaxedElbow[2] +
        (-2.35 - relaxedElbow[2]) * envelope +
        0.08 * wave * envelope,
    ];
    bones.右手捩 = [
      // Positive rotation around the PMX fixed axis rolls the palm forward.
      relaxedTwist[0] + (1.17 - relaxedTwist[0]) * envelope,
      relaxedTwist[1] * (1 - envelope),
      relaxedTwist[2] * (1 - envelope),
    ];
    bones.右手首 = [
      relaxedWrist[0] * (1 - envelope),
      relaxedWrist[1] * (1 - envelope),
      relaxedWrist[2] * (1 - envelope),
    ];
    morphs.にこり = relaxedSmile + (0.55 - relaxedSmile) * envelope;
    morphs.にやり = relaxedMouthSmile + (0.3 - relaxedMouthSmile) * envelope;
  } else if (motion === "curious") {
    const settle = 0.9 + 0.1 * Math.sin(progress * Math.PI);
    const relaxedUpperBody = bones.上半身;
    const relaxedUpperBody2 = bones.上半身2;
    const relaxedHead = bones.頭;
    bones.上半身 = [
      relaxedUpperBody[0] + (0.01 * breath - relaxedUpperBody[0]) * envelope,
      relaxedUpperBody[1] + (-0.015 - relaxedUpperBody[1]) * envelope,
      relaxedUpperBody[2] + (-0.012 - relaxedUpperBody[2]) * envelope,
    ];
    bones.上半身2 = [
      relaxedUpperBody2[0] * (1 - envelope),
      relaxedUpperBody2[1] + (-0.018 - relaxedUpperBody2[1]) * envelope,
      relaxedUpperBody2[2] + (-0.015 - relaxedUpperBody2[2]) * envelope,
    ];
    bones.頭 = [
      relaxedHead[0] + (-0.025 - relaxedHead[0]) * envelope,
      relaxedHead[1] + (0.11 - relaxedHead[1]) * envelope,
      relaxedHead[2] + (0.27 * settle - relaxedHead[2]) * envelope,
    ];
    morphs.困る = 0.28 * envelope;
    morphs.にこり = relaxedSmile + (0.12 - relaxedSmile) * envelope;
  } else {
    const relaxedArm = bones.右腕;
    const relaxedElbow = bones.右ひじ;
    const relaxedTwist = bones.右手捩;
    const relaxedWrist = bones.右手首;
    const relaxedHead = bones.頭;
    bones.右腕 = [
      relaxedArm[0] + (-0.2 - relaxedArm[0]) * envelope,
      relaxedArm[1] + (-0.04 - relaxedArm[1]) * envelope,
      relaxedArm[2] + (0.76 - relaxedArm[2]) * envelope,
    ];
    bones.右ひじ = [
      relaxedElbow[0] * (1 - envelope),
      relaxedElbow[1] * (1 - envelope),
      relaxedElbow[2] + (-2.72 - relaxedElbow[2]) * envelope,
    ];
    bones.右手捩 = [
      relaxedTwist[0] + (1.14 - relaxedTwist[0]) * envelope,
      relaxedTwist[1] * (1 - envelope),
      relaxedTwist[2] * (1 - envelope),
    ];
    bones.右手首 = [
      relaxedWrist[0] * (1 - envelope),
      relaxedWrist[1] * (1 - envelope),
      relaxedWrist[2] * (1 - envelope),
    ];
    bones.右親指０ = [0, -0.82 * envelope, 0];
    bones.右親指１ = [0, -0.46 * envelope, 0];
    bones.右親指２ = [0, -0.22 * envelope, 0];
    bones.右人指１ = [0, 0.3 * envelope, 0];
    bones.右中指１ = [0, -0.24 * envelope, 0];
    for (const name of ["右薬指１", "右薬指２", "右小指１", "右小指２"]) {
      bones[name] = [0, 0, -0.78 * envelope];
    }
    bones.右薬指３ = [0, 0, -0.52 * envelope];
    bones.右小指３ = [0, 0, -0.58 * envelope];
    bones.頭 = [
      relaxedHead[0] + (-0.02 - relaxedHead[0]) * envelope,
      relaxedHead[1] + (-0.04 - relaxedHead[1]) * envelope,
      relaxedHead[2] + (0.12 - relaxedHead[2]) * envelope,
    ];
    morphs.ウィンク右 = 0.96 * envelope;
    morphs.まばたき *= 1 - smootherStep(envelope / 0.08);
    morphs.にこり = relaxedSmile + (0.62 - relaxedSmile) * envelope;
    morphs.にやり = relaxedMouthSmile + (0.34 - relaxedMouthSmile) * envelope;
  }

  return { bones, morphs };
}

export function isMmdMotionComplete(motion: MmdMotionName, elapsedMs: number) {
  return elapsedMs >= MMD_MOTION_DURATION_MS[motion];
}

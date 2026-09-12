export const MMD_MOTION_NAMES = [
  "idle",
  "greet",
  "curious",
  "peace",
  "singing",
] as const;

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
  singing: "闭眼歌唱",
};

export const MMD_MOTION_DURATION_MS: Record<MmdMotionName, number> = {
  curious: 3200,
  greet: 3600,
  idle: Number.POSITIVE_INFINITY,
  peace: 3800,
  singing: Number.POSITIVE_INFINITY,
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
  } else if (motion === "peace") {
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
  } else if (motion === "singing") {
    const singingEnvelope = smootherStep(elapsedMs / 2200);
    const singingSway = Math.sin(idleTime * 0.48);
    const singingBreath = Math.sin(idleTime * 1.12);
    bones.上半身 = [
      0.015 + 0.012 * singingBreath,
      0.018 * singingSway,
      0.022 * singingSway,
    ];
    bones.上半身2 = [0.01 + 0.008 * singingBreath, 0.012 * singingSway, 0];
    bones.頭 = [0.035 + 0.008 * singingBreath, 0, -0.018 * singingSway];

    const singingBones: Record<string, BoneRotation> = {
      右ひじ: [0.793, -0.003, 2.356],
      右手捩: [-0.42, 0, 0],
      右手首: [-0.2, 0.1, 1.1],
      右肩: [0.004, -0.031, -0.083],
      右腕: [-0.628, -0.539, 0.468],
      右腕捩: [0.076, 0, 0],
      左ひじ: [0.666, 0.002, -2.301],
      左手捩: [0.3, 0, 0],
      左手首: [-0.18, -0.07, -1.14],
      左肩: [0.005, 0.032, 0.085],
      左腕: [-0.6, 0.514, -0.48],
      左腕捩: [-0.145, 0, 0],
    };
    for (const [name, target] of Object.entries(singingBones)) {
      const relaxed = bones[name] ?? [0, 0, 0];
      bones[name] = [
        relaxed[0] + (target[0] - relaxed[0]) * singingEnvelope,
        relaxed[1] + (target[1] - relaxed[1]) * singingEnvelope,
        relaxed[2] + (target[2] - relaxed[2]) * singingEnvelope,
      ];
    }
    const fingerCurls = [
      ["人指", [0.36, 0.64, 0.24]],
      ["中指", [0.46, 0.74, 0.29]],
      ["薬指", [0.6, 0.86, 0.35]],
      ["小指", [0.74, 0.98, 0.42]],
    ] as const;
    for (const side of ["右", "左"] as const) {
      const mirror = side === "右" ? -1 : 1;
      for (const [index, [finger, [mcp, pip, dip]]] of fingerCurls.entries()) {
        // Fan the four fingers in anatomical order so the opposite hand can
        // occupy the gaps. A smaller alternating twist supplies the depth
        // order without turning the fingertips into a radial knot.
        const weave = (-0.15 + index * 0.1) * mirror;
        const layer = (index % 2 === 0 ? 0.09 : -0.075) * mirror;
        bones[`${side}${finger}１`] = [
          layer * singingEnvelope,
          weave * singingEnvelope,
          mirror * mcp * singingEnvelope,
        ];
        bones[`${side}${finger}２`] = [
          -layer * 0.45 * singingEnvelope,
          -weave * 0.3 * singingEnvelope,
          mirror * pip * singingEnvelope,
        ];
        bones[`${side}${finger}３`] = [0, 0, mirror * dip * singingEnvelope];
      }
      const thumbDepth = side === "右" ? 0.13 : -0.05;
      bones[`${side}親指０`] = [
        0,
        thumbDepth * singingEnvelope,
        mirror * (side === "右" ? 1.08 : 1.3) * singingEnvelope,
      ];
      bones[`${side}親指１`] = [
        0,
        mirror * (side === "右" ? 0.32 : 0.24) * singingEnvelope,
        mirror * (side === "右" ? 0.16 : 0.12) * singingEnvelope,
      ];
      bones[`${side}親指２`] = [0, 0, mirror * 0.12 * singingEnvelope];
    }
    morphs.まばたき = singingEnvelope;
    morphs.にこり = relaxedSmile + (0.28 - relaxedSmile) * singingEnvelope;
    morphs.にやり =
      relaxedMouthSmile + (0.12 - relaxedMouthSmile) * singingEnvelope;
  }

  return { bones, morphs };
}

export function isMmdMotionComplete(motion: MmdMotionName, elapsedMs: number) {
  return elapsedMs >= MMD_MOTION_DURATION_MS[motion];
}

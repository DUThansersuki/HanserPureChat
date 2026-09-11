import { type Bone, Euler, Quaternion, type SkinnedMesh, Vector3 } from "three";
import {
  type GrantSolver,
  MMDAnimationHelper,
} from "three/addons/animation/MMDAnimationHelper.js";
import {
  isMmdMotionComplete,
  MMD_IDLE_ACTION_INTERVAL_MS,
  MMD_IDLE_ACTION_SEQUENCE,
  MMD_MOTION_DURATION_MS,
  MMD_MOTION_NAMES,
  type MmdMotionName,
  sampleMmdPose,
} from "./mmd-motion-library";
import { ParameterOwner, type RigAdapter } from "./rig-adapter";
import type { Live2DFrame } from "./timeline-evaluator";

const EXPRESSION_MORPHS: Record<string, string> = {
  attentive: "まじめ",
  curious: "困る",
  gentle: "にこり",
  happy: "にこり",
  neutral: "",
  sad: "困る",
  soft_smile: "にこり",
  surprised: "びっくり",
};

const FIXED_BONE_AXES = new Map<string, Vector3>([
  ["右手捩", new Vector3(-0.827_429, -0.558_297, -0.060_552).normalize()],
  ["右腕捩", new Vector3(-0.794_183, -0.607_069, 0.027_227).normalize()],
  ["左手捩", new Vector3(0.827_428, -0.558_298, -0.060_552).normalize()],
  ["左腕捩", new Vector3(0.794_183, -0.607_069, 0.027_227).normalize()],
]);

export class MmdRigAdapter implements RigAdapter {
  readonly capabilities = {
    expressionPresets: Object.keys(EXPRESSION_MORPHS),
    motions: [...MMD_MOTION_NAMES],
    mouthParameter: "あ",
    revision: "hanser-mmd-rig-v0.2",
  };

  private readonly baseRotations = new Map<Bone, Quaternion>();
  private readonly bones = new Map<string, Bone>();
  private readonly euler = new Euler();
  private readonly grantSolver: GrantSolver;
  private readonly owner = new ParameterOwner();
  private readonly rotationDelta = new Quaternion();
  private readonly mesh: SkinnedMesh;
  private activeMotion: MmdMotionName = "idle";
  private requestedMotion: MmdMotionName = "idle";
  private motionStartedAt = 0;
  private idlePhaseStartedAt: number | undefined;
  private idleActionIndex = 0;
  private nextIdleActionAt: number | undefined;
  private lastUpdatedAt = 0;
  private speechActivity = 0;
  private frame: Live2DFrame = {
    expressionPreset: "neutral",
    expressionWeight: 0,
    motion: "idle",
    mouthOpen: 0,
  };

  constructor(mesh: SkinnedMesh) {
    this.mesh = mesh;
    this.grantSolver = new MMDAnimationHelper().createGrantSolver(mesh);
    for (const bone of mesh.skeleton.bones) {
      this.bones.set(bone.name, bone);
      this.baseRotations.set(bone, bone.quaternion.clone());
    }
  }

  apply(frame: Live2DFrame, epoch: number) {
    if (!this.owner.claim(epoch)) {
      return;
    }
    this.frame = frame;
    if (
      MMD_MOTION_NAMES.includes(frame.motion as MmdMotionName) &&
      frame.motion !== this.requestedMotion
    ) {
      this.requestedMotion = frame.motion as MmdMotionName;
      this.play(this.requestedMotion, performance.now());
    }
  }

  destroy() {
    this.reset(Number.MAX_SAFE_INTEGER);
  }

  play(motion: MmdMotionName, now = performance.now()) {
    this.activeMotion = motion;
    this.motionStartedAt = now;
    this.nextIdleActionAt =
      motion === "idle"
        ? now + MMD_IDLE_ACTION_INTERVAL_MS[this.idleActionIndex]
        : undefined;
  }

  getActiveMotion() {
    return this.activeMotion;
  }

  reset(epoch: number) {
    if (!this.owner.claim(epoch)) {
      return;
    }
    for (const [bone, rotation] of this.baseRotations) {
      bone.quaternion.copy(rotation);
    }
    this.resetMorphs();
    this.activeMotion = "idle";
    this.motionStartedAt = performance.now();
    this.idlePhaseStartedAt = undefined;
    this.idleActionIndex = 0;
    this.nextIdleActionAt = undefined;
  }

  update(now = performance.now()) {
    const deltaMs = this.lastUpdatedAt
      ? Math.min(50, now - this.lastUpdatedAt)
      : 16;
    this.lastUpdatedAt = now;
    const speechTarget = Math.min(1, this.frame.mouthOpen * 1.25);
    const follow = speechTarget > this.speechActivity ? 0.24 : 0.12;
    this.speechActivity +=
      (speechTarget - this.speechActivity) * follow * (deltaMs / 16);
    this.idlePhaseStartedAt ??= now;
    this.nextIdleActionAt ??=
      now + MMD_IDLE_ACTION_INTERVAL_MS[this.idleActionIndex];
    if (
      this.activeMotion === "idle" &&
      this.requestedMotion === "idle" &&
      now >= this.nextIdleActionAt
    ) {
      this.activeMotion = MMD_IDLE_ACTION_SEQUENCE[this.idleActionIndex];
      this.idleActionIndex =
        (this.idleActionIndex + 1) % MMD_IDLE_ACTION_SEQUENCE.length;
      this.motionStartedAt = now;
      this.nextIdleActionAt = undefined;
    }
    const idleElapsedMs = now - this.idlePhaseStartedAt;
    const sampledMotion = this.activeMotion;
    const elapsedMs = now - this.motionStartedAt;
    const motionComplete = isMmdMotionComplete(sampledMotion, elapsedMs);
    const sampledElapsedMs = motionComplete
      ? MMD_MOTION_DURATION_MS[sampledMotion]
      : elapsedMs;
    const pose = sampleMmdPose(sampledMotion, sampledElapsedMs, idleElapsedMs);
    if (motionComplete) {
      this.activeMotion = "idle";
      this.motionStartedAt = now;
      this.nextIdleActionAt =
        now + MMD_IDLE_ACTION_INTERVAL_MS[this.idleActionIndex];
    }
    for (const [bone, base] of this.baseRotations) {
      bone.quaternion.copy(base);
    }
    for (const [name, rotation] of Object.entries(pose.bones)) {
      const bone = this.bones.get(name);
      const base = bone ? this.baseRotations.get(bone) : undefined;
      if (!(bone && base)) {
        continue;
      }
      const fixedAxis = FIXED_BONE_AXES.get(name);
      if (fixedAxis) {
        this.rotationDelta.setFromAxisAngle(fixedAxis, rotation[0]);
      } else {
        this.rotationDelta.setFromEuler(this.euler.set(...rotation, "XYZ"));
      }
      bone.quaternion.copy(base).multiply(this.rotationDelta);
    }
    this.grantSolver.update();

    // Small audio-driven posture changes make the rig feel connected to the
    // spoken cadence while keeping semantic gestures under explicit control.
    this.addBoneRotation("頭", [
      -0.01 * this.speechActivity +
        0.006 * (this.frame.mouthOpen - this.speechActivity),
      0,
      0,
    ]);
    this.addBoneRotation("上半身2", [0.004 * this.speechActivity, 0, 0]);

    this.resetMorphs();
    for (const [name, value] of Object.entries(pose.morphs)) {
      this.setMorph(name, value);
    }
    this.setMorph("あ", this.frame.mouthOpen * 0.82);
    const expressionMorph = EXPRESSION_MORPHS[this.frame.expressionPreset];
    if (expressionMorph) {
      this.setMorph(expressionMorph, this.frame.expressionWeight);
    }
  }

  private resetMorphs() {
    this.mesh.morphTargetInfluences?.fill(0);
  }

  private addBoneRotation(
    name: string,
    rotation: readonly [number, number, number]
  ) {
    const bone = this.bones.get(name);
    if (!bone) {
      return;
    }
    bone.quaternion.multiply(
      this.rotationDelta.setFromEuler(this.euler.set(...rotation, "XYZ"))
    );
  }

  private setMorph(name: string, value: number) {
    const index = this.mesh.morphTargetDictionary?.[name];
    if (index === undefined || !this.mesh.morphTargetInfluences) {
      return;
    }
    this.mesh.morphTargetInfluences[index] = Math.max(0, Math.min(1, value));
  }
}

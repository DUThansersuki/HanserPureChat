import { type Bone, Euler, Quaternion, type SkinnedMesh } from "three";
import {
  isMmdMotionComplete,
  MMD_MOTION_NAMES,
  type MmdMotionName,
  sampleMmdPose,
} from "./mmd-motion-library";
import { ParameterOwner, type RigAdapter } from "./rig-adapter";
import type { Live2DFrame } from "./timeline-evaluator";

const EXPRESSION_MORPHS: Record<string, string> = {
  curious: "困る",
  happy: "にこり",
  neutral: "",
  sad: "困る",
  surprised: "びっくり",
};

export class MmdRigAdapter implements RigAdapter {
  readonly capabilities = {
    expressionPresets: Object.keys(EXPRESSION_MORPHS),
    motions: [...MMD_MOTION_NAMES],
    mouthParameter: "あ",
    revision: "hanser-mmd-rig-v0.1",
  };

  private readonly baseRotations = new Map<Bone, Quaternion>();
  private readonly bones = new Map<string, Bone>();
  private readonly euler = new Euler();
  private readonly owner = new ParameterOwner();
  private readonly rotationDelta = new Quaternion();
  private readonly mesh: SkinnedMesh;
  private activeMotion: MmdMotionName = "idle";
  private requestedMotion: MmdMotionName = "idle";
  private motionStartedAt = 0;
  private frame: Live2DFrame = {
    expressionPreset: "neutral",
    expressionWeight: 0,
    motion: "idle",
    mouthOpen: 0,
  };

  constructor(mesh: SkinnedMesh) {
    this.mesh = mesh;
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
  }

  update(now = performance.now()) {
    let elapsedMs = now - this.motionStartedAt;
    if (isMmdMotionComplete(this.activeMotion, elapsedMs)) {
      this.activeMotion = "idle";
      this.motionStartedAt = now;
      elapsedMs = 0;
    }

    const pose = sampleMmdPose(this.activeMotion, elapsedMs);
    for (const [bone, base] of this.baseRotations) {
      bone.quaternion.copy(base);
    }
    for (const [name, rotation] of Object.entries(pose.bones)) {
      const bone = this.bones.get(name);
      const base = bone ? this.baseRotations.get(bone) : undefined;
      if (!(bone && base)) {
        continue;
      }
      bone.quaternion
        .copy(base)
        .multiply(
          this.rotationDelta.setFromEuler(this.euler.set(...rotation, "XYZ"))
        );
    }

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

  private setMorph(name: string, value: number) {
    const index = this.mesh.morphTargetDictionary?.[name];
    if (index === undefined || !this.mesh.morphTargetInfluences) {
      return;
    }
    this.mesh.morphTargetInfluences[index] = Math.max(0, Math.min(1, value));
  }
}

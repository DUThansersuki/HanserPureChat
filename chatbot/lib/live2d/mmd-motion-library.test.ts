import assert from "node:assert/strict";
import test from "node:test";
import {
  isMmdMotionComplete,
  MMD_IDLE_ACTION_SEQUENCE,
  MMD_MOTION_DURATION_MS,
  MMD_MOTION_NAMES,
  sampleMmdPose,
} from "./mmd-motion-library";

test("all acceptance motions produce finite bone rotations", () => {
  for (const motion of MMD_MOTION_NAMES) {
    const pose = sampleMmdPose(motion, motion === "idle" ? 1200 : 900);
    for (const rotation of Object.values(pose.bones)) {
      assert.equal(rotation.length, 3);
      assert.ok(rotation.every(Number.isFinite));
    }
    for (const value of Object.values(pose.morphs)) {
      assert.ok(Number.isFinite(value));
      assert.ok(value >= 0 && value <= 1);
    }
  }
});

test("greet starts and ends at the relaxed arm pose", () => {
  const idlePhase = 1700;
  const idle = sampleMmdPose("idle", 0, idlePhase);
  const start = sampleMmdPose("greet", 0, idlePhase);
  const end = sampleMmdPose("greet", MMD_MOTION_DURATION_MS.greet, idlePhase);

  assert.deepEqual(start.bones.右腕, idle.bones.右腕);
  assert.deepEqual(start.bones.右ひじ, idle.bones.右ひじ);
  assert.deepEqual(start.bones.右手首, idle.bones.右手首);
  assert.deepEqual(start.bones.右腕, end.bones.右腕);
  assert.deepEqual(start.bones.右ひじ, end.bones.右ひじ);
  assert.equal(end.morphs.にこり, idle.morphs.にこり);

  const raisedHand = sampleMmdPose("greet", 900);
  assert.ok(raisedHand.bones.右手捩[0] > 0.9);

  const waveLeft = sampleMmdPose("greet", 650);
  const waveRight = sampleMmdPose("greet", 1050);
  assert.ok(waveLeft.bones.右手首.every((value) => Math.abs(value) < 1e-9));
  assert.deepEqual(waveLeft.bones.右手首, waveRight.bones.右手首);
  assert.notEqual(waveLeft.bones.右腕[2], waveRight.bones.右腕[2]);
  assert.notEqual(waveLeft.bones.右ひじ[2], waveRight.bones.右ひじ[2]);
});

test("every action ends on the current continuous idle pose", () => {
  const idlePhase = 7137;
  const idle = sampleMmdPose("idle", 0, idlePhase);

  for (const motion of MMD_IDLE_ACTION_SEQUENCE) {
    const end = sampleMmdPose(
      motion,
      MMD_MOTION_DURATION_MS[motion],
      idlePhase
    );
    for (const [name, rotation] of Object.entries(idle.bones)) {
      assert.deepEqual(end.bones[name], rotation, `${motion}: ${name}`);
    }
    for (const [name, value] of Object.entries(idle.morphs)) {
      assert.equal(end.morphs[name], value, `${motion}: ${name}`);
    }
  }
});

test("peace pose raises a forward palm, forms a V, and winks", () => {
  const pose = sampleMmdPose("peace", 1200, 130);

  assert.ok(pose.bones.右手捩[0] > 1);
  assert.ok(pose.bones.右人指１[1] > 0);
  assert.ok(pose.bones.右中指１[1] < 0);
  assert.ok(pose.bones.右人指１[1] - pose.bones.右中指１[1] > 0.5);
  assert.ok(pose.bones.右親指０[1] < -0.8);
  assert.ok(pose.bones.右親指１[1] < -0.4);
  assert.ok(pose.bones.右薬指２[2] < -0.7);
  assert.ok(pose.bones.右小指２[2] < -0.7);
  assert.ok(pose.morphs.ウィンク右 > 0.9);
  assert.equal(pose.morphs.まばたき, 0);
});

test("natural blink resumes only after the wink has fully released", () => {
  const idlePhase = 130;
  const end = sampleMmdPose("peace", MMD_MOTION_DURATION_MS.peace, idlePhase);
  const idle = sampleMmdPose("idle", 0, idlePhase);

  assert.equal(end.morphs.ウィンク右, 0);
  assert.equal(end.morphs.まばたき, idle.morphs.まばたき);
});

test("idle stance keeps one hand on hip and the free arm relaxed", () => {
  const pose = sampleMmdPose("idle", 0);

  assert.notEqual(Math.abs(pose.bones.右腕[2]), Math.abs(pose.bones.左腕[2]));
  assert.ok(Math.abs(pose.bones.右ひじ[2]) >= 0.087);
  assert.ok(pose.bones.左ひじ[2] <= -1);
  assert.notDeepEqual(pose.bones.右手首, pose.bones.左手首);
  assert.ok(pose.morphs.にこり > 0.1);
  assert.ok(pose.morphs.にやり > 0);
});

test("singing pose closes both eyes and brings both hands to the chest", () => {
  const pose = sampleMmdPose("singing", 2600);

  assert.equal(pose.morphs.まばたき, 1);
  assert.ok(Math.abs(pose.bones.右肩[2]) < 0.1);
  assert.ok(Math.abs(pose.bones.左肩[2]) < 0.1);
  assert.ok(pose.bones.右腕[1] < -0.4);
  assert.ok(pose.bones.左腕[1] > 0.4);
  assert.ok(pose.bones.右腕[2] > 0.2);
  assert.ok(pose.bones.左腕[2] < -0.2);
  assert.ok(pose.bones.右ひじ[2] > 2.3);
  assert.ok(pose.bones.左ひじ[2] < -2.2);
  assert.ok(Math.abs(pose.bones.右手捩[0]) < 0.5);
  assert.ok(Math.abs(pose.bones.左手捩[0]) < 0.5);
  assert.ok(pose.bones.右手首[2] > 0.9);
  assert.ok(pose.bones.左手首[2] < -0.9);

  for (const side of ["右", "左"] as const) {
    for (const finger of ["人指", "中指", "薬指", "小指"] as const) {
      const mcp = Math.abs(pose.bones[`${side}${finger}１`][2]);
      const pip = Math.abs(pose.bones[`${side}${finger}２`][2]);
      const dip = Math.abs(pose.bones[`${side}${finger}３`][2]);
      assert.ok(pip > mcp, `${side}${finger}: PIP should bend more than MCP`);
      assert.ok(mcp > dip, `${side}${finger}: MCP should bend more than DIP`);
    }
    for (const segment of ["０", "１", "２"] as const) {
      assert.ok(pose.bones[`${side}親指${segment}`]);
    }
  }

  assert.ok(
    Math.abs(pose.bones.右小指１[2]) > Math.abs(pose.bones.右人指１[2])
  );
  assert.ok(
    Math.abs(pose.bones.左小指１[2]) > Math.abs(pose.bones.左人指１[2])
  );
  assert.ok(Math.abs(pose.bones.右親指０[2]) > 0.5);
  assert.ok(Math.abs(pose.bones.左親指０[2]) > 0.5);
});

test("finite actions complete while idle remains active", () => {
  assert.equal(isMmdMotionComplete("idle", 60_000), false);
  assert.equal(isMmdMotionComplete("singing", 315_000), false);
  assert.equal(
    isMmdMotionComplete("peace", MMD_MOTION_DURATION_MS.peace),
    true
  );
  assert.equal(
    isMmdMotionComplete("curious", MMD_MOTION_DURATION_MS.curious - 1),
    false
  );
});

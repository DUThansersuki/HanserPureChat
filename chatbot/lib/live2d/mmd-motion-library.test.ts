import assert from "node:assert/strict";
import test from "node:test";
import {
  isMmdMotionComplete,
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
  const start = sampleMmdPose("greet", 0);
  const end = sampleMmdPose("greet", MMD_MOTION_DURATION_MS.greet);

  assert.deepEqual(start.bones.右腕, end.bones.右腕);
  assert.deepEqual(start.bones.右ひじ, end.bones.右ひじ);
  assert.equal(end.morphs.にこり, 0);
});

test("finite actions complete while idle remains active", () => {
  assert.equal(isMmdMotionComplete("idle", 60_000), false);
  assert.equal(isMmdMotionComplete("nod", MMD_MOTION_DURATION_MS.nod), true);
  assert.equal(
    isMmdMotionComplete("curious", MMD_MOTION_DURATION_MS.curious - 1),
    false
  );
});

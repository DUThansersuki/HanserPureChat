import assert from "node:assert/strict";
import test from "node:test";
import {
  Bone,
  BufferGeometry,
  Euler,
  MeshBasicMaterial,
  Quaternion,
  Skeleton,
  SkinnedMesh,
  Vector3,
} from "three";
import {
  MMD_IDLE_ACTION_INTERVAL_MS,
  MMD_MOTION_DURATION_MS,
  sampleMmdPose,
} from "./mmd-motion-library";
import { MmdRigAdapter } from "./mmd-rig-adapter";

function createTwistRig() {
  const elbow = new Bone();
  elbow.name = "右ひじ";
  const twist = new Bone();
  twist.name = "右手捩";
  const quarterTwist = new Bone();
  quarterTwist.name = "右手捩1";
  const halfTwist = new Bone();
  halfTwist.name = "右手捩2";
  const threeQuarterTwist = new Bone();
  threeQuarterTwist.name = "右手捩3";
  const wrist = new Bone();
  wrist.name = "右手首";
  const upperBody = new Bone();
  upperBody.name = "上半身";
  const bones = [
    elbow,
    twist,
    quarterTwist,
    halfTwist,
    threeQuarterTwist,
    wrist,
    upperBody,
  ];
  const mesh = new SkinnedMesh(new BufferGeometry(), new MeshBasicMaterial());
  mesh.bind(new Skeleton(bones));
  mesh.geometry.userData.MMD = {
    grants: [
      {
        affectPosition: false,
        affectRotation: true,
        index: 2,
        isLocal: false,
        parentIndex: 1,
        ratio: 0.25,
      },
      {
        affectPosition: false,
        affectRotation: true,
        index: 3,
        isLocal: false,
        parentIndex: 1,
        ratio: 0.5,
      },
      {
        affectPosition: false,
        affectRotation: true,
        index: 4,
        isLocal: false,
        parentIndex: 1,
        ratio: 0.75,
      },
    ],
  };
  return { bones, mesh };
}

test("forearm twist is distributed through PMX grant bones", () => {
  const { bones, mesh } = createTwistRig();
  const adapter = new MmdRigAdapter(mesh);
  adapter.play("greet", 0);
  adapter.update(900);

  const source = bones[1].quaternion;
  const identity = new Quaternion();
  const fixedAxisRotation = new Quaternion().setFromAxisAngle(
    new Vector3(-0.827_429, -0.558_297, -0.060_552).normalize(),
    1.17
  );
  assert.ok(source.angleTo(fixedAxisRotation) < 1e-6);
  assert.ok(
    bones[2].quaternion.angleTo(identity.clone().slerp(source, 0.25)) < 1e-6
  );
  assert.ok(
    bones[3].quaternion.angleTo(identity.clone().slerp(source, 0.5)) < 1e-6
  );
  assert.ok(
    bones[4].quaternion.angleTo(identity.clone().slerp(source, 0.75)) < 1e-6
  );
});

test("completed actions keep the continuous idle phase", () => {
  const { bones, mesh } = createTwistRig();
  const adapter = new MmdRigAdapter(mesh);
  adapter.update(0);
  adapter.play("greet", 0);
  adapter.update(MMD_MOTION_DURATION_MS.greet);

  const idlePose = sampleMmdPose("idle", 0, MMD_MOTION_DURATION_MS.greet);
  const expected = new Quaternion().setFromEuler(
    new Euler(...idlePose.bones.上半身, "XYZ")
  );
  const upperBody = bones.find((bone) => bone.name === "上半身");
  assert.ok(upperBody);
  assert.ok(upperBody.quaternion.angleTo(expected) < 1e-6);
});

test("idle loop cycles through greet, curious, and peace", () => {
  const { mesh } = createTwistRig();
  const adapter = new MmdRigAdapter(mesh);
  const [greetDelay, curiousDelay, peaceDelay] = MMD_IDLE_ACTION_INTERVAL_MS;
  adapter.update(0);

  const greetAt = greetDelay;
  adapter.update(greetAt - 1);
  assert.equal(adapter.getActiveMotion(), "idle");
  adapter.update(greetAt);
  assert.equal(adapter.getActiveMotion(), "greet");

  const greetEnd = greetAt + MMD_MOTION_DURATION_MS.greet;
  adapter.update(greetEnd);
  assert.equal(adapter.getActiveMotion(), "idle");

  const curiousAt = greetEnd + curiousDelay;
  adapter.update(curiousAt);
  assert.equal(adapter.getActiveMotion(), "curious");

  const curiousEnd = curiousAt + MMD_MOTION_DURATION_MS.curious;
  adapter.update(curiousEnd);
  const peaceAt = curiousEnd + peaceDelay;
  adapter.update(peaceAt);
  assert.equal(adapter.getActiveMotion(), "peace");
});

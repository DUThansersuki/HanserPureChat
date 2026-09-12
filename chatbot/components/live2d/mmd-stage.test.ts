import assert from "node:assert/strict";
import test from "node:test";
import { live2dModelUrl } from "./mmd-stage";

test("MMD model requests preserve the configured Next.js base path", () => {
  assert.equal(
    live2dModelUrl("/demo", "hanser_ver2.0.pmx"),
    "/demo/api/live2d/model/hanser_ver2.0.pmx"
  );
  assert.equal(
    live2dModelUrl("", "hanser_ver2.0.pmx"),
    "/api/live2d/model/hanser_ver2.0.pmx"
  );
});

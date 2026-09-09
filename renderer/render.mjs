#!/usr/bin/env node
import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

function valueAfter(flag) {
  const index = process.argv.indexOf(flag);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

const manifestPath = valueAfter("--manifest");
const outputDirectory = resolve(valueAfter("--output") ?? "renderer/output");
const fps = Number(valueAfter("--fps") ?? "30");
const planOnly = process.argv.includes("--plan-only");
if (!manifestPath) {
  throw new Error("usage: node renderer/render.mjs --manifest <package.json> [--plan-only]");
}

const manifest = JSON.parse(await readFile(resolve(manifestPath), "utf8"));
if (manifest.schema_version !== "1.1" || manifest.status !== "completed") {
  throw new Error("only completed schema 1.1 packages can be rendered");
}
if (!Number.isInteger(manifest.total_duration_samples) || !manifest.timeline_sample_rate) {
  throw new Error("manifest is missing a sample-based duration");
}

const frameCount = Math.ceil(
  (manifest.total_duration_samples * fps) / manifest.timeline_sample_rate
);
const plan = {
  clock: "audio",
  fps,
  frame_count: frameCount,
  frame_sample(step) {
    return Math.min(
      manifest.total_duration_samples,
      Math.floor((step * manifest.timeline_sample_rate) / fps)
    );
  },
  manifest: resolve(manifestPath),
  schema_version: "1.1",
};
await mkdir(outputDirectory, { recursive: true });
await writeFile(
  resolve(outputDirectory, "render-plan.json"),
  JSON.stringify(
    {
      ...plan,
      frame_sample: undefined,
      first_sample: plan.frame_sample(0),
      last_sample: plan.frame_sample(Math.max(0, frameCount - 1)),
    },
    null,
    2
  )
);

if (planOnly) {
  process.stdout.write(`${JSON.stringify({ frameCount, outputDirectory })}\n`);
  process.exit(0);
}

const frames = resolve(outputDirectory, "frames", "%08d.png");
const audio = valueAfter("--audio");
if (!audio) {
  throw new Error("an accepted full audio artifact is required unless --plan-only is used");
}
const output = resolve(outputDirectory, "performance.mp4");
const child = spawn(
  "ffmpeg",
  [
    "-y",
    "-framerate",
    String(fps),
    "-i",
    frames,
    "-i",
    resolve(audio),
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-shortest",
    output,
  ],
  { stdio: "inherit" }
);
await new Promise((done, fail) => {
  child.on("error", fail);
  child.on("exit", (code) => (code === 0 ? done() : fail(new Error(`ffmpeg exited ${code}`))));
});

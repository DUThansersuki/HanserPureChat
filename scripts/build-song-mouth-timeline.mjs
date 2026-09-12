import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";

const [, , inputPath, outputPath] = process.argv;
if (!(inputPath && outputPath)) {
  throw new Error(
    "Usage: node build-song-mouth-timeline.mjs <input.flac> <output.json>"
  );
}

const sampleRate = 16_000;
const framesPerSecond = 20;
const samplesPerFrame = sampleRate / framesPerSecond;
const ffmpeg = spawn(
  "ffmpeg",
  [
    "-v",
    "error",
    "-i",
    inputPath,
    "-ac",
    "1",
    "-ar",
    String(sampleRate),
    "-af",
    "highpass=f=150,lowpass=f=4000",
    "-f",
    "f32le",
    "pipe:1",
  ],
  { stdio: ["ignore", "pipe", "inherit"] }
);

const chunks = [];
for await (const chunk of ffmpeg.stdout) {
  chunks.push(chunk);
}
const exitCode = await new Promise((resolve) => ffmpeg.once("close", resolve));
if (exitCode !== 0) {
  throw new Error(`ffmpeg exited with code ${exitCode}`);
}

const pcm = Buffer.concat(chunks);
const frameCount = Math.ceil(pcm.length / 4 / samplesPerFrame);
const rms = new Array(frameCount);
for (let frame = 0; frame < frameCount; frame += 1) {
  const firstSample = frame * samplesPerFrame;
  const lastSample = Math.min(firstSample + samplesPerFrame, pcm.length / 4);
  let sumSquares = 0;
  for (let sample = firstSample; sample < lastSample; sample += 1) {
    const value = pcm.readFloatLE(sample * 4);
    sumSquares += value * value;
  }
  rms[frame] = Math.sqrt(sumSquares / Math.max(1, lastSample - firstSample));
}

const sorted = [...rms].sort((a, b) => a - b);
const percentile = (ratio) => sorted[Math.floor((sorted.length - 1) * ratio)];
const floor = percentile(0.22);
const ceiling = percentile(0.96);
const span = Math.max(ceiling - floor, 1e-6);
let smoothed = 0;
const values = rms.map((value) => {
  const normalized = Math.max(0, Math.min(1, (value - floor) / span));
  const shaped = normalized < 0.055 ? 0 : normalized ** 0.72;
  const follow = shaped > smoothed ? 0.58 : 0.3;
  smoothed += (shaped - smoothed) * follow;
  return Number(smoothed.toFixed(3));
});

await writeFile(
  outputPath,
  `${JSON.stringify({
    durationSeconds: values.length / framesPerSecond,
    framesPerSecond,
    revision: "it-is-like-a-star-mouth-v1",
    values,
  })}\n`,
  "utf8"
);

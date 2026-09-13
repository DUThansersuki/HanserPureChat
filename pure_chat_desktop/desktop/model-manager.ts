import { createHash } from "node:crypto";
import {
  createReadStream,
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statfsSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import path from "node:path";
import extract from "extract-zip";
import type { DesktopPaths } from "./paths";

type ModelAsset = {
  archive: string;
  bytes: number;
  folder: string;
  name: string;
  sha256: string;
  url: string;
  version: string;
};

type ModelManifest = {
  models: ModelAsset[];
  schema_version: 1;
};

export type ModelProgress = {
  currentBytes: number;
  message: string;
  model: string;
  percent: number;
  totalBytes: number;
};

type ProgressCallback = (progress: ModelProgress) => void;

function readManifest(manifestPath: string): ModelManifest {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as ModelManifest;
  if (manifest.schema_version !== 1 || !Array.isArray(manifest.models)) {
    throw new Error("模型清单格式不受支持。");
  }
  for (const model of manifest.models) {
    if (
      !model.archive ||
      !model.folder ||
      !model.name ||
      !model.url ||
      !model.version ||
      !Number.isSafeInteger(model.bytes) ||
      model.bytes <= 0 ||
      path.basename(model.archive) !== model.archive ||
      path.basename(model.folder) !== model.folder ||
      !/^[a-f0-9]{64}$/i.test(model.sha256)
    ) {
      throw new Error(`模型清单条目无效：${model.name || "unknown"}`);
    }
  }
  return manifest;
}

async function sha256(filePath: string) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filePath)) {
    hash.update(chunk);
  }
  return hash.digest("hex");
}

function markerMatches(modelRoot: string, model: ModelAsset) {
  const markerPath = path.join(modelRoot, "hub", model.folder, ".hanser-model.json");
  if (!existsSync(markerPath)) {
    return false;
  }
  try {
    const marker = JSON.parse(readFileSync(markerPath, "utf8")) as {
      sha256?: string;
      version?: string;
    };
    return marker.sha256 === model.sha256 && marker.version === model.version;
  } catch {
    return false;
  }
}

function assertDiskSpace(paths: DesktopPaths, model: ModelAsset) {
  const stats = statfsSync(paths.userRoot);
  const availableBytes = Number(stats.bavail) * Number(stats.bsize);
  const requiredBytes = model.bytes * 2 + 512 * 1024 * 1024;
  if (availableBytes < requiredBytes) {
    throw new Error(
      `磁盘空间不足，安装 ${model.name} 至少需要 ${Math.ceil(requiredBytes / 1024 ** 3)} GiB 可用空间。`
    );
  }
}

async function download(
  model: ModelAsset,
  destination: string,
  onProgress: ProgressCallback
) {
  let existingBytes = existsSync(destination) ? statSync(destination).size : 0;
  if (existingBytes > model.bytes) {
    rmSync(destination, { force: true });
    existingBytes = 0;
  }
  if (existingBytes === model.bytes) {
    return;
  }
  onProgress({
    currentBytes: existingBytes,
    message: `正在下载 ${model.name}…`,
    model: model.name,
    percent: Math.floor((existingBytes / model.bytes) * 100),
    totalBytes: model.bytes,
  });
  const headers = existingBytes > 0 ? { Range: `bytes=${existingBytes}-` } : undefined;
  let response = await fetch(model.url, { headers, redirect: "follow" });
  if (existingBytes > 0 && response.status === 200) {
    rmSync(destination, { force: true });
    existingBytes = 0;
    response = await fetch(model.url, { redirect: "follow" });
  }
  if (!(response.ok || response.status === 206) || !response.body) {
    throw new Error(`模型下载失败：${model.name}（HTTP ${response.status}）`);
  }
  const output = createWriteStream(destination, { flags: existingBytes > 0 ? "a" : "w" });
  let currentBytes = existingBytes;
  let lastReportedPercent = -1;
  const readable = Readable.fromWeb(response.body as never);
  readable.on("data", (chunk: Buffer) => {
    currentBytes += chunk.length;
    const percent = Math.min(100, Math.floor((currentBytes / model.bytes) * 100));
    if (percent !== lastReportedPercent) {
      lastReportedPercent = percent;
      onProgress({
        currentBytes,
        message: `正在下载 ${model.name}…`,
        model: model.name,
        percent,
        totalBytes: model.bytes,
      });
    }
  });
  await pipeline(readable, output);
  if (statSync(destination).size !== model.bytes) {
    throw new Error(`模型文件大小不匹配：${model.name}`);
  }
}

async function installModel(
  paths: DesktopPaths,
  model: ModelAsset,
  onProgress: ProgressCallback
) {
  assertDiskSpace(paths, model);
  const archivePath = path.join(paths.modelDownloadRoot, `${model.archive}.part`);
  await download(model, archivePath, onProgress);
  onProgress({
    currentBytes: model.bytes,
    message: `正在校验 ${model.name}…`,
    model: model.name,
    percent: 100,
    totalBytes: model.bytes,
  });
  if ((await sha256(archivePath)) !== model.sha256) {
    rmSync(archivePath, { force: true });
    throw new Error(`模型完整性校验失败：${model.name}`);
  }

  const hubRoot = path.join(paths.userModelRoot, "hub");
  const temporaryRoot = path.join(paths.userModelRoot, `.extracting-${model.folder}`);
  const extractedModel = path.join(temporaryRoot, model.folder);
  const finalModel = path.join(hubRoot, model.folder);
  rmSync(temporaryRoot, { force: true, recursive: true });
  mkdirSync(temporaryRoot, { recursive: true });
  onProgress({
    currentBytes: model.bytes,
    message: `正在安装 ${model.name}…`,
    model: model.name,
    percent: 100,
    totalBytes: model.bytes,
  });
  await extract(archivePath, { dir: temporaryRoot });
  if (!existsSync(extractedModel)) {
    throw new Error(`模型包目录结构无效：${model.name}`);
  }
  mkdirSync(hubRoot, { recursive: true });
  rmSync(finalModel, { force: true, recursive: true });
  renameSync(extractedModel, finalModel);
  writeFileSync(
    path.join(finalModel, ".hanser-model.json"),
    `${JSON.stringify({ sha256: model.sha256, version: model.version }, null, 2)}\n`,
    "utf8"
  );
  rmSync(temporaryRoot, { force: true, recursive: true });
  rmSync(archivePath, { force: true });
}

export async function ensureModels(paths: DesktopPaths, onProgress: ProgressCallback) {
  if (existsSync(path.join(paths.resourcesRoot, "models", "hub"))) {
    return;
  }
  const manifest = readManifest(paths.modelManifest);
  mkdirSync(paths.modelDownloadRoot, { recursive: true });
  for (const model of manifest.models) {
    if (!markerMatches(paths.userModelRoot, model)) {
      await installModel(paths, model, onProgress);
    }
  }
}

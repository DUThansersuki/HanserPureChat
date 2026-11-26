import { app } from "electron";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";

export type DesktopPaths = {
  projectRoot: string;
  resourcesRoot: string;
  frontendRoot: string;
  backendExecutable: string;
  backendEntrypoint: string;
  voicePython: string;
  voiceWorkingRoot: string;
  voiceProfile: string;
  rigProfile: string;
  configTemplate: string;
  seedDatabase: string;
  userdict: string;
  userRoot: string;
  dataRoot: string;
  database: string;
  configRoot: string;
  settings: string;
  secrets: string;
  cacheRoot: string;
  logsRoot: string;
  runtimeRoot: string;
  runtimeConfig: string;
  voiceDataRoot: string;
};

export function configureUserDataRoot() {
  const explicitRoot = process.env.HANSER_DESKTOP_DATA_ROOT;
  const localAppData = process.env.LOCALAPPDATA;
  if (explicitRoot) {
    app.setPath("userData", path.resolve(explicitRoot));
  } else if (localAppData) {
    app.setPath(
      "userData",
      path.join(localAppData, app.isPackaged ? "HanserChatVoice" : "HanserChatVoiceDev")
    );
  }
}

export function resolveDesktopPaths(): DesktopPaths {
  const projectRoot = path.resolve(__dirname, "..", "..");
  const resourcesRoot = app.isPackaged
    ? process.resourcesPath
    : path.join(projectRoot, "resources");
  const userRoot = app.getPath("userData");
  const dataRoot = path.join(userRoot, "data");
  const configRoot = path.join(userRoot, "config");
  const runtimeRoot = path.join(userRoot, "runtime");
  return {
    projectRoot,
    resourcesRoot,
    frontendRoot: app.isPackaged
      ? path.join(resourcesRoot, "frontend")
      : path.join(projectRoot, "release", "runtime", "frontend"),
    backendExecutable: app.isPackaged
      ? path.join(resourcesRoot, "backend", "hanser_backend", "hanser_backend.exe")
      : path.join(
          projectRoot,
          "release",
          "runtime",
          "backend",
          "hanser_backend",
          "hanser_backend.exe"
        ),
    backendEntrypoint: path.join(projectRoot, "backend", "run_desktop.py"),
    voicePython: app.isPackaged
      ? path.join(resourcesRoot, "voice", "python", "python.exe")
      : path.join(projectRoot, "voice_runtime", ".venv", "Scripts", "python.exe"),
    voiceWorkingRoot: app.isPackaged ? resourcesRoot : projectRoot,
    voiceProfile: process.env.HANSER_VOICE_PROFILE
      ? path.resolve(process.env.HANSER_VOICE_PROFILE)
      : path.join(app.isPackaged ? resourcesRoot : projectRoot, "voice_runtime", "config", "voice_profile.local.yml"),
    rigProfile: app.isPackaged
      ? path.join(resourcesRoot, "voice_runtime", "config", "rig_profile.disabled.yml")
      : path.join(projectRoot, "voice_runtime", "config", "rig_profile.disabled.yml"),
    configTemplate: app.isPackaged
      ? path.join(resourcesRoot, "defaults", "config.desktop.example.yml")
      : path.join(projectRoot, "backend", "config.desktop.example.yml"),
    seedDatabase: path.join(resourcesRoot, "database", "documents.seed.db"),
    userdict: path.join(resourcesRoot, "userdict.txt"),
    userRoot,
    dataRoot,
    database: path.join(dataRoot, "documents.db"),
    configRoot,
    settings: path.join(configRoot, "settings.json"),
    secrets: path.join(configRoot, "secrets.bin"),
    cacheRoot: path.join(userRoot, "cache"),
    logsRoot: path.join(userRoot, "logs"),
    runtimeRoot,
    runtimeConfig: path.join(runtimeRoot, "config.desktop.yml"),
    voiceDataRoot: path.join(userRoot, "voice-data"),
  };
}

export function prepareWritablePaths(paths: DesktopPaths) {
  for (const directory of [
    paths.userRoot,
    paths.dataRoot,
    paths.configRoot,
    paths.cacheRoot,
    paths.logsRoot,
    paths.runtimeRoot,
    paths.voiceDataRoot,
  ]) {
    mkdirSync(directory, { recursive: true });
  }
  if (!existsSync(paths.database)) {
    if (!existsSync(paths.seedDatabase)) {
      throw new Error(`数据库种子不存在：${paths.seedDatabase}`);
    }
    copyFileSync(paths.seedDatabase, paths.database);
  }
}

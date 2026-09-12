import { app } from "electron";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";

export type DesktopPaths = {
  projectRoot: string;
  resourcesRoot: string;
  frontendRoot: string;
  backendExecutable: string;
  backendEntrypoint: string;
  configTemplate: string;
  modelRoot: string;
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
};

export function configureUserDataRoot() {
  const explicitRoot = process.env.HANSER_DESKTOP_DATA_ROOT;
  const localAppData = process.env.LOCALAPPDATA;
  if (explicitRoot) {
    app.setPath("userData", path.resolve(explicitRoot));
  } else if (localAppData) {
    app.setPath(
      "userData",
      path.join(localAppData, app.isPackaged ? "HanserPureChat" : "HanserPureChatDev")
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
    configTemplate: app.isPackaged
      ? path.join(resourcesRoot, "defaults", "config.desktop.example.yml")
      : path.join(projectRoot, "backend", "config.desktop.example.yml"),
    modelRoot: path.join(resourcesRoot, "models"),
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

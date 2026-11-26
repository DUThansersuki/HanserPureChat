import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  safeStorage,
  session,
  shell,
} from "electron";
import path from "node:path";
import { appendFileSync } from "node:fs";
import { SidecarManager } from "./process-manager";
import {
  configureUserDataRoot,
  prepareWritablePaths,
  resolveDesktopPaths,
  type DesktopPaths,
} from "./paths";
import {
  SettingsStore,
  isLocalOllama,
  type ModelSettings,
} from "./settings-store";

configureUserDataRoot();
app.disableHardwareAcceleration();

const hasLock = app.requestSingleInstanceLock();
if (!hasLock) {
  app.quit();
}

let mainWindow: BrowserWindow | null = null;
let setupWindow: BrowserWindow | null = null;
let settingsStore: SettingsStore;
let desktopPaths: DesktopPaths;
let sidecars: SidecarManager;
let activeRendererOrigin = "";
let resolveSetup: ((value: ModelSettings | null) => void) | null = null;
let stopping = false;
let restarting = false;

function windowOptions() {
  return {
    backgroundColor: "#fff7ed",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
      sandbox: true,
    },
  } as const;
}

function attachNavigationPolicy(
  window: BrowserWindow,
  allowedOrigin: () => string
) {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://") || url.startsWith("http://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    const nextOrigin = new URL(url).origin;
    if (nextOrigin !== allowedOrigin()) {
      event.preventDefault();
      if (url.startsWith("https://") || url.startsWith("http://")) {
        void shell.openExternal(url);
      }
    }
  });
}

function ensureMainWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    return mainWindow;
  }
  mainWindow = new BrowserWindow({
    ...windowOptions(),
    height: 780,
    minHeight: 600,
    minWidth: 780,
    show: false,
    title: "Hanser Chat Voice",
    width: 1120,
  });
  attachNavigationPolicy(mainWindow, () => activeRendererOrigin);
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  return mainWindow;
}

async function showStatus(message: string, canRetry: boolean) {
  activeRendererOrigin = "null";
  const window = ensureMainWindow();
  await window.loadFile(path.join(__dirname, "status.html"), {
    query: { canRetry: canRetry ? "1" : "0", message },
  });
}

async function promptForSettings(): Promise<ModelSettings | null> {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.focus();
    return null;
  }
  return await new Promise((resolve) => {
    resolveSetup = resolve;
    setupWindow = new BrowserWindow({
      ...windowOptions(),
      height: 570,
      parent: mainWindow ?? undefined,
      resizable: false,
      title: "Hanser Chat Voice 设置",
      width: 560,
    });
    attachNavigationPolicy(setupWindow, () => "null");
    void setupWindow.loadFile(path.join(__dirname, "setup.html"));
    setupWindow.on("closed", () => {
      setupWindow = null;
      if (resolveSetup) {
        resolveSetup(null);
        resolveSetup = null;
      }
    });
  });
}

async function bootRuntime() {
  if (restarting) {
    return;
  }
  restarting = true;
  try {
    await showStatus("正在启动聊天服务……", false);
    let settings = settingsStore.load();
    let apiKey = isLocalOllama(settings.baseUrl) ? "" : settingsStore.readApiKey();
    if (!isLocalOllama(settings.baseUrl) && !apiKey) {
      const configured = await promptForSettings();
      if (!configured) {
        app.quit();
        return;
      }
      settings = configured;
      apiKey = isLocalOllama(settings.baseUrl) ? "" : settingsStore.readApiKey();
    }
    await showStatus("正在启动聊天服务，首次使用 Ollama 会自动下载对话模型……", false);
    const services = await sidecars.start(settings, apiKey);
    activeRendererOrigin = new URL(services.frontendUrl).origin;
    appendFileSync(path.join(desktopPaths.logsRoot, "desktop.log"), "loading chat window\n");
    await ensureMainWindow().loadURL(services.frontendUrl);
    appendFileSync(path.join(desktopPaths.logsRoot, "desktop.log"), "chat window loaded\n");
  } catch (error) {
    const message = error instanceof Error ? error.message : "桌面服务启动失败。";
    await showStatus(message, true);
  } finally {
    restarting = false;
  }
}

function registerIpc() {
  ipcMain.handle("settings:get", () => settingsStore.setupState());
  ipcMain.handle(
    "settings:save",
    (_, value: ModelSettings & { apiKey: string }) => {
      try {
        const settings = settingsStore.save(value, value.apiKey);
        resolveSetup?.(settings);
        resolveSetup = null;
        setupWindow?.close();
        return { ok: true };
      } catch (error) {
        return {
          ok: false,
          message: error instanceof Error ? error.message : "设置保存失败。",
        };
      }
    }
  );
  ipcMain.handle("desktop:open-logs", () => shell.openPath(desktopPaths.logsRoot));
  ipcMain.handle("desktop:open-settings", async () => {
    const changed = await promptForSettings();
    if (changed) {
      await bootRuntime();
    }
  });
  ipcMain.handle("desktop:reload", () => mainWindow?.webContents.reload());
  ipcMain.handle("desktop:toggle-fullscreen", () => {
    if (mainWindow) {
      mainWindow.setFullScreen(!mainWindow.isFullScreen());
    }
  });
  ipcMain.handle("desktop:retry", () => bootRuntime());
  ipcMain.handle("desktop:voice-start", () => sidecars.startVoice());
  ipcMain.handle("desktop:voice-state", () => sidecars.voiceProcessState());
  ipcMain.handle("desktop:quit", () => app.quit());
  ipcMain.handle("desktop:version", () => app.getVersion());
}

if (hasLock) {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    }
  });

  void app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error("Windows 安全存储不可用，无法启动应用。");
    }
    desktopPaths = resolveDesktopPaths();
    prepareWritablePaths(desktopPaths);
    settingsStore = new SettingsStore(desktopPaths);
    sidecars = new SidecarManager(desktopPaths, (message) => {
      void showStatus(message, true);
    });
    registerIpc();
    session.defaultSession.setPermissionRequestHandler((_, __, callback) => callback(false));
    await bootRuntime();
  });
}

app.on("window-all-closed", () => app.quit());

app.on("before-quit", (event) => {
  if (stopping || !sidecars) {
    return;
  }
  event.preventDefault();
  stopping = true;
  void sidecars.stop().finally(() => app.quit());
});

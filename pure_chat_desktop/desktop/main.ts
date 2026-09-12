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
import { SidecarManager } from "./process-manager";
import {
  configureUserDataRoot,
  prepareWritablePaths,
  resolveDesktopPaths,
  type DesktopPaths,
} from "./paths";
import {
  SettingsStore,
  type ModelSettings,
} from "./settings-store";

configureUserDataRoot();

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
    title: "Hanser Pure Chat",
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
      title: "Hanser Pure Chat 设置",
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
    await showStatus("正在启动本地聊天服务，首次加载检索模型可能需要一些时间……", false);
    let settings = settingsStore.load();
    let apiKey = settingsStore.readApiKey();
    if (!apiKey) {
      const configured = await promptForSettings();
      if (!configured) {
        app.quit();
        return;
      }
      settings = configured;
      apiKey = settingsStore.readApiKey();
    }
    const services = await sidecars.start(settings, apiKey);
    activeRendererOrigin = new URL(services.frontendUrl).origin;
    await ensureMainWindow().loadURL(services.frontendUrl);
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
  ipcMain.handle("desktop:retry", () => bootRuntime());
  ipcMain.handle("desktop:quit", () => app.quit());
  ipcMain.handle("desktop:version", () => app.getVersion());
}

function installMenu() {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "应用",
        submenu: [
          {
            label: "模型设置…",
            click: async () => {
              const changed = await promptForSettings();
              if (changed) {
                await bootRuntime();
              }
            },
          },
          { type: "separator" },
          { role: "quit", label: "退出" },
        ],
      },
      {
        label: "查看",
        submenu: [
          { role: "reload", label: "重新加载" },
          { role: "togglefullscreen", label: "切换全屏" },
        ],
      },
    ])
  );
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
    installMenu();
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

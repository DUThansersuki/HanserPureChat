import { contextBridge, ipcRenderer } from "electron";
import type { ModelSettings, SetupState } from "./settings-store";

const desktopApi = {
  getSetupState: (): Promise<SetupState> => ipcRenderer.invoke("settings:get"),
  saveSettings: (settings: ModelSettings & { apiKey: string }) =>
    ipcRenderer.invoke("settings:save", settings) as Promise<
      { ok: true } | { ok: false; message: string }
    >,
  openLogs: () => ipcRenderer.invoke("desktop:open-logs"),
  openSettings: () => ipcRenderer.invoke("desktop:open-settings"),
  reload: () => ipcRenderer.invoke("desktop:reload"),
  toggleFullscreen: () => ipcRenderer.invoke("desktop:toggle-fullscreen"),
  retryStartup: () => ipcRenderer.invoke("desktop:retry"),
  quit: () => ipcRenderer.invoke("desktop:quit"),
  version: () => ipcRenderer.invoke("desktop:version") as Promise<string>,
};

contextBridge.exposeInMainWorld("hanserDesktop", desktopApi);

export type HanserDesktopBridge = typeof desktopApi;

export {};

declare global {
  interface Window {
    hanserDesktop?: {
      openSettings: () => Promise<void>;
      quit: () => Promise<void>;
      reload: () => Promise<void>;
      toggleFullscreen: () => Promise<void>;
    };
  }
}


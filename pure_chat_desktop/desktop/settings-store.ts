import { safeStorage } from "electron";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import type { DesktopPaths } from "./paths";

export type ModelSettings = {
  baseUrl: string;
  model: string;
};

export type SetupState = ModelSettings & {
  hasApiKey: boolean;
};

const defaultSettings: ModelSettings = {
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-flash",
};

export class SettingsStore {
  constructor(private readonly paths: DesktopPaths) {}

  load(): ModelSettings {
    if (!existsSync(this.paths.settings)) {
      return defaultSettings;
    }
    const value = JSON.parse(readFileSync(this.paths.settings, "utf8")) as Partial<ModelSettings>;
    return {
      baseUrl: value.baseUrl || defaultSettings.baseUrl,
      model: value.model || defaultSettings.model,
    };
  }

  setupState(): SetupState {
    return { ...this.load(), hasApiKey: existsSync(this.paths.secrets) };
  }

  readApiKey(): string {
    const environmentKey = process.env.HANSER_MODEL_API_KEY;
    if (environmentKey) {
      return environmentKey;
    }
    if (!existsSync(this.paths.secrets)) {
      return "";
    }
    if (!safeStorage.isEncryptionAvailable()) {
      throw new Error("Windows 安全存储当前不可用，无法读取模型 API Key。");
    }
    return safeStorage.decryptString(readFileSync(this.paths.secrets));
  }

  save(settings: ModelSettings, apiKey: string): ModelSettings {
    const baseUrl = settings.baseUrl.trim().replace(/\/+$/, "");
    const model = settings.model.trim();
    const parsedUrl = new URL(baseUrl);
    if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
      throw new Error("Base URL 必须使用 http 或 https。");
    }
    if (!model) {
      throw new Error("模型名不能为空。");
    }
    if (apiKey.trim()) {
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error("Windows 安全存储当前不可用，不能保存 API Key。");
      }
      writeFileSync(this.paths.secrets, safeStorage.encryptString(apiKey.trim()));
    } else if (!existsSync(this.paths.secrets)) {
      throw new Error("首次配置必须填写 API Key。");
    }
    const normalized = { baseUrl, model };
    writeFileSync(this.paths.settings, `${JSON.stringify(normalized, null, 2)}\n`, "utf8");
    return normalized;
  }
}

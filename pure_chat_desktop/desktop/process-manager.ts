import type { ChildProcess } from "node:child_process";
import { spawn } from "node:child_process";
import { createWriteStream, existsSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { parse, stringify } from "yaml";
import type { DesktopPaths } from "./paths";
import type { ModelSettings } from "./settings-store";

export type RunningServices = {
  frontendUrl: string;
  backendPort: number;
  frontendPort: number;
};

type ManagedChild = {
  name: "backend" | "frontend";
  process: ChildProcess;
};

async function freeLoopbackPort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("无法分配本地端口。"));
        return;
      }
      const port = address.port;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

const delay = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

export class SidecarManager {
  private children: ManagedChild[] = [];
  private generation = 0;

  constructor(
    private readonly paths: DesktopPaths,
    private readonly onUnexpectedExit: (message: string) => void
  ) {}

  async start(settings: ModelSettings, apiKey: string): Promise<RunningServices> {
    await this.stop();
    const generation = ++this.generation;
    const backendPort = await freeLoopbackPort();
    const frontendPort = await freeLoopbackPort();
    const desktopToken = randomBytes(32).toString("base64url");
    this.writeRuntimeConfig(settings);

    const backend = this.spawnBackend(backendPort, desktopToken, apiKey);
    this.track("backend", backend, generation);
    await this.waitForHealth(
      `http://127.0.0.1:${backendPort}/health`,
      desktopToken,
      backend,
      180_000
    );

    const frontend = this.spawnFrontend(frontendPort, backendPort, desktopToken);
    this.track("frontend", frontend, generation);
    await this.waitForHealth(
      `http://127.0.0.1:${frontendPort}/api/health`,
      "",
      frontend,
      45_000
    );

    return {
      frontendUrl: `http://127.0.0.1:${frontendPort}`,
      backendPort,
      frontendPort,
    };
  }

  async stop() {
    this.generation += 1;
    const children = this.children.splice(0);
    await Promise.all(
      children.map(async ({ process: child }) => {
        if (child.exitCode !== null || child.killed) {
          return;
        }
        child.kill();
        await Promise.race([
          new Promise<void>((resolve) => child.once("exit", () => resolve())),
          delay(5_000),
        ]);
        if (child.exitCode === null) {
          child.kill("SIGKILL");
        }
      })
    );
  }

  private writeRuntimeConfig(settings: ModelSettings) {
    const template = parse(readFileSync(this.paths.configTemplate, "utf8")) as Record<
      string,
      any
    >;
    template.data.db_path = this.paths.database;
    template.data.data_dir = this.paths.dataRoot;
    template.data.userdict_path = this.paths.userdict;
    template.style.output_dir = path.join(this.paths.dataRoot, "persona");
    template.llm.base_url = settings.baseUrl;
    template.llm.api_key = "";
    template.llm.model = settings.model;
    for (const task of ["bunny", "prometheus", "hanser"]) {
      template.llm[task].model = settings.model;
    }
    const hasBundledModels = existsSync(path.join(this.paths.modelRoot, "hub"));
    template.models.embedding.local_files_only = hasBundledModels;
    template.models.reranker.local_files_only = hasBundledModels;
    writeFileSync(this.paths.runtimeConfig, stringify(template), "utf8");
  }

  private spawnBackend(port: number, desktopToken: string, apiKey: string) {
    const commonEnvironment = {
      ...process.env,
      HANSER_BACKEND_PORT: String(port),
      HANSER_CONFIG: this.paths.runtimeConfig,
      HANSER_DESKTOP: "1",
      HANSER_DESKTOP_TOKEN: desktopToken,
      HF_HOME: this.paths.modelRoot,
      OPENAI_API_KEY: apiKey,
      PYTHONIOENCODING: "utf-8",
    };
    if (existsSync(this.paths.backendExecutable)) {
      return this.spawnLogged(
        "backend",
        this.paths.backendExecutable,
        [],
        path.dirname(this.paths.backendExecutable),
        commonEnvironment
      );
    }
    const pythonExecutable = process.env.HANSER_PYTHON_EXE;
    if (!pythonExecutable) {
      throw new Error(
        "后端运行包尚未构建。开发模式请先运行 pnpm build:backend，或设置 HANSER_PYTHON_EXE。"
      );
    }
    return this.spawnLogged(
      "backend",
      pythonExecutable,
      [this.paths.backendEntrypoint],
      path.dirname(this.paths.backendEntrypoint),
      commonEnvironment
    );
  }

  private spawnFrontend(port: number, backendPort: number, desktopToken: string) {
    const serverEntrypoint = path.join(this.paths.frontendRoot, "server.js");
    if (!existsSync(serverEntrypoint)) {
      throw new Error("前端 standalone 产物不存在，请先运行 pnpm build:frontend。");
    }
    return this.spawnLogged(
      "frontend",
      process.execPath,
      [serverEntrypoint],
      this.paths.frontendRoot,
      {
        ...process.env,
        ELECTRON_RUN_AS_NODE: "1",
        HANSER_API_BASE_URL: `http://127.0.0.1:${backendPort}`,
        HANSER_DESKTOP_TOKEN: desktopToken,
        HANSER_USER_ID: "local-user",
        HOSTNAME: "127.0.0.1",
        NODE_ENV: "production",
        PORT: String(port),
      }
    );
  }

  private spawnLogged(
    name: "backend" | "frontend",
    command: string,
    args: string[],
    cwd: string,
    environment: NodeJS.ProcessEnv
  ) {
    const log = createWriteStream(path.join(this.paths.logsRoot, `${name}.log`), {
      flags: "a",
    });
    log.write(`\n[${new Date().toISOString()}] starting ${name}\n`);
    const child = spawn(command, args, {
      cwd,
      env: environment,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    child.stdout?.pipe(log, { end: false });
    child.stderr?.pipe(log, { end: false });
    child.once("close", (code, signal) => {
      log.end(
        `[${new Date().toISOString()}] ${name} stopped code=${code ?? "none"} signal=${signal ?? "none"}\n`
      );
    });
    return child;
  }

  private track(name: "backend" | "frontend", child: ChildProcess, generation: number) {
    this.children.push({ name, process: child });
    child.once("exit", (code) => {
      if (generation !== this.generation) {
        return;
      }
      this.onUnexpectedExit(`${name} 进程意外退出（code=${code ?? "none"}）。`);
    });
  }

  private async waitForHealth(
    url: string,
    desktopToken: string,
    child: ChildProcess,
    timeoutMilliseconds: number
  ) {
    const deadline = Date.now() + timeoutMilliseconds;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) {
        throw new Error(`服务启动前退出（code=${child.exitCode}），请查看日志。`);
      }
      const response = await fetch(url, {
        headers: desktopToken
          ? { "X-Hanser-Desktop-Token": desktopToken }
          : undefined,
        signal: AbortSignal.timeout(2_000),
      }).catch(() => null);
      if (response?.ok) {
        const payload = (await response.json().catch(() => null)) as {
          ok?: boolean;
          chat_ready?: boolean;
        } | null;
        if (payload?.ok !== false && payload?.chat_ready !== false) {
          return;
        }
      }
      await delay(300);
    }
    throw new Error(`服务健康检查超时：${new URL(url).pathname}`);
  }
}

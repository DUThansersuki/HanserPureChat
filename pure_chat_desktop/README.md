# Hanser Pure Chat Desktop

这是从 HanserAgent 完整版冻结出的独立纯文字聊天桌面副本。完整版源码、启动器、Voice、Live2D、MMD、离线演出和 Artifact 功能不由本目录修改。

## 当前能力

- Windows 10/11 x64 Electron 桌面窗口，不依赖浏览器。
- 纯文本新会话、回复、历史恢复、复制、停止与重试。
- Persona v2 production、Wiki/Style 检索、长期记忆和 PostTurn 幂等链路。
- 首次运行配置 OpenAI-compatible Base URL、模型名和 API Key。
- API Key 通过 Electron `safeStorage` 使用 Windows DPAPI 加密保存。
- Python、Node、Next.js 和 Qwen Embedding/Reranker 均由安装目录携带。
- 回答模型仍通过网络调用；本应用不是离线大语言模型。

详细边界与设计决策见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 目录

```text
desktop/     Electron 主进程、preload、设置页和错误页
frontend/    精简后的 Next.js Chat UI 与 4 个业务 API
backend/     纯 Chat FastAPI 入口及 Hanser 业务副本
resources/   数据库种子、分词词典和发布时模型资源
scripts/     种子导出、前后端构建、验证与安装包构建
release/     生成的中间运行目录（不提交 Git）
dist/        NSIS 安装包与 unpacked 候选（不提交 Git）
```

## 开发验证

在 `pure_chat_desktop` 目录执行：

```powershell
pnpm install
pnpm check
pnpm build:desktop
pnpm build:frontend
pnpm build:backend
.\scripts\smoke_packaged_backend.ps1
pnpm smoke:app
```

后端聚焦测试：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_desktop_api.py -q
```

`smoke_packaged_backend.ps1` 只检查打包后端能否载入冻结模型、种子数据库和 Persona，并达到 `chat_ready=true`；它不会调用回答模型。
`smoke:app` 会启动 `win-unpacked` 成品，使用一次性独立数据目录确认数据库复制、前后端启动及桌面进程退出；占位 API Key 不会被用于模型请求。

## 数据库种子

发布种子保留文档、分块、向量、活动索引代际和已审核 Style，清空会话、消息、记忆、关系状态、场景状态、请求执行和 PostTurn 数据。导出不会原地修改源库：

```powershell
.\backend\.venv\Scripts\python.exe .\scripts\export_seed.py `
  H:\HanserAgent\source_data\documents.db `
  .\resources\database\documents.seed.db
.\backend\.venv\Scripts\python.exe .\scripts\verify_seed.py `
  .\resources\database\documents.seed.db
```

数据库文件和模型属于发布输入，不提交 Git。首次启动时，Electron 将种子复制到 `%LOCALAPPDATA%\HanserPureChat\data\documents.db`，后续升级不覆盖用户数据。

## 构建安装包

本机 Hugging Face cache 中需存在：

- `Qwen/Qwen3-Embedding-0.6B`
- `Qwen/Qwen3-Reranker-0.6B`

完整构建：

```powershell
pnpm build:installer -- -SourceDatabase H:\HanserAgent\source_data\documents.db
```

若种子、模型和后端依赖已准备好，可使用脚本的跳过参数缩短重复构建。输出位于 `dist/`：

```text
HanserPureChatSetup-0.1.0-x64.exe
win-unpacked/Hanser Pure Chat.exe
latest-build-manifest.json
```

`pnpm verify:package` 检查成品必需资源和纯 Chat 路由边界，并在发布阶段生成安装包大小、SHA-256、Git 提交与运行时摘要。

安装采用 per-user NSIS，不要求管理员权限。模型资源约 2.25 GiB，后端运行时约 0.57 GiB，因此安装包和安装后目录都较大；候选发布必须记录实测体积与启动时间。

## 运行目录与日志

```text
%LOCALAPPDATA%\HanserPureChat\
├─ data\documents.db
├─ config\settings.json
├─ config\secrets.bin
├─ cache\
├─ logs\backend.log
├─ logs\frontend.log
└─ runtime\config.desktop.yml
```

桌面菜单“应用 → 模型设置…”可更新 endpoint、模型和密钥；“打开日志目录”只在启动失败页提供。日志不写 API Key 或内部令牌。

## Git 检查点

- `eb21977`：架构基线。
- `17453c7`：完整版本源副本冻结清单。
- `e4c9851`：纯 Chat 后端和前端。
- `17e5c18`：Electron 壳与构建链初版。

开发分支为 `codex/pure-chat-desktop`。完整版分支及其工作目录不需要切换或清理；本次实施使用独立 Git worktree 隔离后续构建。

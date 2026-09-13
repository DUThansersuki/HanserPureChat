# Hanser Pure Chat

Hanser Pure Chat 是从 HanserAgent 完整版冻结出的 Windows 纯文字聊天桌面应用。它保留 Persona、知识与 Style 检索、长期记忆、会话历史和请求幂等链路，移除了 Live2D、语音、MMD、离线演出、Artifact、附件与登录系统。

应用自带 Electron、Next.js、Python 后端和数据库种子；用户无需安装 Node.js、Python，也无需手动启动服务。回答模型仍通过用户配置的 OpenAI-compatible API 调用，因此它不是完全离线的大语言模型应用。

## 功能

- Windows 10/11 x64 原生桌面窗口。
- 新建纯文字会话、连续对话、停止、重试和复制回复。
- 会话历史保存与重启恢复。
- Persona v2 production、Wiki/Style 混合检索和长期记忆。
- 首次运行配置 Base URL、模型名和 API Key。
- API Key 使用 Electron `safeStorage` 和 Windows DPAPI 加密保存。
- 本地 Qwen Embedding/Reranker 首次运行自动下载，支持进度显示、断点续传、SHA-256 校验和缓存复用。

完整架构、裁剪边界和验收标准见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 下载与安装

从 GitHub Releases 下载：

```text
HanserPureChatLiteSetup-0.1.0-x64.exe
```

双击安装即可。安装采用 per-user NSIS，默认不要求管理员权限。当前测试版尚未配置正式代码签名，Windows SmartScreen 可能显示“未知发布者”。

首次启动流程：

1. 填写 OpenAI-compatible Base URL、回答模型名和 API Key。
2. 应用下载 Qwen3 Embedding 0.6B 与 Qwen3 Reranker 0.6B，共约 2.25 GiB。
3. 下载完成后自动校验并安装模型，然后启动本地聊天服务。
4. 后续启动直接复用本地模型，不会重复下载；回答模型调用仍需联网。

建议首次运行前准备至少 4 GiB 可用磁盘空间。下载中断时保留 `.part` 文件，下次启动从已完成部分继续。

## 发布部署

一个可用的 GitHub Release 必须在同一个 tag 下提供以下五个文件：

```text
HanserPureChatLiteSetup-0.1.0-x64.exe
Qwen3-Embedding-0.6B.zip
Qwen3-Reranker-0.6B.zip
model-manifest.json
latest-build-manifest.json
```

当前模型清单固定指向 tag `pure-chat-v0.1.0-beta.1`。如果修改 tag、仓库名、模型内容或文件名，必须重新执行模型包构建，不能手工沿用旧清单。

发布者部署步骤：

```powershell
pnpm install
.\scripts\prepare_models.ps1
pnpm build:models
pnpm build:installer -- -SourceDatabase H:\HanserAgent\source_data\documents.db
pnpm verify:package
```

然后创建与模型清单一致的 GitHub Release tag，上传上述五个文件。发布前核对：

- `latest-build-manifest.json` 中 `git_dirty` 为 `false`。
- 安装包 SHA-256 与清单一致。
- 两个模型 ZIP 的大小和 SHA-256 与 `model-manifest.json` 一致。
- Release 标记为 pre-release，直到干净 Windows 环境验收完成。

Lite 安装包不携带模型；模型 ZIP 是应用首次启动时自动获取的发布资源，不需要用户手动下载或解压。原 Full 安装包仅作为本地离线备选，不是当前 GitHub 主发布物。

## 从源码构建

环境要求：

- Windows 10/11 x64
- Node.js 与 pnpm
- Python 3.13 和项目后端虚拟环境
- 本机 Hugging Face cache 中已有：
  - `Qwen/Qwen3-Embedding-0.6B`
  - `Qwen/Qwen3-Reranker-0.6B`

常用命令：

```powershell
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

`smoke_packaged_backend.ps1` 验证冻结后端能够载入模型、数据库种子和 Persona，并达到 `chat_ready=true`，但不会调用回答模型。`smoke:app` 启动 `win-unpacked` 成品，验证数据库复制、前后端启动和桌面进程退出。

## 项目结构

```text
desktop/     Electron 主进程、模型下载器、preload、设置页和状态页
frontend/    精简后的 Next.js Chat UI 与业务 API
backend/     纯 Chat FastAPI 入口及 Hanser 业务副本
resources/   数据库种子、分词词典、模型清单与模型包构建输入
scripts/     种子导出、模型打包、前后端构建、验证与安装器脚本
release/     构建中间目录，不提交 Git
dist/        NSIS、模型发布包和 unpacked 候选，不提交 Git
```

## 本地数据

应用数据默认位于：

```text
%LOCALAPPDATA%\HanserPureChat\
├─ config\settings.json
├─ config\secrets.bin
├─ data\documents.db
├─ downloads\models\
├─ models\hub\
├─ logs\backend.log
├─ logs\frontend.log
└─ runtime\config.desktop.yml
```

新版安装程序不会覆盖用户数据库。启动失败时可从错误页打开日志目录。

## 模型与许可证

检索模型来自 Qwen 官方仓库：

- [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) — Apache-2.0
- [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) — Apache-2.0

## 当前限制

- 仅支持 Windows x64。
- 不包含本地回答大模型；聊天回复依赖用户配置的在线 API。
- 当前未配置自动更新、正式应用图标和代码签名证书。
- 首次运行必须联网下载本地检索模型。

本副本在独立 worktree 和 `codex/pure-chat-desktop` 分支维护，不修改 HanserAgent 完整版的运行目录与数据。

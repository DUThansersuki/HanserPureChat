# Hanser Pure Chat

Hanser Pure Chat 是一个面向 Windows 的纯文字聊天桌面应用，由 HanserAgent 完整版独立拆分而来。

它保留了 Persona、知识检索、Style 检索、长期记忆和会话历史，并移除了 Live2D、语音、MMD、离线演出、Artifact、附件及登录系统。

## 功能特点

- Windows 10/11 x64 桌面应用
- 新建会话、连续对话、停止与重试
- Markdown、代码块和中文排版
- 会话历史保存与重启恢复
- Persona v2 production
- Wiki 与 Style 混合检索
- 长期记忆和 PostTurn 处理
- 本地 Qwen Embedding 与 Reranker
- OpenAI-compatible API
- API Key 使用 Windows DPAPI 加密保存
- 模型首次启动自动下载
- 支持下载进度、断点续传和 SHA-256 校验

不包含：

- Live2D、MMD
- 语音合成与音频播放
- Artifact 和文档编辑器
- 文件上传和附件
- 登录及多用户系统

## 下载

前往 [GitHub Releases](https://github.com/DUThansersuki/HanserPureChat/releases) 下载最新版：

```text
HanserPureChatLiteSetup-0.1.0-x64.exe
```

当前版本为测试版，尚未配置正式代码签名。Windows SmartScreen 可能显示“未知发布者”。

## 安装与首次启动

1. 下载并运行 Lite 安装程序。
2. 选择安装位置并完成安装。
3. 启动 Hanser Pure Chat。
4. 填写 OpenAI-compatible API 配置：
   - Base URL
   - 模型名称
   - API Key
5. 应用自动下载本地检索模型。
6. 模型校验完成后自动启动聊天服务。

首次运行需要下载：

- Qwen3 Embedding 0.6B
- Qwen3 Reranker 0.6B

两个模型合计约 2.25 GiB，建议预留至少 4 GiB 可用磁盘空间。

下载中断后会保留 `.part` 文件，下次启动自动续传。模型安装完成后会保存在本机，后续启动不会重复下载。

> 本地模型只负责知识检索和重排序。聊天回复仍由用户配置的在线回答模型生成。

## Lite 版说明

Lite 版只从安装包中移除了两个大型检索模型，没有删除聊天功能。

| 项目 | Lite 版 |
| --- | --- |
| Electron 桌面程序 | 包含 |
| Next.js 聊天界面 | 包含 |
| Python 后端 | 包含 |
| 数据库种子 | 包含 |
| Persona 与长期记忆 | 包含 |
| Embedding/Reranker | 首次启动下载 |
| 回答模型 | 使用用户配置的在线 API |

Lite 安装包约 379 MB。模型下载完成后，其功能与内置模型版本相同。

## 数据位置

用户数据默认保存在：

```text
%LOCALAPPDATA%\HanserPureChat\
├─ config\
│  ├─ settings.json
│  └─ secrets.bin
├─ data\
│  └─ documents.db
├─ downloads\
│  └─ models\
├─ models\
│  └─ hub\
├─ logs\
│  ├─ backend.log
│  └─ frontend.log
└─ runtime\
   └─ config.desktop.yml
```

升级应用不会覆盖已有聊天历史和用户数据库。

如果应用启动失败，可以从错误页面打开日志目录。

## 发布文件

一个完整的 GitHub Release 包含：

```text
HanserPureChatLiteSetup-0.1.0-x64.exe
Qwen3-Embedding-0.6B.zip
Qwen3-Reranker-0.6B.zip
model-manifest.json
latest-build-manifest.json
```

普通用户只需要手动下载 Lite 安装程序。

两个模型 ZIP 是应用首次启动时使用的自动下载源，不需要手动下载、改名或解压。

## 从源码构建

### 环境要求

- Windows 10/11 x64
- Node.js
- pnpm
- Python 3.13
- 已建立项目后端虚拟环境
- Hugging Face cache 中已有：
  - `Qwen/Qwen3-Embedding-0.6B`
  - `Qwen/Qwen3-Reranker-0.6B`

### 安装依赖

```powershell
pnpm install
```

### 基础检查

```powershell
pnpm check
```

### 分别构建

```powershell
pnpm build:desktop
pnpm build:frontend
pnpm build:backend
```

### 构建模型发布包

```powershell
.\scripts\prepare_models.ps1
pnpm build:models
```

### 构建 Lite 安装程序

```powershell
pnpm build:installer -- `
  -SourceDatabase H:\HanserAgent\source_data\documents.db
```

输出文件位于 `dist/`。

## GitHub Release 部署

1. 准备数据库种子和两个模型。
2. 生成模型 ZIP 与 `model-manifest.json`。
3. 构建 Lite 安装程序。
4. 执行发布验证。
5. 创建与模型清单一致的 Release tag。
6. 上传五个发布文件。
7. 先以 Pre-release 形式发布并完成干净系统验收。

相关命令：

```powershell
.\scripts\prepare_models.ps1
pnpm build:models
pnpm build:installer -- `
  -SourceDatabase H:\HanserAgent\source_data\documents.db
pnpm verify:package
```

发布前确认：

- `latest-build-manifest.json` 中 `git_dirty` 为 `false`
- 安装包 SHA-256 与清单一致
- 两个模型包的大小和 SHA-256 与模型清单一致
- Release tag 与模型下载 URL 中的 tag 一致
- Lite 安装包内没有误带模型目录

## 项目结构

```text
desktop/     Electron 主进程、模型下载器、设置页和状态页
frontend/    Next.js 纯聊天界面及业务 API
backend/     FastAPI 纯聊天后端
resources/   数据库种子、分词词典和模型清单
scripts/     构建、模型打包、验证和安装器脚本
release/     构建中间目录
dist/        安装程序与模型发布包
```

## 开发验证

后端聚焦测试：

```powershell
.\backend\.venv\Scripts\python.exe `
  -m pytest backend\tests\test_desktop_api.py -q
```

验证冻结后的后端：

```powershell
.\scripts\smoke_packaged_backend.ps1
```

验证桌面成品：

```powershell
pnpm smoke:app
```

桌面冒烟测试会验证：

- 用户数据库创建
- 模型载入
- Python 后端启动
- Next.js 前端启动
- Electron 生命周期
- 应用退出后无残留子进程

## 模型来源

本项目使用以下本地检索模型：

- [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen/Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)

两个模型的官方页面均标注为 Apache-2.0。

## 当前限制

- 仅支持 Windows x64
- 首次运行需要联网下载检索模型
- 聊天回复依赖用户配置的在线 API
- 尚未配置自动更新
- 尚未配置正式应用图标
- 尚未配置代码签名证书
- 当前版本仍为 Pre-release

## 项目隔离

Hanser Pure Chat 使用独立源码分支、构建目录和用户数据目录。

它不会修改 HanserAgent 完整版的：

- 源码
- 启动器
- Live2D 与语音模块
- 运行配置
- 聊天数据库
- 用户数据

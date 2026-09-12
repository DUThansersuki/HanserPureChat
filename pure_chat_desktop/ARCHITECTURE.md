# Hanser Pure Chat Desktop 架构与实施方案

文档状态：架构基线（待实施）  
目标平台：Windows 10/11 x64  
目标形态：可安装的独立桌面应用  
当前阶段：只新增本文，不复制或修改现有运行代码，不构建、不调用模型  

## 1. 结论

Hanser Pure Chat Desktop 采用独立副本，不在现有 `backend/`、`chatbot/`、`voice_runtime/`、`renderer/`、`assets/` 和启动脚本上继续裁剪。

推荐技术组合：

- Electron：提供 Windows 主窗口、单实例、进程编排、日志入口和安装包。
- Next.js standalone：保留现有 Chat UI、AI SDK 消息协议以及服务端到 Hanser Backend 的代理层。
- Python backend sidecar：从现有后端复制，只保留文字聊天所需能力，并由桌面主进程启动和关闭。
- electron-builder + NSIS：生成安装程序 `HanserPureChatSetup-<version>-x64.exe`。
- PyInstaller `onedir`：冻结 Python sidecar。首版不采用 `onefile`，避免 PyTorch/Transformers 每次启动解压、启动变慢和排错困难。

最终用户会得到一个正常的 Windows 应用入口 `Hanser Pure Chat.exe`。安装目录内部允许存在 Next.js、Python、模型和数据库种子等资源；“做成 EXE”不等于把所有大型运行资源强塞进一个 PE 文件。

本方案仍需联网调用当前配置的回答模型。它不依赖浏览器、不依赖用户手动启动 Python/Node，也不需要开启原项目启动器，但不是离线大模型版本。

## 2. 目标与范围

### 2.1 必须保留

- 新建文字会话。
- 发送纯文本消息。
- 等待、处理中和失败状态。
- 显示完整文字回复。
- Markdown、中文排版、代码块、数学公式等现有文字渲染能力。
- 会话历史列表和按会话恢复。
- 回复复制。
- 当前 Persona v2 production 组合。
- Wiki/文档混合检索、Style 检索、Planner、统一 Responder。
- 长期记忆、会话摘要、关系/场景状态以及 PostTurn 处理。
- `request_id` 幂等、reply snapshot 和 PostTurn 失败状态。
- `adult_innuendo_opt_in` 显式设置及其冻结语义。
- 健康状态、可理解的启动失败页面和本地日志。

### 2.2 明确移除

- Live2D、MMD、Three.js 舞台、口型、动作和表情。
- Voice runtime、TTS、音频播放、音频任务和语音缓存。
- 离线视频渲染和表现时间线。
- Artifact、文档/代码/表格编辑器。
- 附件和文件上传。
- Weather 等前端示例工具和工具审批 UI。
- 投票、公开分享、公开/私有可见性切换。
- 编辑已发送消息、删除会话和批量删除。
- Auth.js、注册、登录、Postgres、Redis、Blob 和 Vercel AI Gateway。
- 多用户和局域网/公网监听。
- 自动更新器；首版通过新版安装包升级。

“完整聊天功能”在本项目中定义为当前已经接入 Hanser Backend 的纯文字聊天闭环，不把上游 Vercel Chatbot 尚未接入 Hanser 语义的功能算入范围。尤其不能只在 UI 删除历史而保留 Memory 来源，否则会破坏来源追溯。

### 2.3 首版不做

- Token 级逐字流式输出。保持当前后端返回完整 JSON、前端显示等待状态的行为。
- 完全离线回答模型。
- macOS、Linux、ARM64 和移动端包。
- 后台常驻、托盘菜单、开机启动。
- 云同步和跨设备会话。
- Persona、Style 或检索质量调参。

## 3. 原项目不变原则

### 3.1 物理隔离

后续实现全部位于新目录：

```text
H:\HanserAgent\
├─ backend\                    # 原项目，禁止修改
├─ chatbot\                    # 原项目，禁止修改
├─ voice_runtime\              # 原项目，禁止修改
├─ renderer\                   # 原项目，禁止修改
├─ scripts\                    # 原项目，禁止修改
└─ pure_chat_desktop\          # 新桌面副本，唯一实施范围
```

不从副本通过相对路径导入原项目 Python/TypeScript 模块。开发时可以重新执行显式同步脚本，但同步是“源文件复制”，不是运行时共享。这样原项目之后增加 Voice/L2D 时不会改变纯聊天应用，纯聊天应用的裁剪也不会反向影响原项目。

### 3.2 复制基线

正式复制前生成一次 `source-baseline.json`，记录：

- 原项目 Git commit。
- 工作树是否包含未提交改动。
- 明确纳入副本的文件清单。
- 明确排除的目录和生成物。
- Persona package ID、Style generation、schema/detector 版本。
- 复制时间和复制工具版本。

如果复制时原工作树有未提交改动，默认复制磁盘上的当前内容并逐文件记录，不执行 `git clean`、`git reset`、`git checkout` 或任何覆盖原文件的操作。哈希只在这次副本冻结边界和后续候选发布边界计算，不在普通编辑后反复计算。

### 3.3 数据隔离

副本绝不直接打开原项目的 `source_data/documents.db` 进行运行时写入。安装程序携带只读数据库种子，首次启动复制到应用自己的用户数据目录。原项目会话、记忆和副本会话、记忆默认互不影响。

如以后需要迁移已有历史，单独实现一次性、可预览、先备份的导入工具；不把“直接共用数据库”当作迁移方案。

## 4. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Hanser Pure Chat.exe / Electron Main                         │
│                                                              │
│  单实例锁 ─ 路径解析 ─ 密钥解密 ─ 子进程生命周期 ─ 日志入口 │
│       │                                      │               │
│       │ 启动/停止                            │ 创建窗口      │
│       ▼                                      ▼               │
│  Python Chat Backend                    Electron Renderer    │
│  127.0.0.1:<随机端口>                   加载本地 Next URL    │
│       ▲                                      │               │
│       │ X-Hanser-Desktop-Token               ▼               │
│       └────────────────────────────── Next standalone        │
│                                      127.0.0.1:<随机端口>     │
└──────────────────────────────────────────────────────────────┘
                         │
                         │ HTTPS
                         ▼
                    当前 LLM Provider
```

### 4.1 进程职责

| 进程 | 职责 | 禁止承担 |
| --- | --- | --- |
| Electron Main | 选择端口、准备用户目录、读取加密设置、启动/停止 sidecar、窗口安全策略、外链交给系统浏览器 | Persona、聊天历史或业务判断 |
| Next standalone | 提供静态 UI/SSR、把 AI SDK 请求转换为 Hanser `/v1/chat` 请求、历史/健康代理 | 第二套 Persona、第二套历史库 |
| Python Chat Backend | 会话、Persona、检索、记忆、幂等、回复快照、模型调用的唯一真源 | Voice、L2D、离线渲染 |

保留 Next.js 服务层是有意选择。当前聊天 UI 已经依赖 Next API routes 把 Hanser JSON 转为 AI SDK UI Message Stream；直接改成静态 SPA 会扩大改动面，同时失去服务端代理边界。首版优先保留验证过的链路。

### 4.2 启动顺序

1. Electron 获取 single-instance lock；第二个实例只激活已有窗口。
2. 解析安装资源目录和 `%LOCALAPPDATA%\HanserPureChat`。
3. 首次启动时把数据库种子原子复制到用户数据目录。
4. 读取应用设置，用 Windows DPAPI/Electron `safeStorage` 解密模型 API Key。
5. 生成仅本次运行有效的随机内部令牌，选择两个空闲 loopback 端口。
6. 启动 Python Chat Backend，传入配置路径、数据库路径、模型缓存路径、端口和内部令牌。
7. 轮询后端 `/health`；要求 `ok=true` 且 `chat_ready=true`。
8. 启动 Next standalone，传入后端 URL、固定本地用户 ID、内部令牌和前端端口。
9. 轮询 Next `/ping` 后创建 BrowserWindow。
10. 任一步失败都进入本地错误页，显示安全错误摘要和“打开日志目录/重试/退出”。

### 4.3 关闭顺序

1. 禁止新提交。
2. 请求 Next server 正常退出。
3. 请求 Python backend 正常退出并等待 SQLite 提交完成。
4. 超过明确超时后只终止本应用创建的精确 PID 进程树。
5. Electron 退出。

不得按端口号或进程名批量杀死 Python/Node，也不得影响原项目启动器创建的进程。

## 5. 建议目录结构

```text
pure_chat_desktop/
├─ ARCHITECTURE.md
├─ README.md
├─ package.json
├─ pnpm-lock.yaml
├─ desktop/
│  ├─ main.ts
│  ├─ process-manager.ts
│  ├─ paths.ts
│  ├─ settings-store.ts
│  ├─ preload.ts
│  └─ error-page/
├─ frontend/
│  ├─ app/
│  ├─ components/
│  ├─ hooks/
│  ├─ lib/
│  ├─ public/
│  ├─ next.config.ts
│  └─ package.json
├─ backend/
│  ├─ hanser_agent/
│  ├─ run_desktop.py
│  ├─ config.desktop.example.yml
│  ├─ requirements-runtime.txt
│  └─ hanser_backend.spec
├─ resources/
│  ├─ database/documents.seed.db
│  ├─ persona/releases/<immutable-package-id>/
│  ├─ persona/provenance/
│  ├─ userdict.txt
│  └─ models/
├─ scripts/
│  ├─ copy_baseline.ps1
│  ├─ build_backend.ps1
│  ├─ build_frontend.ps1
│  ├─ build_installer.ps1
│  └─ verify_package.ps1
├─ tests/
│  ├─ backend/
│  ├─ frontend/
│  └─ packaging/
└─ release/
   └─ manifests/
```

`resources/` 中的大文件和生成包是否提交 Git 在实施阶段决定；无论是否提交，候选发布清单必须记录其来源和版本。

## 6. 副本裁剪策略

### 6.1 后端保留

- `ChatAgentService` 及其 request state。
- `DialoguePlanner`。
- `ContextBuilder`。
- Wiki、Style、Memory tools。
- Persona compiler、signals、policy 和 presentation。
- Hanser responder、validator、model gateway。
- Conversation、Memory、summary、relationship/scene state。
- SQLite schema、迁移和索引读取。
- `/health`、`/v1/chat`、`/v1/conversations`、`/v1/conversations/{id}`。
- PostTurn 状态所需的内部存储能力。

### 6.2 后端移除

- `RenderBridge` 的构建和生命周期。
- `/v1/voice/**`。
- `/v1/performance/visual-plans`。
- Voice/performance models 和 contracts。
- Voice runtime HTTP client。
- 离线渲染相关配置。
- 用于离线评估、候选生成和发布的 scripts；这些仍保留在原工程作为研发工具，不进入桌面运行包。

`run_desktop.py` 直接构造纯文字设置；不先加载全功能服务再依靠 UI 隐藏。下面四项在桌面配置中固定为 `false`：

```yaml
performance:
  structured_performance_enabled: false
  speech_runtime_enabled: false
  dynamic_live2d_enabled: false
  offline_export_enabled: false
```

### 6.3 前端保留

- App Router 的聊天页和历史路由。
- ChatShell、Messages、纯文字 Message、输入框、侧栏和主题。
- AI SDK `useChat` 与现有 Hanser 消息适配。
- `/api/chat`、`/api/history`、`/api/messages`、`/api/health`、`/ping`。
- SWR 历史刷新、等待状态、错误提示和 PostTurn pending 提示。
- Markdown/CJK/code/math renderer。
- `adult_innuendo_opt_in` 设置。

### 6.4 前端移除

- `components/live2d/**`、`lib/live2d/**`。
- `components/voice/**`、`lib/voice/**`、所有 `/api/voice/**`。
- Artifact 和 editor 组件、相关 hooks、handlers 和 API routes。
- 文件上传、附件预览和 `@vercel/blob`。
- Weather、工具调用、工具审批和未被 Hanser Backend 使用的 provider/prompts。
- Auth 页面、NextAuth SessionProvider、数据库 queries/migrations。
- model selector、visibility selector、vote、share、delete/edit UI。
- Three.js、CodeMirror、ProseMirror、react-data-grid 等纯聊天不需要的依赖。

不能只使用 `NEXT_PUBLIC_HANSER_CHAT_ONLY=1` 隐藏组件。副本应删除静态 import 和依赖，使它们不进入构建图与安装包。

## 7. 接口契约

### 7.1 Renderer 到 Next

保持同源 HTTP：

- `POST /api/chat`
- `GET /api/history`
- `GET /api/messages?chatId=<id>`
- `GET /api/health`
- `GET /ping`

Renderer 不知道 Python backend 的端口、模型 API Key 或内部令牌。

### 7.2 Next 到 Python

保留现有字段映射：

| UI | Backend |
| --- | --- |
| chat id | `conversation_id` |
| 固定桌面用户 | `user_id=local-user` |
| user message id | `request_id` |
| 文字输入 | `message` |
| 成人轻度双关设置 | `persona_settings.adult_innuendo_opt_in` |
| 历史侧栏 | `GET /v1/conversations` |
| 会话恢复 | `GET /v1/conversations/{conversation_id}` |

每次请求固定发送：

```json
{
  "output_preferences": {
    "text": true,
    "speech": false,
    "dynamic_live2d": false,
    "offline_performance": false
  }
}
```

Next 到 Python 的所有业务请求增加运行期请求头：

```text
X-Hanser-Desktop-Token: <ephemeral random token>
```

令牌只作为本机进程边界，不能替代公网身份认证。Backend 始终只监听 `127.0.0.1`。

### 7.3 回复与失败

- Backend 是正文持久化状态的唯一真源。
- 已生成并持久化的 reply snapshot 不因 UI 断开而重新生成。
- `post_turn_status=pending_retry` 时仍显示正文，同时明确提示记忆整理未完成。
- 同一 `request_id` 重试必须返回同一结果；影响输出的 Persona 设置和 output preferences 继续纳入冻结请求身份。
- 首版不伪装为 Token 流式；Next 仅把完整 Hanser JSON 转成一次 AI SDK 文本事件。
- 桌面进程意外退出后的恢复以已持久化历史为准。若要实现进行中请求恢复，应先增加明确的 request-status 契约，不能靠前端猜测。

## 8. 数据与目录布局

### 8.1 安装目录：只读资源

```text
%ProgramFiles%\Hanser Pure Chat\
├─ Hanser Pure Chat.exe
└─ resources\
   ├─ app.asar
   ├─ frontend\
   ├─ backend\
   ├─ database\documents.seed.db
   ├─ persona\
   ├─ userdict.txt
   └─ models\
```

首版默认使用 per-user NSIS 安装时，实际安装根可位于 `%LOCALAPPDATA%\Programs\Hanser Pure Chat`；代码不得依赖固定盘符或当前工作目录。

### 8.2 用户可写目录

```text
%LOCALAPPDATA%\HanserPureChat\
├─ data\documents.db
├─ config\settings.json
├─ config\secrets.bin
├─ cache\
├─ logs\main.log
├─ logs\frontend.log
├─ logs\backend.log
├─ runtime\
└─ backups\
```

- SQLite、日志、缓存和设置只能写用户目录。
- API Key 使用 `safeStorage`/Windows DPAPI 加密后保存；不写进源码、安装包、日志或 release manifest。
- 日志默认不记录完整用户消息、完整模型回复或 API Key。
- 日志轮转按大小和数量控制，不吞掉明确启动异常。

### 8.3 数据库种子与首次启动

当前 SQLite 同时包含知识/索引数据和运行时会话/记忆表。发布种子应通过显式导出流程生成：

- 保留 `documents`、tokens、chunks、向量、index generations 和已审核 Style 数据。
- 保留 schema/migration 状态。
- 默认清空 conversations、messages、summaries、memories、relationship/scene state、request executions、reply snapshots、PostTurn 表和 permission events。
- 导出前后校验活动 Style generation 与 Persona release 兼容。
- 不从原数据库原地删除记录；生成新的 seed 文件。

若用户明确要求把现有聊天历史带入副本，再通过单独导入步骤复制运行时表和来源关系。

### 8.4 SQLite 运行策略

- 单实例应用对应单个写进程。
- 启用现有事务边界，不新增外部数据库。
- 应用升级前只在 schema/data migration 边界创建一次备份。
- 升级失败保留旧数据库与失败日志，不用空库覆盖。
- 回滚应用版本时不自动回滚聊天、Memory 或用户之后保存的明确偏好。

## 9. Persona、Style 与来源追溯

桌面副本不借机修改 Persona 行为或 Style 数据。首版固定当前 Text 生产组合：

- Selector：`persona-v2-production`
- 不可变 package ID：`hanser-persona-v2-production-20260910`
- Style generation：`persona-v2-style-profanity15-1c71903569618d5d`
- `style.reviewed_only=true`

发布资源使用不可变版本目录，不让 production selector 指向可被候选工具覆盖的目录。release manifest 至少记录：

- package ID、schema version、文件清单和发布边界哈希。
- Style generation、模型与维度。
- detector 版本。
- 数据库 seed 版本。
- 与上一版的兼容关系和回滚目标。
- 来源 ledger/manifest 的相对位置。

Style 不能充当事实来源；显式关闭和人物边界继续是硬约束。副本裁剪不能取消人物事实/来源、明确拒绝、格式保护、请求冻结、PostTurn 幂等和回滚兼容门禁。

## 10. 模型与运行资源

### 10.1 回答模型

继续通过 HTTPS 调用当前 OpenAI-compatible provider。应用第一次启动进入设置向导，要求配置：

- Base URL。
- API Key。
- 模型名。
- 可选连通性测试。

不把开发机上的 `backend/config.yml` 连同密钥原样放进安装包。

### 10.2 Embedding 与 Reranker

为保持当前检索质量，正式安装候选应冻结当前 Qwen Embedding 和 Reranker 的本地模型资源，并设置 `local_files_only=true`。模型的实际体积、许可证、文件清单和冷启动资源消耗在候选冻结阶段记录。

如果发布许可或安装体积不满足要求，必须形成单独决策：

- Full：随包携带本地 Embedding/Reranker，保持当前检索路径。
- Download-on-first-run：首次启动下载并验证版本，安装包较小但首次使用依赖网络。
- Lite：显式切换到经评估的 BM25-only 配置；不得静默降级并宣称与 Full 等价。

首版架构默认选择 Full；Lite 只能在聚焦检索回归通过后作为另一个明确产品变体。

### 10.3 资源预算门禁

候选包阶段实测并记录：

- 安装包和安装后占用。
- 冷启动/热启动时间。
- 首次回复前模型 warmup 时间。
- 空闲内存、检索峰值内存、CPU 和 GPU 行为。
- 典型消息端到端延迟。

架构阶段不猜测或填写通过值，保持 `NOT_MEASURED`。

## 11. Electron 安全与桌面行为

- `nodeIntegration=false`。
- `contextIsolation=true`。
- Preload 只暴露“打开日志目录、读取应用版本、重试启动”等最小 IPC。
- Renderer 不得执行任意 shell 命令或读取任意文件。
- 拒绝非本地导航；外部链接经 allowlist 后交给系统浏览器。
- Next 和 Python 都只监听 loopback。
- 随机运行期端口，不复用原项目的 3000/8765/8766/8770。
- Next → Backend 使用随机内部令牌。
- BrowserWindow 只在两个健康检查通过后加载聊天页。
- CSP 禁止不必要的远程脚本。
- 关闭窗口即关闭本应用子进程；不干预原项目进程。

这些保护位于明确的桌面进程、HTTP 和 IPC 边界，不在业务函数中叠加重复校验或宽泛异常吞没。

## 12. 构建与打包

### 12.1 前端构建

副本 `next.config.ts` 设置：

```ts
output: "standalone"
```

构建时固定 `NEXT_PUBLIC_HANSER_CHAT_ONLY=1`。构建完成后验证 standalone 目录包含运行需要的 server、static 和 public 文件，不包含 L2D/Voice/Artifact 路由或依赖。

### 12.2 后端构建

使用专用 `requirements-runtime.txt`，只保留聊天运行依赖。PyInstaller 使用 `onedir`：

- entrypoint 为 `run_desktop.py`。
- 显式收集 Transformers/PyTorch 所需动态模块和二进制。
- Persona、数据库、userdict 和模型作为版本化资源，不打进 Python zip。
- 所有路径从 Electron 传入的绝对路径解析。
- 后端不得依赖源码仓库的当前工作目录。

### 12.3 Electron/NSIS 构建

输出：

```text
dist/
├─ HanserPureChatSetup-<version>-x64.exe
├─ latest-build-manifest.json
└─ unpacked/                   # 本地验证用，不作为最终交付
```

首版使用 per-user 安装，避免管理员权限。桌面快捷方式和开始菜单项均指向 Electron 主程序。

### 12.4 单文件说明

不采用“运行时、模型、数据库全部塞入一个便携 exe”的方案，原因是：

- PyTorch/Transformers 和模型体积大。
- onefile 每次启动需要解压到临时目录。
- 日志、SQLite 和用户设置本来就必须外置可写。
- 单文件更难诊断 DLL、杀毒误报和残留子进程问题。

用户仍然只需要运行一个安装程序，安装后只操作一个桌面应用。

## 13. 配置设计

配置分三层，后者只能覆盖允许项：

1. 安装包只读默认值：Persona、Style、检索和功能裁剪。
2. 用户设置：主题、成人轻度双关开关、模型 endpoint/model。
3. 加密秘密：API Key。

桌面设置页不能提供 Planner/Responder 分流、Persona 包选择或 Style generation 选择，避免产生第二套运行真源。模型与 Persona 有效组合仍由 Python Backend 在请求进入时冻结。

建议环境变量：

```text
HANSER_DESKTOP=1
HANSER_CONFIG=<absolute path>
HANSER_DATA_ROOT=<absolute path>
HANSER_MODEL_ROOT=<absolute path>
HANSER_BACKEND_PORT=<ephemeral port>
HANSER_DESKTOP_TOKEN=<ephemeral token>
HANSER_FRONTEND_PORT=<ephemeral port>
HANSER_API_BASE_URL=http://127.0.0.1:<backend port>
HANSER_USER_ID=local-user
```

不得在日志中输出包含 API Key 或内部令牌的完整环境。

## 14. 更新与回滚

### 14.1 首版更新

- 不启用自动更新。
- 新安装包可以覆盖应用二进制和只读资源。
- 不覆盖 `%LOCALAPPDATA%\HanserPureChat\data\documents.db`。
- schema 变化前创建一次带版本的数据库备份。

### 14.2 资源兼容

Persona package、Style generation、数据库 seed/index generation、detector/schema 和代码版本作为兼容组合发布。不能只替换其中一个文件。

### 14.3 回滚

- 回滚应用/Persona/Style 到 release manifest 指定的上一兼容组合。
- 不删除新聊天记录。
- 不回滚用户后来明确保存的拒绝和偏好。
- 数据库 schema 若发生不可逆升级，必须在发布前提供向后兼容读取或明确阻止旧程序启动，不能静默用旧代码写新 schema。

## 15. 测试与验收

遵循“聚焦测试优先、阶段边界再综合验证”。普通 UI 调整不重复计算哈希或运行全栈冒烟；入口、依赖、运行配置和候选包发生变化时执行对应验证。

### 15.1 静态裁剪验收

- 副本中不存在对 `voice_runtime`、L2D/MMD、renderer 的运行时引用。
- 生产依赖不含 Three.js、Voice、Artifact/editor 专用包。
- 安装包不启动 8770 Voice 端口或任何渲染进程。
- 四项 performance 开关固定关闭。
- 原项目功能文件无修改。

### 15.2 后端聚焦测试

- `/health` 同时报告 `ok=true`、`chat_ready=true` 和正确 Persona package。
- 纯聊天请求固定关闭 speech/dynamic L2D/offline performance。
- 新会话、连续多轮、历史恢复。
- 相同 request ID 幂等返回。
- request ID 与不同消息/设置冲突时保持现有明确失败。
- reply 已保存、PostTurn 失败时正文和 pending 状态都可恢复。
- Persona 明确拒绝、来源边界、外部称呼、格式保护等现有硬门禁。
- Style 只读取 reviewed、active generation。

### 15.3 前端聚焦测试

- 新建会话、发送、等待、成功、失败、重试。
- 历史分页、切换会话、刷新恢复。
- 中文、长文本、Markdown、代码块和复制。
- PostTurn pending 提示。
- 成人轻度双关设置每次显式传递。
- UI 中不存在 Voice/L2D/Artifact/附件/分享/删除入口。

### 15.4 桌面生命周期测试

- 首次启动建立用户目录和数据库。
- 第二次启动复用既有数据库。
- 单实例。
- Next/Backend 任一启动失败时错误页可用。
- 正常关闭无子进程残留。
- 强制结束后重新启动不会损坏 SQLite。
- 原项目已运行时，桌面副本仍使用独立端口和数据。
- 安装、覆盖升级、卸载；卸载默认保留用户数据并明确提示。

### 15.5 候选冻结综合验证

只在候选冻结边界执行一次：

- 干净 Windows 10/11 x64 环境安装。
- 无系统 Python、Node、pnpm 时能够启动。
- 无原项目目录时能够启动。
- 真实 provider 连通性和一轮真实聊天。
- 冻结 Persona/Text 回归与关键硬门禁。
- 资源、版本、许可证和 release manifest 对齐。
- 安装体积、启动时间、内存和端到端延迟记录为实测值。

## 16. 完成定义

同时满足以下条件才算 Pure Chat EXE 完成：

1. 用户从 Windows 安装程序完成安装，桌面图标可启动应用。
2. 用户无需手动打开终端、Python、Node 或浏览器。
3. 不依赖 `H:\HanserAgent` 原项目路径即可运行。
4. 原项目仍能用原启动器运行，功能和数据均未被副本改变。
5. 新应用只暴露文字聊天范围。
6. Persona、检索、记忆、历史和请求幂等链路通过聚焦验收。
7. 正常关闭无本应用子进程残留。
8. 用户数据库位于独立可写目录，升级不会覆盖。
9. API Key 不以明文进入源码、安装包、日志或 manifest。
10. 候选发布清单能说明代码、Persona、Style、数据库种子和模型资源版本，并有明确回滚目标。

## 17. 实施阶段与预计工作量

| 阶段 | 工作内容 | 预计 |
| --- | --- | ---: |
| P0 副本冻结 | 文件白名单、排除项、source baseline、独立目录 | 0.5 天 |
| P1 后端副本 | Chat-only 入口、移除 performance/voice、路径和 token 边界 | 0.5–1 天 |
| P2 前端副本 | 移除复杂组件与依赖、保留纯聊天协议和 UI | 0.5–1 天 |
| P3 桌面壳 | Electron 生命周期、错误页、设置/密钥、日志 | 0.5–1 天 |
| P4 打包 | Next standalone、Python onedir、NSIS | 0.5–1 天 |
| P5 聚焦验收 | 聊天、历史、记忆、生命周期和干净机验证 | 0.5–1 天 |

预计工程时间：

- 可启动 MVP：1–2 个工作日。
- 稳定安装候选：3–5 个工作日。
- 代码签名证书、模型再分发许可和外部发布流程不计入纯工程时间。

## 18. 实施顺序

严格按以下顺序推进：

1. 冻结并记录复制基线，不改原工程。
2. 建立独立目录，复制后端/前端运行白名单。
3. 先让副本以开发模式完成纯聊天闭环。
4. 移除静态依赖并完成聚焦测试。
5. 增加 Electron 进程编排和独立用户数据目录。
6. 冻结 Python runtime 和模型资源。
7. 生成 NSIS 安装包。
8. 在无 Python/Node、无原项目目录的环境做候选综合验证。
9. 生成 release manifest 和回滚说明后交付。

在第 3 步之前不做安装器，在第 4 步之前不宣称已经完成真正裁剪，在第 8 步之前不宣称 EXE 可独立部署。

## 19. 当前阶段输出

本阶段只完成架构设计：

- 没有修改现有功能代码。
- 没有复制或改写现有 SQLite。
- 没有启动服务或调用任何模型。
- 没有生成 EXE、候选包或 release manifest。
- 性能、包体积和真实启动指标均为 `NOT_MEASURED`。

下一阶段从 P0“副本冻结”开始。

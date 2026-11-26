# Hanser 对话与语音

Windows 上运行的桌面对话应用和 QQ 网关。两套入口共用当前对话后端，支持多轮聊天、记忆、Persona、RAG 和从已提交的回复生成语音。默认模型为本地 Ollama 的 `qwen3.5:4b`，无需 API Key 或模型账号。

## 桌面版

安装并打开 [Ollama for Windows](https://ollama.com/download/windows)。准备 Git、64 位 Python 3.13、Node.js 24 和 pnpm 11.19.0。要开启语音，还需要 Python 3.12、支持 CUDA 13.0 的 NVIDIA 显卡及驱动。首次安装与下载需要联网。建议为模型和依赖预留至少 15 GB 磁盘空间。

克隆发布分支后，在仓库根目录打开 PowerShell：

```powershell
git clone --branch codex/chat-voice --single-branch https://github.com/DUThansersuki/HanserPureChat.git
cd HanserPureChat
```

```powershell
powershell -ExecutionPolicy Bypass -File .\chat_voice_desktop\setup.ps1 -Voice
.\Start-Hanser-Chat.cmd
```

仅使用文字时，省略 `-Voice`。以后可以执行 `powershell -ExecutionPolicy Bypass -File .\chat_voice_desktop\setup.ps1 -VoiceOnly` 补装语音依赖。

桌面版启动时检查本机 Ollama，缺少 [Qwen3.5 4B](https://ollama.com/library/qwen3.5:4b) 时自动下载。点击聊天窗口的语音开关后，独立语音进程自动从 Hugging Face 下载固定版本的 [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2)，约 5 GB，缓存到 `chat_voice_desktop/voice_runtime/models/VoxCPM2`。后续启动复用缓存。首轮下载期间可以继续文字聊天。文字服务与语音同时运行所需显存取决于 Ollama 的显存分配与上下文设置。

桌面菜单可修改模型地址。默认地址为 `http://127.0.0.1:11434/v1`。使用外部服务时，可在设置窗口填入自己的 API Key，由 Windows 安全存储加密保存在本机。启动异常可以从状态页打开日志。

## QQ 网关

先启动 Ollama，再在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\qq_gateway\setup.ps1
.\Start-QQ.cmd
```

可以先用本地终端试聊：

```powershell
cd .\qq_gateway
.\.venv\Scripts\python.exe -X utf8 -m hanser_qq chat
```

接入 QQ 时，从 [NapCat 官方仓库](https://github.com/NapNeko/NapCatQQ) 安装 NapCat 并登录 QQ。添加 OneBot v11 反向 WebSocket `ws://127.0.0.1:8781/onebot/v11/ws`，Access Token 填 `qq_gateway/config.yml` 中的 `gateway.onebot_access_token`。网关在本机随机生成这个内部令牌。可用 `gateway.private_users` 和 `gateway.groups` 指定允许回复的用户与群。

QQ 语音共用桌面版的语音运行环境和模型缓存。按桌面版命令安装 `-VoiceOnly` 后，先按 Ctrl+C 停止正在运行的网关，再在 `qq_gateway` 中执行 `.\.venv\Scripts\python.exe -m hanser_qq voice-prepare`，把 `config.yml` 的 `gateway.voice_reply.enabled` 改为 `true` 并重启。模型缺失时自动下载。默认约 20% 的回复使用语音，模型加载中或合成失败时发送原文字。也可以用 `/voice` 切换回复模式。

## 依赖、数据与参考音色

- 根目录的 `requirements.txt` 汇总文字后端与 QQ 依赖。安装脚本分别安装桌面后端依赖、QQ 锁定依赖及语音 CUDA 依赖。语音清单位于 `chat_voice_desktop/voice_runtime/requirements-base.txt` 和 `requirements-cuda-lock.txt`。
- 首次设置生成空数据库，包含与 Persona 匹配的初始 Style 索引元数据。RAG 和语料发布功能保留。Wiki、Style 和 QQ 全量试用语料由使用者按自己的授权数据准备，QQ 可运行 `python -m hanser_qq --help` 查看发布子命令。本地已有种子可放在 `chat_voice_desktop/resources/database/documents.seed.db`，安装脚本会保留它。
- 默认使用 VoxCPM2 的无需参考音频模式。若有可授权使用的参考音频，可复制 `voice_runtime/config/voice_profile.chat_voice.yml` 到同目录下的本地配置，保持参考音频、逐字转录及来源清单在项目内。将 `HANSER_VOICE_PROFILE` 指向该配置再启动桌面版，或在 QQ 执行 `voice-prepare` 前设置同一环境变量。参考音色配置中可从默认 `voice_profile.local.yml` 复制模型下载设置。公开分支提供代码和模型配置，原始录音、聊天记录、语料库及本机凭据保存在使用者自己的目录。

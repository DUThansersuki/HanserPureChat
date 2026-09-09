# Hanser 语音与 Live2D 扩展链路

本仓库已按 `项目文档/语音_L2D拓展组件架构.md` 搭建 schema 1.1 的可选扩展链路。当前候选发布保持全部关闭：文本聊天仍走原链路，语音模型不会在导入或启动时自动加载，也不会生成伪音频。待图片、音频与 Live2D 源文件到位并通过门禁后，再逐项启用消费者。

## 当前结构

- `backend/`：一次回复内生成、校验并原子落库 `ReplySnapshot`，向独立运行时代理任务、事件、产物与播放回执。
- `voice_runtime/`：单 worker 有界队列，执行 speech 映射、分段、资产选择、VoxCPM2 公共 API 适配、48 kHz 单声道 PCM16 归一化、时间线与产物封装。
- `chatbot/`：同源代理、SSE 事件消费、Web Audio 播放时钟、暂停/恢复/中断和 Live2D 时间线适配。
- `renderer/`：离线渲染骨架；没有角色源文件时只能生成 presentation plan。
- `contracts/`：从后端与运行时 Pydantic 模型导出的 JSON Schema。
- `release/performance-stack.candidate.yml`：候选版本、门禁与一键回滚定义。

规范化文本 `semantic_text` 是语音和展示的共同事实源；`display_text`、`speech_text` 分别派生。语音与视觉能力独立裁剪，性能意图在切段前解析，参考资产在切段后选择。停顿只进入时间线，不进入音频缓存键。

## 安装

需要 Python 3.12。基础控制链路不加载任何模型：

```powershell
./scripts/setup_voice_runtime.ps1
```

安装固定版本的 CUDA PyTorch 与 VoxCPM2 包、下载固定 revision 权重：

```powershell
./scripts/setup_voice_runtime.ps1 -InstallVoxCPM -DownloadModel
```

版本冻结位置：

- 模型：`openbmb/VoxCPM2@32279effe8c19989596f05d353d1447f51d9e915`
- 包：`OpenBMB/VoxCPM@f772e498a45fbb5fb8e13fbf9b9c48be9fe33e69`
- 权重目录：`voice_runtime/models/VoxCPM2`（被 Git 忽略）

机器只需搭链路时，不要设置 `HANSER_VOICE_LOAD_MODEL=true`。

## 启动和检查

候选配置启动后只报告控制链路就绪，语音与视觉仍为未就绪：

```powershell
./scripts/start_voice_runtime.ps1
Invoke-RestMethod http://127.0.0.1:8770/runtime
```

完整本地聊天入口会在独立环境存在时同时启动该运行时：

```powershell
./scripts/start_hanser_chat.ps1
```

后端 `config.yml` 中的 `performance` 四个消费开关默认均为 `false`。浏览器也只有用户明确开启语音后，才会为后续回复请求 speech 输出。

## 素材到位后的启用顺序

1. 将人工确认的真人身份参考、风格提示音频及元数据写入 `assets/voices/hanser/manifest.jsonl`，以 `assets/voices/hanser/manifest.jsonl.example` 为格式参考。
2. 将 Live2D 模型放入 `assets/live2d/hanser/`，校准 rig preset，并填写模型路径。
3. 把 voice/rig profile 从 `candidate + enabled: false` 晋级为已审核 revision；不要直接放宽候选配置。
4. 完成冻结输入上的文本回归、语音 golden set、rig 校准和多模态验收，更新 `release/performance-stack.candidate.yml` 门禁。
5. 先启用 structured performance，再分别启用 speech、dynamic Live2D 和 offline export。任何回滚只需关闭消费者，不改历史对话、记忆或已落库快照。

## 不需要模型的验证

```powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_performance_contract.py
voice_runtime/.venv/Scripts/python.exe -m pytest voice_runtime/tests
pnpm --dir chatbot exec tsc --noEmit
backend/.venv/Scripts/python.exe scripts/export_performance_schemas.py
```

这些检查使用假 TTS 后端或仅验证控制面，不加载 VoxCPM2 权重，也不调用本地或外部模型。

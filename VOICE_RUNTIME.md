# Hanser 语音与 Live2D 扩展链路

本分支提供 schema 1.1 的 Chat + GPT-SoVITS v2Pro + MMD 动态角色集成链路。运行文件统一位于项目目录；Voice runtime 只支持 GPT-SoVITS，不包含其他 TTS 后端。

## 当前结构

- `backend/`：一次回复内生成、校验并原子落库 `ReplySnapshot`，向独立运行时代理任务、事件、产物与播放回执。
- `voice_runtime/`：单 worker 有界队列，执行 speech 映射、分段、资产选择、GPT-SoVITS v2Pro sidecar 适配、48 kHz 单声道 PCM16 归一化、时间线与产物封装。
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

### GPT-SoVITS v2Pro

GPT-SoVITS 使用它自带的 Python 3.9/CUDA 11.8 运行包作为 loopback sidecar，不能导入本项目 Python 3.12 进程。候选配置固定为非流式单段请求；项目侧继续负责已审核资产选择、确定性 seed、任务取消、缓存、基础 QA 和 32 kHz 到 48 kHz 的一次高质量重采样。

项目内运行文件：

- 分发目录：`voice_runtime/models/GPT-SoVITS-v2Pro`
- sidecar 配置：`voice_runtime/config/gpt_sovits_v2pro.sidecar.candidate.yml`
- runtime profile：`voice_runtime/config/voice_profile.candidate.yml`
- 启动脚本：`scripts/start_gpt_sovits_v2pro_sidecar.ps1`

默认 profile 使用完整的 v2Pro 底模和已人工核查的项目内参考音频：

```powershell
./scripts/start_gpt_sovits_v2pro_sidecar.ps1
$env:HANSER_VOICE_PROFILE = "voice_runtime/config/voice_profile.candidate.yml"
$env:HANSER_VOICE_LOAD_MODEL = "true"
./scripts/start_voice_runtime.ps1
```

GPT-SoVITS 的 `/set_gpt_weights`、`/set_sovits_weights` 和 `/control` 不由 Hanser runtime 暴露或调用；权重组合只在 sidecar 启动配置中冻结，避免运行中全局切换影响在途任务。endpoint 只允许 loopback HTTP origin，不接受远程地址或重定向。

上游静态审计（2026-09-11）：

- 官方最后一个正式 release/tag 仍是 `20250606v2pro@d7c2210`；不能把当前 `main` 当作新的已发布模型版本。
- 官方 `main@48b1a016` 已包含 2025-12 的采样/吞句修复、2026-04 的音频后处理与中文多音字/长句开销修复。这些变更适合在独立副本中固定 revision 后做下一轮候选，不覆盖 `K:` 下无 Git 元数据的测试包。
- 项目内测试包的 `/tts` 已包含新版流式字段，和本适配器所用的非流式请求兼容；核心 `TTS.py` 仍缺少 2026-04 的若干修复，因此当前定位为本地集成基线。
- 2026-04 的 CUDA Graph 加速目前接在官方 WebUI 普通推理路径，并未进入 `api_v2.py` 的 `/tts` 契约；6 GB 显存机器上不在 sidecar 适配器里私自移植或默认开启。
- 当前配置显式冻结 `top_k=5` 以保证本地基线可复现。

后续升级应在独立副本中固定上游 revision，并在同一冻结输入上验证显存峰值、截断/吞句、中文多音字与长句。

## 启动和检查

启动 GPT-SoVITS sidecar 后再启动 Voice runtime：

```powershell
./scripts/start_voice_runtime.ps1
Invoke-RestMethod http://127.0.0.1:8770/runtime
```

完整本地聊天入口会在独立环境存在时同时启动该运行时：

```powershell
./scripts/start_hanser_chat.ps1
```

本地集成配置已开启 structured performance、speech runtime 和 dynamic MMD/L2D；offline export 仍关闭。浏览器只有在用户明确开启语音后，才会为后续回复请求 speech 输出。Voice 返回的音频时间线会逐帧驱动 MMD 口型、表情和受支持动作。

## 当前素材与后续验收

1. 两段人工确认的身份参考与中性提示音频已纳入 `assets/voices/hanser/` 和 manifest。
2. MMD 模型与贴图位于 `.runtime/mmd/hanser_v2.0_cloth2_test/`；GPT 分发包位于 `voice_runtime/models/GPT-SoVITS-v2Pro/`。两者都在项目目录内，重型运行时不提交 Git。
3. voice/rig profile 已标记为 `validated + enabled`，用于本地集成候选。
4. 静态与假后端测试通过；真实推理 golden set、动态 rig 目视校准和端到端多模态验收仍需在启动服务后执行。
5. 回滚只需关闭 performance 消费开关，不改历史对话、记忆或已落库快照。

## 不需要模型的验证

```powershell
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_performance_contract.py
voice_runtime/.venv/Scripts/python.exe -m pytest voice_runtime/tests
pnpm --dir chatbot exec tsc --noEmit
backend/.venv/Scripts/python.exe scripts/export_performance_schemas.py
./scripts/verify_integrated_stack.ps1
```

这些检查使用假 TTS 后端或仅验证控制面，不执行真实 GPT-SoVITS 推理。

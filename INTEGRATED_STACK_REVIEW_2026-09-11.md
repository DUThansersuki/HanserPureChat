# Chat + MMD/L2D + GPT Voice 分支完整性与可用性审查

审查日期：2026-09-11  
分支：`codex/chat-l2d-gpt-voice-full`  
基线：`main@62f03c5`  
集成功能来源：`codex/gpt-sovits-v2pro@3903f5b`

## 结论

静态完整性与不启动服务条件下的可用性检查通过。分支包含 Chat、MMD/L2D、GPT-SoVITS Voice 三条链路，并已将 Voice 播放的逐段音频时间线接入 MMD rig，使口型、表情与受支持动作共享同一个音频采样时钟。

Voice 后端已收敛为 `gpt_sovits_v2pro`；活动代码、配置和文档中不存在 VoxCPM2/OpenBMB 引用。GPT-SoVITS 分发包、MMD 模型、贴图与已审核参考音频均位于 `H:\HanserAgent` 项目目录内。

## 完整性证据

- GPT-SoVITS 本地分发目录：`voice_runtime/models/GPT-SoVITS-v2Pro`，50,576 个文件，约 13.15 GiB。
- MMD 本地模型目录：`.runtime/mmd/hanser_v2.0_cloth2_test`，包含 PMX、7 张基础贴图和衣装替换贴图。
- Voice 资产：`assets/voices/hanser/reference/identity.wav` 与 `assets/voices/hanser/prompts/neutral.wav`；manifest 两项均为 `approved`。
- Voice profile 与 rig profile 均能由 Pydantic 配置加载器解析，状态为 `validated + enabled`，解析后的模型路径仍位于项目内。
- `scripts/verify_integrated_stack.ps1` 对关键文件、项目路径边界、GPT-only profile 和资产审核状态执行复核并通过。
- 一键启动脚本已编排 GPT-SoVITS sidecar → Voice runtime → Chat backend → Chat frontend；本次遵照用户要求未执行启动。

## 验证结果

- Backend 聚焦测试：18 passed。
- Voice runtime 测试：10 passed（仅有第三方 Starlette/httpx 弃用警告）。
- MMD 动作库测试：3 passed。
- Frontend TypeScript：`tsc --noEmit` passed。
- Frontend Biome（本次相关文件）：passed。
- PowerShell 脚本语法：5 个集成脚本全部通过解析。
- Git diff whitespace：passed。
- VoxCPM2/OpenBMB 活动引用扫描：0 项。

## 边界与待验收项

本次没有启动 sidecar 或监听端口，因此没有声称真实 GPT 推理、GPU/CUDA 加载、最终音色、浏览器音频策略和动态画面效果已做端到端验收。发布清单继续将 voice golden set、rig 目视校准和 multimodal acceptance 保持为未完成门禁；这不影响当前分支的静态集成完整性，但正式发布前必须在目标机器上补齐。

GPT-SoVITS 与 MMD 重型运行文件位于项目内但由 `.gitignore` 排除；Git 提交包含配置、适配代码、已审核小型参考音频以及可重复准备/验证脚本，不把约 13 GiB 的第三方运行包写入仓库历史。

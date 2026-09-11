# 集成栈静态复核（2026-09-11）

## 结论

当前分支的活动组合已切换为 Chat + VoxCPM2 hybrid Voice + MMD/L2D。GPT Voice 的适配器、sidecar 配置、准备脚本与启动编排均已移出活动工程链路。

本次只做静态复核和不加载模型的聚焦测试，没有启动服务，也没有执行真实语音生成。

## Voice 冻结项

- profile：`hanser-voxcpm2-hybrid-clip180-20260911-1`
- backend：`voxcpm2_hybrid_vae_cpu_1`
- model：`voice_runtime/models/VoxCPM2`
- package：`voxcpm-f772e498a45f`
- device：主模型 CUDA、AudioVAE CPU
- prompt/identity：人工核查的 `clip_000180.wav` 与精确转写
- output：48 kHz mono

此前单独验证该混合设备方案时，完整 CUDA 流程在 6 GB 显存目标上于参考音频编码阶段 OOM；迁移 AudioVAE 到 CPU 后可以完成生成。因此本分支固定采用该轻量显存布局，而不是换模型或量化模型。

## 组合链路

1. Chat 回复由 backend 保存不可变 reply snapshot，并按 speech / dynamic_live2d 输出偏好授权消费。
2. `RenderBridge` 把通过 owner 校验的语音任务代理到本地 Voice runtime。
3. Voice runtime 使用 VoxCPM2 hybrid 生成 48 kHz segment package 和时间线。
4. 浏览器 `PlaybackController` 以音频 sample offset 作为播放时钟。
5. `timeline-evaluator` 和 `mmd-rig-adapter` 消费同一 segment timeline，驱动口型、表情和允许的动作。

## 本轮门禁含义

`scripts/verify_integrated_stack.ps1` 只验证以下静态事实：

- VoxCPM2 模型文件、MMD 模型和活动参考音频都在项目路径内；
- profile 固定为 CUDA 主模型 + CPU AudioVAE，且不存在 GPT endpoint/sidecar 设置；
- Chat backend 同时开启 speech 与 dynamic L2D，并连接本地 Voice runtime；
- 启动脚本会加载当前 Voice profile，且不再编排 GPT sidecar；
- release manifest 的 Voice、timeline 与 rig 版本组合一致。

真实音质、峰值显存、端到端延迟、口型观感和多模态验收仍保持 `not_measured` 或未放行状态，必须在用户允许实际运行后另行确认。

## 真实工作流补测

随后按用户授权执行了真实页面工作流，输入“你好呀”，得到文字“你好呀 今天想聊点什么”。页面自动创建 Voice 任务并完成音频下载、播放开始/完成回执；播放状态依次经过 `preparing -> playing -> finished`。MMD 舞台在 `playing` 期间消费同一个 audio sample offset，画面观察到嘴部由闭合变为张开，结束后回到 neutral。

- Chat 快照：`speech=true`、`dynamic_live2d=true`，delivery 为 `gentle`、intensity 为 `0.4`。
- 文字阶段记录：planner 11.85 秒、style 10.22 秒、context 0.02 秒、responder 2.46 秒。
- Voice：文字完成后约 20 秒产出，音频 48 kHz、215040 samples、4.48 秒；timeline 为 amplitude mouth cues，expression 为 `soft_smile`。
- GPU：RTX 3060 Laptop 6 GB；完整工作流观测峰值约 5929/6144 MiB，最低空闲约 66 MiB，成功但安全余量不足。

补测同时发现并修复两项真实阻塞：Voice 入口被 Uvicorn 二次导入导致模型重复加载；Chat 快照中的 `persona_trace` 超出 Voice schema 导致 422。入口现直接复用已创建 app，RenderBridge 会在 Voice/visual 边界投影掉 Persona 调试轨迹。

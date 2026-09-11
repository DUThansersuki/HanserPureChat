# Hanser Voice runtime

本分支使用 schema 1.1 的 Chat + VoxCPM2 hybrid Voice + MMD/L2D 集成链路。GPT Voice 已退出项目活动配置与启动流程。

## 当前 Voice 方案

- 后端：`voxcpm2_hybrid`
- 模型：项目内 `voice_runtime/models/VoxCPM2`
- 显存策略：VoxCPM2 主模型放 CUDA，AudioVAE 编解码放 CPU
- 推理：`cfg_value=2.0`、`inference_timesteps=10`、固定 seed 派生
- 输出：48 kHz、单声道
- 参考音频：`assets/voices/hanser/training/v1/clip_000180.wav`
- 参考文本：来自人工核查的同名文本，并由 manifest 保留来源与版本

活动配置位于 `voice_runtime/config/voice_profile.candidate.yml`。它禁止运行时下载、后端切换和递归生成参考音频；模型只在 `HANSER_VOICE_LOAD_MODEL=true` 时加载，因此控制面与静态联调可以不占用显存。

## 与 Chat 和 L2D 的组合

Chat backend 根据回复快照向 Voice runtime 创建任务。Voice runtime 完成 speech 映射、分段、参考资产选择、VoxCPM2 合成和 48 kHz 时间线封装。前端播放控制器以同一采样时钟播放音频；MMD rig adapter 通过 timeline evaluator 读取该时钟，驱动口型和允许的表情/动作。

```text
Chat reply snapshot
  -> backend RenderBridge
  -> Voice runtime /internal/v1/jobs
  -> VoxCPM2 hybrid segment + 48 kHz timeline
  -> browser PlaybackController
  -> MMD timeline evaluator / rig adapter
```

组合版本固定在 `release/performance-stack.candidate.yml`。后端开关位于 `backend/config.example.yml`：`speech_runtime_enabled` 与 `dynamic_live2d_enabled` 均开启，并指向 `http://127.0.0.1:8770`。

## 启动与静态验证

需要实际运行时使用：

```powershell
./scripts/start_hanser_chat.ps1
```

该脚本依次编排 Voice runtime、Chat backend 和前端，不再启动 GPT sidecar。

只检查文件、配置与组合关系，不启动服务或模型：

```powershell
./scripts/verify_integrated_stack.ps1
```

聚焦测试同样使用假 TTS 后端或 `load_voice_model=false` 控制面，不执行真实 VoxCPM2 推理：

```powershell
./voice_runtime/.venv/Scripts/python.exe -m pytest voice_runtime/tests
```

真实音质、端到端延迟和多模态验收仍属于后续运行门禁；本轮静态切换不会把它们标记为已测量。

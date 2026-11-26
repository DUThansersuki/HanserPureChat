# QQ 全量语料验证记录

当前已按用户 2026-10-18 明确指令，停止效果评估循环并启用全量原文 **user_trial**。原报告保留 HOLD；用户先检查可运行版本，再决定后续微调。

## 最近一轮已经完成

`cases.v8.jsonl` 共 180 项：144 项回归和 36 项新留出场景。A/B 均重新真实调用，360 输出完整，无请求失败。候选硬失败 0，通过状态/操作、全体文本、来源覆盖和独立状态门禁；独立留出集文本非劣的置信区间不足，判定 **HOLD**。682 次真实模型调用，1,504,241 tokens。

`ratings.v8.blind.json` 保存完整 X/Y 评分、理由和硬失败；评审由本次编码代理完成，不是熟悉人物的真人或音频校准。序列评审保留前轮上下文，写完评分后解盲。状态金标检查实际 Memory 和操作账本，来源覆盖计算真正 injected 的 ID，不能以 selected 代替。

该轮评估的是 **审核片段模式**，只准入 413 条精确短反应，145 种表面表达、199 篇来源；完整 A 仍不批准。后来按用户要求启用的原文试用新增代码和运行范围，没有再跑 A/B，不能复用上述结果宣称原文模式通过质量门禁。

报告 [evaluation.v8.json](../validation/evaluation.v8.json)；原始请求、完整提示、输出、trace、快照、实际调用账本保存在本机 `.runtime/full_eval_20261018_001/frozen_v8/`。保留全部失败与历史报告：v2 有偏好误记/删除虚报，v3/v4 仍有归属或自身口味问题；v5/v6 因近期行程确定否认提前停止并记缺失，v7 做针对性开发检查，v8 完成整轮。未把旧输出拼成新结果。

## 当前试用的功能证明

用户要求“这次验证跑完之后，不论结果如何，都直接切换全量语料使用”。因此 `--user-trial` 激活 pending 原文，不依赖 PASS；选择器记录原评估结果、试用授权、实际引擎与 `formal_approval=false`。正常不带该参数的正式审核片段发布仍要求匹配 PASS。

- 相关发布/上下文/记忆测试 12 项通过：默认模式不注入原文，试用原文实际进入模型请求并保留 pending、来源坐标；HOLD 可明确试用且不冒称批准；回滚保留新用户数据。
- [隔离迁移检查](../validation/migration.qq-full-corpus-20261018-v8.json)：升级、源码刷新、回滚保留发布后新增数据。
- [实际安装检查](../validation/installed.qq-full-corpus-20261018-v8.json)：实际工厂和 HTTP，现有环境真实模型回复，3 条 `qq-trial:` 来源实际注入，同 request_id 重放没有新增调用。
- [本机离线就绪](../validation/readiness.user-trial.json)：真实 QQ 运行组合已切换、可启动。只读检查本身不发网络请求。
- 未连接 QQ、未发送账号消息。第 7 步在 [NAPCAT_NEXT.md](../NAPCAT_NEXT.md)。

可在服务启动后用 `python -m hanser_qq chat` 检查当前试用效果，无需先连接 QQ。未来由用户检查结果决定微调，不自动继续新一轮长评估。

## 复核已有结果与激活

在 `qq_gateway` 下执行，以下评估命令只汇总既有输出，不调用模型：

```powershell
.\.venv\Scripts\python.exe eval/assess.py --run .runtime/full_eval_20261018_001/frozen_v8 --cases eval/cases.v8.jsonl --ratings eval/ratings.v8.blind.json --output validation/evaluation.v8.json
# 本机已执行，无需重复。停止服务后才能切换组合。
.\.venv\Scripts\python.exe -m hanser_qq release-publish --release-id qq-full-corpus-20261018-v8 --evaluation validation/evaluation.v8.json --user-trial
.\.venv\Scripts\python.exe -m hanser_qq release-rollback
```

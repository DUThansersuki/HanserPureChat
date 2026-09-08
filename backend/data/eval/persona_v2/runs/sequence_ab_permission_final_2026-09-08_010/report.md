# Persona v2 多轮 Text A/B

- 序列 / 轮次：1 / 12
- 生成失败：1
- 裁判失败率：0.00%
- 换位不一致率：0.00%
- B 硬失败：0
- 裁决：**反向**
- 原因：candidate generation failure or hard-gate failure

自然历史按 variant 独立滚动；Planner 使用同会话的用户消息历史共享一次，避免把不同候选回复直接带入 Planner 造成额外混杂。

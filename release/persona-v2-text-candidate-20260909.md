# Persona v2 聊天 Agent 收口报告（2026-09-09）

修改目标：完成 `Persona v2待进行_0909_18.md` 中聊天 Agent 的 P0、P1、P2 实现与验收闭环，并在证据不足时保持候选、不替换生产。

实际修改文件：核心变更覆盖 `backend/hanser_agent/persona/`、ChatAgentService/ContextBuilder、Memory/PostTurn、Responder、API/config/db、候选 Persona 包、候选 Style 审核资产、评估脚本与测试；完整列表以当前 `main` 工作树 diff 为准。新增冻结输入、coverage、参数消费者表和本发布清单。

对应本文任务 ID：P0-01 至 P0-06；P1-01 至 P1-11；P1-DATA-01 至 P1-DATA-03；P2-01 至 P2-04；L01 至 L13。

根因与实现选择：统一显式偏好事件入口，禁止 Planner 信号直接成为硬规则；局部停止按 topic 生效；成年/未成年仅由当前用户显式文本形成硬门槛；行为卡、有效参数、统一 Style 排序、recent-example 和长期表达偏好均接入真实运行路径。删除话题绑定固定回复，保留一个 Responder，不新增闲聊 Router、FSM 或逐轮 Judge。

人物证据与 owner_preference：trait ledger 已分列 `source_supported`、`owner_preference`、`hypothesis`；设计样例不提升真人频率证据。`plain_warmth_without_infantilizing` 保持 research candidate，未因发布需要越级。

Descriptive / override / Effective 差异：描述性证据只提供先验；owner preference 作为明确产品偏好；override 只在其生命周期允许时参与；Effective 由边界、用户显式偏好和运行参数合成。关系数值、笑声、轮数和 memory write 不再推导表达许可。

参数消费者变化：`behavior.yaml` 已进入 Policy soft prior；humor/teasing/meme/profanity/innuendo/cutesy 等参数进入统一 effective settings、排序和输出约束。详细映射见 `backend/data/persona/parameter_consumer_matrix.md`。

语料新增 / 隔离 / 待审 / 缺口：候选共 840 条，运行时 eligible 416 条；101 条高曝光事实 payload 已 quarantine，323 条 schema 未批准，设计日常 episode 12 条仅用于产品行为验证、运行时为 0。来源 episode 仍有 7 条 pending，不能证明跨场景频率。

production 与 candidate 的包 / 配置 / index 组合：

- Production 保持 `persona-v1-production + source_data/documents.db + legacy-v1(revision 1)`。
- Candidate 固定为 `hanser-persona-v2-candidate-20260908 + candidate.db + persona-v2-style-safe-7bd0970e2164b249(revision 3, 416 items)`。
- 候选数据库已迁移到当前 memory schema。旧生产库和 `legacy-v1` 未修改、未删除。
- 组合、hash 和回滚目标见 `release/persona-v2-text-candidate-20260909.yml`。

执行的聚焦无模型检查及结果：L01-L13 与相关单测通过；最终全量 pytest 为 141 passed、0 failed（仅 3 个第三方 deprecation warning）。开发集 Signal 离线复核为 gold 25/25、unknown 24/24。冻结输入中成年案例把 `audience_age_status` 同时列入 gold 与 unknown，评估脚本已报告并从 unknown 重复分母剔除，未改写隐藏输入。

执行的模型操作：

- Planner：本地 Ollama `qwen3.5:4b`。
- Responder：外部 `deepseek-v4-flash`。
- 双向盲评 Judge：外部 `deepseek-v4-pro`。
- CUDA embedding：`torch 2.11.0+cu130`，RTX 3060 Laptop GPU，float16；未使用 CPU torch 作为最终运行环境。
- 最新 24-case 开发 A/B：24 Planner + 48 Responder + 48 Judge，全部成功。
- 隐藏集仅执行一次，没有在最终 Signal 校准后重跑。

Signal / Policy / Retrieval / Generation 证据：

- Signal：最新开发 trace 的 gold 25/25，unknown 24/24；Planner 无依据的成年/中性情绪推断降为 unavailable，硬规则只接受显式证据。
- Policy：显式拒绝、局部停止、成人适用门槛、跨用户隔离和来源边界的候选硬失败为 0。
- Retrieval：安全 generation 416/416 可用，24 个开发案例 A/B Style 非空覆盖均为 24/24；fact payload 不进入候选运行时。
- Generation：最新开发集 B 相对 A 的 composite delta 为 +0.375，95% CI `[+0.131944, +0.607639]`；B hard failures 0，Judge 换位不一致率 0.2083。预注册的 contextual release improvement 未被证明。
- 多轮：3 个开发序列、36 轮、B hard failures 0，换位不一致率 0，但样本门禁不足，结论未证实。
- 真实 lifecycle：12/12 turn 完成，PostTurn 全部完成；conversation ownership、长期禁粗口、新用户撤销优先、跨用户 memory 隔离、trace 完整、包与 generation 固定共 8/8 检查通过。

硬门禁失败：候选在最新开发 A/B、隐藏 A/B、多轮 A/B 和通过的真实 lifecycle 中均为 0。早期 lifecycle `.001/.002` 是修复前诊断运行，不计为候选验收证据。

未执行、未知和剩余限制：当前代码最后校准发生在唯一隐藏运行之后；为保持隐藏集不重复暴露，未重跑。隐藏样本仅 8 条且 Judge 不一致率 0.375；日常多轮仅 3 个开发序列；合适机会偶发放飞的预注册改进没有得到证明。当前变更位于 `main` 工作树但未形成可部署 commit，因此 `deployable_revision_frozen=false`。TTS、Live2D、语音和设置控制台不计入本次 Text 结论。

结论：**保持候选 / 未证实**。不切换生产，不填写实际启用时间，不把总体分数提升替代缺失的目标证据。

下一批次：新增且预先冻结一组不复用当前隐藏题的 contextual-release 与日常多轮样本，先校准 Judge 换位一致性，再做一次新隐藏泛化验收；全部门禁通过后，才创建可部署代码 revision 并进入生产切换评审。回滚始终保留聊天数据、Memory 和用户后来明确保存的偏好。

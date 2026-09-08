# Persona v2 阶段 4 实施与审查报告

报告时间：2026-09-08

修改目标：

按《Hanser Persona 专项架构与执行规范》复核此前中断状态，继续完成 Stage 4 Text 端到端审查与已复现缺陷修复。重点处理显式停止后的许可持久化、未定义指代、未经证实的共同记忆、精确输出契约，以及一次受约束重写仍失败时的有界降级。同步把“减少防御性编程，大幅减少哈希校验和冒烟测试频率”写入规范和项目级 Codex 记忆。

实际修改文件 / package_id / parent_release：

- package_id：`hanser-persona-v2-candidate-20260908`
- parent_release：`persona_v1`
- 最终冻结候选：`persona_v2_candidate_2026-09-08_011`
- 主要实现文件：`persona/schemas.py`、`persona/signals.py`、`persona/permissions.py`、`persona/policy.py`、`persona/output_contract.py`、`agent/context_builder.py`、`responder/service.py`、`responder/validator.py`、`prompts/planner.md`。
- 候选包变更：`style_constraints.yaml`、`manifest.yaml`。
- 聚焦测试：`test_persona_permissions.py`、`test_persona_signals.py`、`test_persona_policy.py`、`test_unified_responder.py`。
- 项目治理：根目录 `Hanser_Persona_专项架构与执行规范.md` 与 `AGENTS.md`。
- 候选数据库：`candidate.db`；构建摘要见同目录 `build_summary.json`。

人物证据与 owner_preference 分别是什么：

- Descriptive evidence 沿用 Stage 1 冻结资产：562 条来源清单、7 个 transcript-only episode、6 个 `source_supported` trait 候选；7 个 episode 仍为 pending，不能据此声称已测得人物真实频率。
- owner_preference 仍为 4 项：自然默认姿态、降低主动卖萌、合适语境适度放开玩梗/轻爆粗/非露骨双关、有理由的独立判断。
- 本轮新增的是工程和输出边界修复，不把产品偏好提升为人物事实，也没有生成新的风格样例。

Descriptive / product override / Effective Persona 的差异与未解决冲突：

- Descriptive 账本保持原来源与适用范围；Product override 继续以独立授权引用记录。
- Effective Persona 保持自然底色、低 cutesy 软倾向、适中 warmth/candor；成人语境、事实边界、共同记忆与显式拒绝属于不可放宽门槛。
- 显式停止后，后续笑声或“我开玩笑的”不自动恢复 humor/teasing；“下次可以”也必须等未来那一轮再次明确允许。
- 未解决冲突：来源表达频率仍未建立；直播语境到一对一聊天的迁移有效性仍未知；用户偏好与来源还原度尚无独立人工锚点评审。

默认参数及改动依据：

- `humor_initiative=0.35`、`teasing_intensity=1`、`meme_affinity=0.30`、`profanity_level=1`、`innuendo_level=1`。
- `cutesy_bias=0.10`、`warmth=0.55`、`candor=0.60`、`reply_length=adaptive`。
- `address_bias=0.15`、`display_punctuation=legacy_sparse`、`repetition_penalty=0.30`、`stacking_aversion=medium`、`observation_turns=6`、`recency_decay=0.7`。
- 参数未因单次失败被搜索或扩张；本轮只修已复现的硬边界和精确输出失败模式。

语料新增/拒绝/待审/缺口（按场景与来源）：

- 原审核集合 840 条；最终候选中 v2 runtime eligible 517 条，rejected/pending 323 条。
- 独立校准样本 96 条：decision agreement `0.75`，payload agreement `0.447917`，behavior exact agreement `0.364583`，expression exact agreement `0.84375`。
- 未新增模型生成样例；向量沿用文本完全相同的既有 embedding，并发布为候选内部 generation `persona-v2-style-f5a39aec2e5c20eb`。
- 缺口仍包括来源频率、跨语境迁移、人工锚点和当前最终候选的完整多轮重复评估。

生效的 production/candidate/config/index 组合：

- Production 未变：`persona_v1` + `H:\HanserAgent\source_data\documents.db` + `legacy-v1` generations。
- Candidate：`hanser-persona-v2-candidate-20260908` + `persona_v2_candidate_2026-09-08_011/candidate.db` + Style generation `persona-v2-style-f5a39aec2e5c20eb`。
- `production_switched=false`；没有修改生产数据库、生产 Persona 指针或 WPF contract。

已执行的离线检查及结果：

- 修复过程按改动面执行聚焦测试，阶段结果分别为 28、42、40 个通过；这些集合有重叠，不相加作为覆盖率。
- 最终直接相关检查：`tests/test_unified_responder.py`，11 passed，0 failed；仅有 `jieba/pkg_resources` 弃用 warning。
- 候选包只在冻结边界计算一次 manifest/候选数据库完整性信息；没有在普通编辑后重复做哈希校验。
- 没有运行全量 pytest 或无关冒烟测试。

已执行的模型操作（无则明确无）：

- 已获用户授权后执行 Planner、Responder A/B 与 Judge；没有执行 TTS。
- 所有带完整 `metrics.json` 的本阶段运行合计记录：Planner 656 calls / 807,616 tokens；Responder A 584 calls / 1,022,604 tokens；Responder B 595 calls / 1,547,149 tokens；Judge 310 calls / 1,603,550 tokens。
- `sequence_ab_6x12_2026-09-08_005` 只有 snapshot；`..._006` 在生成前因错误 Python 环境缺少依赖而中止，均保留作审计，不计入上述完整指标。
- 关键最终定向运行 011：Planner 12/12、A 12/12、B 14/14、Judge 2/2 成功；B 多出的 2 次是固定上限为一次的约束重写。

Signal / Policy / Retrieval / Generation 四层指标、unknown 与失败归因：

- Signal：干净完整多轮运行 008 中 Planner 72/72 成功；最终定向运行 011 中 12/12 成功。规则层新增未定义指代跨轮保持、未经证实共同记忆提示；复杂隐含语义的真实召回率仍 unknown。
- Policy：显式停止被持久化为 humor/teasing deny；共同记忆在无可信 History/Memory 时不得确认，也不得猜测用户记错；精确命令输出进入独立输出契约。
- Retrieval：517 条审核合格候选参与 Style generation。有限消融没有证明架构块本身的独立贡献，因此检索/信号贡献仍不能单独归因。
- Generation：完整多轮运行 008 为 144/144 输出、B hard failure 0；人工审查发现许可、精确命令、未定义指代和共同记忆缺陷，说明 Judge 存在漏判。针对性 009/010 曾分别出现 2/1 个候选失败，均归因于单次约束重写仍违反明确许可；011 加入有界降级后最终许可序列 24/24 输出、0 失败。

Text A/B 对照范围、语料或模型混杂、有限消融结果：

- 早期单轮 72 项运行（candidate 002）为 B 103 / tie 24 / A 17，B hard failure 0，自动结论“正向候选”；它不是最终候选 011，且后续人工审查证明 Judge 会漏掉语义边界错误，不能直接作为发布结论。
- 24 项受控消融为 B 21 / tie 17 / A 10，结论“未证实”；因此不能声称新架构本身已经独立证明优于相同语料基准。
- 干净 6×12 多轮运行 008 为 144/144 生成，双向判断 B 8 / tie 2 / A 2，B hard failure 0，但 2/6 序列顺序不一致，自动结论“未证实”。
- 最终 011 只复跑已修复的许可序列：双向判断均偏好 B，B 六项均分 5.0，0 顺序不一致；样本量不足以替代完整预注册多轮集合。
- A/B 同时包含审核语料与架构变化，存在语料/策略混杂；有限消融仍未消除归因不确定性。

目标集合的胜/平/负、频率与不适合插入率：

- 单轮旧候选：B/A/tie = 103/17/24（按 144 个方向判断）。
- 完整多轮 008：B/A/tie = 8/2/2（按 12 个方向判断）。
- 最终许可序列 011：B/A/tie = 2/0/0。
- 人物表达真实频率、主动玩笑频率及“不适合插入率”没有独立可靠标注，保持 unknown；不得用 Judge 偏好反推人物自然频率。

普通聊天、温柔、独立性、多轮恢复的退步检查：

- 旧候选单轮总体未见 material naturalness/warmth regression；完整多轮 008 也未报告 material regression。
- 人工审查发现自动指标会漏掉“停止后继续回梗”等问题，本轮已对精确失败链路做定向修复。
- 最终 011 只证明许可撤回链路恢复：t3 立即停止，t7 不回接玩笑并回到正事，t8 持续不接梗，t12 不把未来允许当当前授权。
- 最终候选 011 的普通聊天、温柔和独立性全量回归未重复执行，状态仍为 unknown。

硬门禁失败：

- 早期自动运行中 candidate hard failure 均为 0，但人工审查发现了 Judge 漏判的事实/许可问题，已作为真实缺陷处理。
- 最终许可运行 011：candidate generation failure 0，hard failure 0，permission respected，人工审查通过。
- 当前最终候选没有完成一次新的全量预注册多轮门禁，因此不能把局部通过写成整体通过。

NOT_EXECUTED / unknown / 缺失样本：

- 最终候选 011 的全量 72 单轮与 6×12 多轮冻结复跑：`NOT_EXECUTED`，以遵守减少重复冒烟和无必要模型调用的工程要求。
- Stage 5 settings API、控制台、semantic_text → display/speech 双分支、speech payload 与实际 TTS：`NOT_EXECUTED`，因为 Text 尚未达到整体“正向”门槛。
- Stage 6 发布、release 指针切换和生产回滚演练：`NOT_EXECUTED`；规范明确当前任务不进行该阶段。
- 人工锚点、来源频率、跨语境迁移、最终候选整体回归：unknown。

结论：正向候选 / 反向 / 未证实：

**未证实。** 候选 011 已修复并通过已知许可撤回硬门禁，早期单轮与多轮结果显示明显正向信号，但当前最终组合没有完成一次满足预注册范围与顺序一致性要求的完整 Stage 4 裁决，有限消融也未证明架构独立贡献。不能据局部定向通过进入 Stage 5 或切换生产。

允许启用的范围、回滚版本与遗留问题：

- 允许范围：离线候选、聚焦验证和后续人工审查；不允许生产启用。
- 当前生产/回滚基线：`persona_v1` + `H:\HanserAgent\source_data\documents.db` + `legacy-v1` generations。
- 遗留问题：在下一次确有发布决策或重大变更的阶段边界，才对最终冻结候选做一次完整预注册 Text 复跑；加入独立人工锚点以处理 Judge 顺序偏差和语义漏判；根据完整结论决定 Stage 5 或继续修复。
- 工程治理已更新：普通开发优先根因修复与聚焦测试，只在候选冻结/发布/索引代际等明确边界做必要哈希校验；不取消 Persona 硬门禁、来源追溯和回滚兼容要求。

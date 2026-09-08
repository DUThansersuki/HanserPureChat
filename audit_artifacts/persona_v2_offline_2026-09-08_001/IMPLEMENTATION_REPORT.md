# Persona v2 离线实施报告

修改目标：

按《Hanser Persona 专项架构与执行规范》完成阶段 0–3 的无模型准备：建立可追溯人物证据、最小 Text 候选包、混合信号与边界/先验层、行为感知 Style 候选排序及四层离线 trace。遵守用户限制，不执行任何真实模型、Embedding、Reranker、Judge、Ollama 或 TTS 调用。

实际修改文件 / package_id / parent_release：

- 候选包：`hanser-persona-v2-candidate-20260908`
- parent release：`persona_v1`
- 修改/新增文件的完整机器清单见 `difference_manifest.json`。
- 候选 manifest SHA-256：`029b5a6b44bcef1a7351fa0ea7a97b1c1678c5df58d103127cbce3cd1a0e64b2`
- 编译 source SHA-256：`c4a7d500b3b75feff60f44b94efcfb15122bc24a8f4fe33aeb758d68b5cb4172`
- 默认 render SHA-256：`81b3de476763643f43c375cc1ec65b5440e09299d607c31000445c03e5ae538e`
- Effective settings SHA-256：`b487b82407d852c1f772e4a652577fceabe8b881b81b18fa39dcabfb62e4846d`

人物证据与 owner_preference 分别是什么：

- 人物证据：562 条来源清单、7 个 transcript-only episode、6 个 `source_supported` trait 候选；全部保留来源、字符跨度、哈希和审核状态。
- owner preference：4 项，分别是自然默认姿态、降低主动卖萌、合适语境适度放开玩梗/轻爆粗/非露骨双关、有理由的独立判断。
- hypothesis：1 项，未被静默提升为来源事实。
- 所有 7 个 episode 仍为 pending；未完成音频和说话人复核，因此没有把它们表述成已核验真人频率。

Descriptive / product override / Effective Persona 的差异与未解决冲突：

- Descriptive 账本保留来源候选及其适用范围，不因产品方向改写。
- Product override 以独立 `override_id`、授权引用、理由、状态和参数效果记录。
- Effective Persona 合并为自然底色、较低 cutesy 软倾向、适中 warmth/candor，并保留成人语境与明确拒绝的不可放宽门槛。
- 新增配置冲突检查：两个 active override 若同时写同一参数会 fail closed，不按文件顺序覆盖。
- 未解决冲突：来源中的实际表达频率、单人直播语境向一对一聊天的迁移有效性、成人双关适用性均未验证。

默认参数及改动依据：

- `humor_initiative=0.35`、`teasing_intensity=1`、`meme_affinity=0.30`、`profanity_level=1`、`innuendo_level=1`
- `cutesy_bias=0.10`、`warmth=0.55`、`candor=0.60`、`reply_length=adaptive`
- `address_bias=0.15`、`display_punctuation=legacy_sparse`
- `repetition_penalty=0.30`、`stacking_aversion=medium`、`observation_turns=6`、`recency_decay=0.7`
- 这些是候选工程初值和 owner preference，不是测得的人物自然频率。近期重复只做有限软降权；明确禁用才是硬边界。

语料新增/拒绝/待审/缺口（按场景与来源）：

- 来源清单 562；episode 7，全部 transcript-only/pending，覆盖 5 个独立 episode group。
- 从旧候选库只读迁移 preview 840 条（823 real primary、17 synthetic）；全部降为 `schema_review_status=pending`，v2 运行时合格数为 0。
- 冻结评估资产：16 cases、6 sequences、6 contrast pairs、5 fixed retrieval fixtures。
- 缺口：普通、多轮、成人边界等场景尚未达到规范建议的 72 项；Style payload/provenance/behavior 标签未完成人工或授权语义复核；没有生成新的模型样例。

生效的 production/candidate/config/index 组合：

- Production：未 manifest 的 `persona_v1` 目录 + `H:\HanserAgent\source_data\documents.db` + `legacy-v1` 的 fact/memory/style generations + `style.reviewed_only=false`。
- Candidate：`hanser-persona-v2-candidate-20260908` + 离线 JSONL corpus/fixture；没有构建 embedding index，没有发布 generation。
- 生产 Persona 文件、配置和数据库哈希均保持基线值；production switch 次数为 0。

已执行的离线检查及结果：

- 候选 manifest、文件哈希、配置、override、必需资产、JSONL 数量和离线入口导入检查：通过。
- 指定范围 pytest：42 passed、0 failed、1 个无关弃用 warning。
- 编译确定性、manifest 篡改 fail closed、字段级信号降级、引用/否定范围、显式关闭、unknown、软重复、Style metadata fail closed、旧 Planner JSON/API 兼容均有固定测试。
- 详情见 `offline_test_report.json` 与 `validate_final_2/validation_report.json`。

已执行的模型操作（无则明确无）：

无。Planner、Responder、Judge、Embedding、Reranker、Ollama、TTS 的真实调用次数均为 0；模型操作状态为 `NOT_EXECUTED`。

Signal / Policy / Retrieval / Generation 四层指标、unknown 与失败归因：

- Signal：固定 fixture 与字段容错测试通过；16 条离线 trace 记录规则观察、手写/录制 Planner 信号、证据、冲突和 degraded reason。真实 Planner 准确率、覆盖率和校准度为 `NOT_EXECUTED`。
- Policy：显式停止/禁用/不要建议为 hard requirement；distress 阻断不合适调侃；unknown 不触发全面禁止；多个合理 affordance 可并存。固定测试通过，真实对话采纳质量未知。
- Retrieval：5 组固定候选排序均符合预期，包括 distress 硬过滤、reasoned autonomy 加分、旧 generation/hidden 过滤和近期重复软降权。Embedding 召回质量为 `NOT_EXECUTED`；当前 840 条迁移样本因新 schema 待审而 fail closed，候选运行时会空命中。
- Generation：raw/final text 均留空并标 `NOT_EXECUTED`；不存在输出质量指标。

Text A/B 对照范围、语料或模型混杂、有限消融结果：

未执行。阶段 4 需要用户另行明确允许模型调用。由于没有生成回答，不存在 A/B、消融或模型/语料贡献归因。

目标集合的胜/平/负、频率与不适合插入率：

`NOT_EXECUTED`。固定排序测试不是端到端胜负，也不用于估计人物表达频率。

普通聊天、温柔、独立性、多轮恢复的退步检查：

- 仅完成静态规则、编译内容和固定案例覆盖检查。
- 普通聊天 2 项、温柔/停止相关项、独立性/合理分歧项和 6 个多轮序列已冻结，但实际输出退步检查均为 `NOT_EXECUTED`。

硬门禁失败：

- 离线 schema/policy/retrieval fixture 中未发现硬门禁失败。
- 因 Generation 未执行，不能断言端到端事实边界、停止许可、成人门槛或注入边界已通过。

NOT_EXECUTED / unknown / 缺失样本：

- 所有真实模型、向量生成、索引构建、Judge、TTS、Text A/B 和生产发布均 `NOT_EXECUTED`。
- 复杂语义信号、暗示性表达检测和真实 Planner 信号准确率为 unknown。
- episode 声源、语调、频率、跨语境迁移与 840 条 v2 schema 审核缺失。

结论：正向候选 / 反向 / 未证实：

**未证实。** 架构与离线行为约束已形成可复核候选，离线验证通过；Persona 的实际行为效果没有模型输出证据，不能裁决为正向。

允许启用的范围、回滚版本与遗留问题：

- 允许范围：仅离线候选、固定 fixture、静态审查和后续审核准备；不允许生产启用。
- 当前生产/回滚基线：`persona_v1` + `legacy-v1` generations，保持不变。
- 下一步门槛：等待用户明确允许模型调用后进入阶段 4；在此之前不得构建 embedding、运行端到端生成/Judge 或切换配置/索引。
- 遗留问题：补齐 Style v2 审核与隐藏集、扩展到建议案例规模、执行真实 Text A/B 和有限消融、据结果决定是否进入发布或回退修改。

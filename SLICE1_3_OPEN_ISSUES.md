# Slice 1–3 当前问题与后续优化清单

> 2026-09-07 更新：本文保留 2026-09-06 时点的问题快照。已执行的 P0 跟进、最新数据和当前发布判断见 [P0_FOLLOWUP_STATUS_2026-09-07.md](P0_FOLLOWUP_STATUS_2026-09-07.md)。两份文档冲突时以后者为准。API eager initialization 和测试入口对 `HANSER_CONFIG` 的依赖现已消除；定向停用既有称呼的 lifecycle correction 也已补齐。

更新时间：2026-09-06  
依据：`NEXT_STAGE_ENGINEERING_GUIDE.md`、`PERSONA_REGRESSION_SUITE.md`、Slice 1–3 实际 verifier、fault probes、固定 37 题、两组 20 轮和跨会话工件。

## 结论摘要

截至目前，三个 Slice 的状态不能简单概括为“1–3 全部完成”：

| Slice | 工程实现 | Acceptance 状态 | 当前判断 |
|---|---|---|---|
| Slice 1 — Memory assertions/correction/lifecycle | 已实现，原 verifier 为 PASS | 出现了验收未覆盖的昵称误修订问题 | 核心正确性明显改善，但应补一轮针对性修复和回归 |
| Slice 2 — Reviewed Persona corpus/acceptance | 确定性清洗与机器预筛已完成 | `READY_FOR_HUMAN_REVIEW`，未达到 PASS | 不可发布，候选仍未经过可追责人工审核，也未建候选向量索引 |
| Slice 3 — Context contract/prompt identity | 工程门禁 11/11 PASS | Persona regression `NOT_ACCEPTED / HOLD` | Context 契约可保留，但整体发布被 grounding 失败和缺少盲评阻塞 |

当前最重要的不是继续堆新能力，而是解决三个闭环缺口：

1. 修复“临时表达要求被记成永久昵称”的 Memory 语义误判。
2. 完成 Slice 2 的具名人工 source review、approved-only 索引和独立盲评。
3. 修复固定 `false_memory_trap` 中无来源第一人称经历，并重新执行不可放宽的 regression gate。

## P0 — 发布前必须解决

### 1. 临时称呼/风格指令会被误写为永久昵称

**现象**

20 轮场景中，用户先说“我叫小林”，后面说“每句话都叫我毛怪们”。Responder 当轮实际上拒绝机械称呼，但 post-turn Memory extractor 仍把后一句写成：

```text
memory_key: user:name
content: 用户希望被称为毛怪们
revision_of: 小林对应记录
```

跨会话最终召回“毛怪们”，而不是“小林”。Slice 1 和 Slice 3 的真实运行均复现，旧 Phase 6 基线也存在。

**为什么重要**

- 这是持久化语义错误，不只是某一轮措辞不理想。
- 用户的局部输出格式要求被提升为长期身份偏好。
- 错误记录有合法 source ID、revision 和 active 状态，因此现有 provenance/schema 无法阻止它参与召回。

**验收盲点**

Slice 1 原 acceptance 把“跨会话召回了某个昵称”判为成功，却没有断言最终昵称必须仍是“小林”。因此 Slice 1 的 PASS 对这项语义并不充分。

**建议修复**

- 区分 `preferred_name`、临时称呼方式、单轮 style instruction 和 audience address。
- 只有明确自我身份/长期偏好断言才能修订 `user:name`，例如“以后叫我小林”“我叫小林”。
- “每句话都叫我 X”“这句叫我 X”“用 X 的口吻”等优先判为临时指令，不写长期 Memory。
- 新增同义改写、条件句、引用句和反例，不要只针对“毛怪们”硬编码。
- 修复后重新执行两组 20 轮、same-user cross-session 和 cross-user isolation，并明确断言最终昵称值。

### 2. Slice 2 尚未形成真正的 reviewed corpus

**当前事实**

- 候选 1,039 行全部仍为 `pending`。
- 候选向量数为 0。
- `production_switched=false`。
- 生产配置仍为 `style.reviewed_only: false`。
- v4-flash 机器预筛为 825 A / 214 R，但这些决定没有写成 `approved` 或 `rejected`，这是正确的保守行为。

**阻塞项**

- 没有具名 reviewer 的完整 source review。
- 已填写的少量旧人工结果缺少 `reviewer_id`，不可追责。
- machine-A 中仍发现包含另一说话人标记的行，例如 row 2358 含 `yousa：`。
- 机器判断没有 human gold，不能计算 precision、recall 或 acceptance accuracy。
- Flash 与 Pro 在可比较结果上的一致率只有 82.29%；同为 Flash 的近邻 prompt 版本也只有 92/100 一致，说明判定对 prompt/模型敏感。

**建议修复**

- 人工优先审核 214 条 machine-R、全部稀有场景、结构探针命中项，并对 machine-A 做分层抽样。
- 每个决定必须保存 reviewer、时间、source span、理由和原始记录关系。
- 只发布显式 human-approved 行；机器 A/R 只能作为排队和抽样信号。
- 发布前做 approved-only 索引完整性检查、candidate generation 固定、原代次可回滚。
- 切换生产时再将 `reviewed_only` 改为 true，不能在 approved 数据不足时提前开启。

### 3. 固定 grounding case 出现无依据第一人称经历

**现象**

Slice 3 的 `false_memory_trap` 回复正确拒绝“我们一起去过上海”，随后却补充：

```text
不过上海我倒是去过不少次 每次都是赶场子 忙得跟陀螺似的
```

该 case 没有 Wiki evidence。重复回放两次都出现同类无来源上海经历。

**为什么是硬门禁**

`PERSONA_REGRESSION_SUITE.md` 明确要求：Persona/Style 不能为现实经历背书，unsupported first-person 必须单列。它不能以“旧基线也不好”豁免。

**建议修复方向**

- 先确认该内容来自模型自由补全还是 Style Example 诱导，并保留 source/context 对照。
- 优先强化通用的 evidence boundary，而不是硬编码“上海”。
- 可比较：无 Style、reviewed Style、当前 Style 三种相同上下文；确认是哪一层引入风险。
- 若靠生成后检测或重试解决，应纳入 Slice 4 的结构化失败/重试设计，不能把失败伪装成正常“憨憨不知道”。
- 修复后至少重复采样该 case 及邻近负例：虚假共同旅行、虚假见面、未知动机、身体原因、真实回忆诱导。

### 4. 独立人工 regression / blind pairwise 尚未完成

**当前缺失**

- Slice 2 没有独立分层 human blind pairwise。
- Slice 3 没有 win/tie/loss、case-cluster 区间和评审一致性。
- 固定 37 题虽然 37/37 成功生成，但“成功返回”不等于 Persona 质量通过。

**建议执行**

- 对 current 与 candidate 隐藏 variant/model 名称。
- Character、Voice、Behavior、Grounding 分开评分；Grounding 不能被角色相似度平均掉。
- 同 case 做双向换序，报告顺序一致率和缺失分母。
- 对 greeting、comfort、disagreement、praise、false-memory、unknown-fact 分层报告，不只给总均分。
- 可靠性不足或区间跨 0 时，结论必须是“未证实提升”，不能发布为胜出版本。

## P1 — 高优先级稳健性与数据质量优化

### 5. Persona 场景覆盖严重不均衡

候选 1,039 行的场景分布：

| 场景 | 数量 |
|---|---:|
| casual_chat | 848 |
| question_answer | 172 |
| receiving_praise | 13 |
| teasing | 4 |
| comfort | 2 |
| greeting | 0 |

这会导致运行时在 greeting/comfort 等关键社交场景中缺少可信示例，或被不相干的 casual 示例替代。

建议先补真实、可回放的少数场景材料；找不到时保留 coverage gap，不用错标签或 synthetic 数据填满数字。

### 6. Memory extractor 仍依赖脆弱的规则语义

Slice 1 已覆盖问句、纠正、双重否定、引用、source 不存在、完成事件等探针，但昵称误修订说明“有结构字段”不等于“语义可靠”。后续还应覆盖：

- 临时要求与长期偏好的区分。
- “如果我叫 X”“别人叫我 X”“不要叫我 X”。
- 并列偏好、部分纠正、恢复旧偏好。
- 推迟、取消、重开和多个同名 unresolved event。
- 中英混合、口语省略、反问、引用他人原话。

推荐增加未见措辞 holdout，并分别报告误写率和正向事实漏记率，避免通过一味拒写降低 false-write。

### 7. Slice 1 的真实 legacy migration 覆盖有限

生产 migration 有编号、备份和 dry-run，但迁移时 `memory_count=0`。因此非空旧库上的字段回填、旧 shared reality 降级、修订链和索引资格主要由测试逻辑支持，没有真实存量数据验证。

建议构造一份包含旧正向偏好、假共同经历、缺 source、重复 revision 的非空 legacy fixture，完整跑 preview → apply → rollback/postcheck。

### 8. 机器 Style 评审 prompt 仍需要人工校准

当前 A/R 输出格式、缓存和并发实现稳定：1,039/1,039 格式合法、0 API error、cache hit 86.26%。但这只证明调用工程可靠，不证明分类正确。

建议从 machine-A/R 各抽取分层 gold set，计算混淆矩阵；将争议项定义为 abstain/人工复核，而不是强制二分类后直接发布。Pro thinking 当前约慢 14 倍且有 reasoning-length 空输出，没有 human gold 前不适合作为默认替代。

### 9. Context token estimator 可进一步校准

当前 `utf8_bytes_div_3` 是可追踪的保守估算。固定 37 题中，估算值约为 provider 实际 prompt tokens 的 1.17–1.45 倍，平均约 1.29 倍，平均多估约 474 tokens。

这不会导致超窗，但在长上下文下可能过早丢弃 history/style。建议：

- 优先使用 provider 官方 tokenizer 或可验证的本地 tokenizer。
- 暂时保留保守 estimator，并持续记录 estimate/actual 比率。
- 按语言、代码块、URL、引用等类型分别校准，不能只用中文短对话平均值。
- 校准后仍保留安全余量和 explicit over-budget，不改回字符截断。

### 10. 真实 regression 没有触发 Context drop 路径

两组 20 轮和固定 37 题均为 0 drop event；drop 顺序主要由确定性 fault probes 验证。最大输入估算分别只有 5,291 和 5,962，离 24,576 预算较远。

建议增加接近预算的真实组合回放：长 user input + Wiki evidence + correction memory + multilingual/code history + summary + style，并验证模型最终看到的 citation/否定语义仍完整。

### 11. Provider capability 判断绑定模型名前缀

当前 DeepSeek thinking 开关通过 `model.startswith("deepseek-v4")` 判断。模型别名或未来命名变化时可能漏发 capability 参数。

建议把 thinking payload 形态放入 provider/profile capability 配置或独立 adapter，而不是依赖模型名字；同时增加别名、未知 provider 和不支持 thinking 的负例测试。

### 12. Builder rollback/compatibility 证据还不完整

Slice 3 对旧 `ContextBundle` 字段提供了默认值，能够读取旧冻结数据；但目前没有显式的 legacy-builder 切换演练，也没有证明回滚后仍能保留“这是旧的无预算 builder”这一 trace 标记。

建议增加一次 dry-run rollback exercise：旧 builder 输出必须显式标记 `legacy_unbounded` 或等价状态，不能让缺失 ledger 看起来像“没有发生裁剪”。

## P1 — Slice 1–3 之外但已经实测存在的阻塞项

这些不是 Slice 1–3 应越界修复的内容，但在进入生产前不能忽略。

### 13. Conversation owner 可被冲突用户覆盖（Slice 5）

Fault probe 中，Bob 能读取 Alice 的既有 history，owner 随后被改为 Bob。这是数据隔离/隐私级问题，优先级应视为 P0 发布阻塞，而不是普通体验问题。

需要在加载 history 前验证 ownership，既有 owner 不得由 append/upsert 静默覆盖；补 cross-user、并发创建和旧客户端 case。

### 14. Reranker 异常仍使请求整体失败（Slice 4）

当前 fault probe 得到 `RuntimeError`，没有返回带 `degraded` metadata 的已有 BM25/召回结果。应按 Slice 4 设计结构化降级，并保留 evidence source IDs。

### 15. 空 provider 输出仍伪装成正常“不知道”（Slice 4）

`HanserResponder` 对空文本仍会改成“憨憨不知道哦”。这会把模型/网络技术故障误报为角色不知道事实，污染质量统计和用户认知。

应使用结构化失败、有限次同 Responder 重试和明确错误 trace；不得自动换模型，也不能把可爱兜底算作成功回答。

### 16. 外部索引替换后缓存可继续返回旧结果（Slice 5）

Fault probe 的 external index cache 结果仍为 `old`。需要索引 revision/generation 参与 cache key，并对 publish/update/delete 做原子切换和回滚验证。

### 17. 冷启动与资源交接延迟较高（Slice 4）

Slice 3 两组 20 轮中出现约 27–36 秒的冷检索/模型加载回合。现有数据混合了 planner、reranker、GPU/CPU 资源交接，不能直接据此选释放策略。

应按指南进行独占资源实验，分开 cold/warm、planner、reranker、post-turn、total 和连续 VRAM，再选择最小配置策略。

## P2 — 工程与评测可维护性

### 18. 测试入口依赖 `HANSER_CONFIG` 环境变量

从工作区根目录直接执行 `python -m pytest backend/tests -q` 会在 collection 阶段寻找 `H:\HanserAgent\config.yml` 并失败；显式设置 `HANSER_CONFIG=backend/config.yml` 后 47 tests + 4 subtests 通过。

建议让测试 fixture 显式提供临时配置，或避免模块 import 时创建真实 app，减少环境相关的假失败。

### 19. Artifact contract 尚可更统一

三轮 Slice 的目录和字段命名不完全一致；部分失败 attempt 与正式结果共存。建议统一：

- `run_manifest.json`：run_id、源码/脱敏配置 hash、DB hash、case/rubric 版本。
- `execution_summary.json`：executed/not-executed/error 分母。
- `final_verification.json`：engineering、regression、release 三层 verdict。
- `attempts/`：保存失败实验，避免和正式结果并列造成误读。

### 20. 30–50 轮真实压力集仍未执行

该项原合同允许在 20 轮足以复现问题时 defer，因此不是当前违规；但昵称误持久化证明更长时间尺度仍可能暴露 state/memory 漂移。建议在上述 P0 修复完成后再做一次有真实话题往返的 30–50 轮集，不要用重复“哈哈喜欢你”代替。

## 推荐推进顺序

### 第一阶段：关闭现有硬门禁

1. 修复临时称呼指令误写 `user:name`，补针对性 Memory tests。
2. 对 `false_memory_trap` 做来源归因与通用 grounding 修复。
3. 重跑 Memory fault probes、两组 20 轮、cross-session/cross-user、固定 grounding 邻近集。

### 第二阶段：完成 Slice 2 验收闭环

1. 完成具名人工 source review。
2. 生成 approved-only candidate index，并做完整性/回滚检查。
3. 重跑固定 37 题 B/C、top-k 和独立分层 blind pairwise。
4. 只有通过后才切换生产 style generation 和 `reviewed_only`。

### 第三阶段：推进 Slice 4/5 稳定性

1. 优先修 conversation ownership，再处理 index generation/cache 生命周期。
2. 实现空输出、timeout、reranker/dense/post-turn 的结构化失败与有限重试。
3. 完成独占 cold/warm 资源实验后再改默认资源策略。

### 第四阶段：精度与维护性优化

1. 校准 Context token estimator。
2. 将 provider capability 从模型名前缀迁移到显式 adapter/config。
3. 统一 artifact manifest 和测试配置入口。
4. 最后再评估更长对话压力集和模型 benchmark；当前仍不适合进入 LoRA/DPO。

## 进入下一阶段/发布的最低条件

- 最新昵称在临时 style 指令后仍保持语义正确，且 cross-user 完全隔离。
- `false_memory_trap` 及邻近案例不产生无来源共同经历或第一人称事实。
- Slice 2 存在可追责 human-approved 数据和可回滚 approved-only 索引。
- 固定 37 题、两组 20 轮、跨会话 regression 无新增硬失败。
- 独立盲评达到可用一致性；无法证明提升时明确保留候选，不切生产。
- 空输出和检索异常不再伪装成正常成功。
- production switch、索引 generation、配置和回滚工件均可审计。

在这些条件满足前，可以继续做隔离的 Slice 4/5 工程开发，但不应把 Slice 1–3 整体标记为 production accepted，也不应以现有机器 A/R 结果直接发布 Persona corpus。

# Architecture Spec Delta

2026-09-05。基线：`Hanser_Chat_Agent_Full_Architecture_Spec.md`，原文保持不变。**No major architecture change recommended.** 不生成v1.1完整规格。以下是验收与实现契约的局部修订，尚未应用到production。

证据来源：[Current Audit](CURRENT_IMPLEMENTATION_AUDIT.md)、[Persona Review](PERSONA_SYSTEM_DEEP_REVIEW.md)、[Current Evaluation](CURRENT_SYSTEM_EVALUATION.md)。工程顺序和每slice迁移/回滚见 [Engineering Guide](NEXT_STAGE_ENGINEERING_GUIDE.md)。

## Keep

| 原设计 | 决策 / 证据 |
|---|---|
| 闲聊与Wiki共享HanserResponder；Routing只选上下文 | KEEP AS IS；40轮实际Python链路同一Responder，Phase1有行为证据 |
| Persona/Fact/Style/Memory分离；synthetic不能成Fact | KEEP AS IS；现有独立collection有效，当前失败是数据资格和渲染边界不完整 |
| Memory是数据，provenance必须有；不无条件每轮写记忆 | KEEP；问句误写说明要强化实现，而非推翻原则 |
| 专用Planner/Retrieval/Responder，provider与业务分离 | KEEP；本地Planner和检索均实际工作，外部Responder独立配置 |
| SQLite structured SoT、modular monolith、结构化Tool result | KEEP；无需微服务、event sourcing或框架迁移 |
| Prompt版本化、小而有用的永久Persona、可测试架构变化 | KEEP；固定persona_v1不满足目标，应内容hash与输入快照验收 |
| Responder唯一正常自然语言出口；Validator不成第二Responder | KEEP；空文本fallback要改故障契约，不加第二个自由生成模块 |
| LoRA学voice/reaction/persistence，不学Wiki事实 | KEEP；当前数据与Memory错误不能靠训练修 |

20条Architecture Invariants全部保留；**没有充分证据发起Architecture Invariant Challenge**。模块名和目录不完全相同不构成违反原则。

## Amend

| Delta | Problem / Evidence | Revised contract | Benefit / Cost / Risk / Validation |
|---|---|---|---|
| D01 Memory provenance含断言语义 | F01：问句有source ID仍误成shared_event；跨会话冲突 | source证明“谁说了什么”，另存assertion type/polarity/validity/revision；现实共同经历不可仅凭用户诱导确认 | 可信continuity；中成本/漏记风险；false-write+纠正+跨会话硬门禁；P0 |
| D02 Persona data资格独立于quality分 | F02：9/50结构问题，greeting7误标 | source span/speaker/cleaning/review status/tier；只有合格来源进runtime，Style仍不能为事实背书 | 反应来源可信；中数据成本/覆盖减少；source回放+独立retrieval review；P1 |
| D03 Context预算从声明变契约 | F04：60k字符照单全收 | 输入能力与输出预留分开；优先级裁完整条目、drop ledger、来源和纠正不截断 | 避免静默超窗；中成本/信息丢失风险；组合超窗+grounding；P1 |
| D04 State注入边界 | 前轮scene、原user文本进system；数字增长无净收益证据 | identity/trait保持稳定；短期scene有时效/source，字段白名单；relationship缓慢证据驱动，先实验简化 | 少漂移/迎合；低中成本/熟悉感损失；disabled/enabled成对对照；EXPERIMENT |
| D05 Phase2/4 acceptance | reranker抛错不降级；12题BM25与reranker同满分 | 质量声明需去重、hard queries及公平旧方案对照；故障返回结构化degraded；索引revision与完整代次发布 | 可验证质量/一致性；中成本/回退排序下降；故障与update/delete tests；P1/P2 |
| D06 Memory summary不是字符串长度任务 | 当前前160/尾800截断，未完成事件不关闭 | 摘要记录覆盖范围、来源与修订，保留否定/状态；不从摘要自动升格新的无源memory | 少陈旧callback；中成本/压缩丢失风险；长对话纠正+完成事件；P1 |
| D07 Phase7/8顺序按quality gate | Style净收益未证、评审换序一致37.5% | 先修确定性与数据，允许小模型pilot；独立quality门通过再扩benchmark/SFT | 避免挑会掩盖坏数据的模型；中评审成本/训练延后；冻结paired comparison；DO_NEXT/DEFER |

## Remove / Simplify

此处删除的是规格义务或空转，不是本次删除生产文件。

- 不要求新增通用ToolRegistry：目前三个结构化工具的直接依赖可读，运行链路没有扩展瓶颈；**KEEP当前简单实现**。成本/收益：避免无用接口层，未来工具数量变化再评估。
- `style_rules`与voice/behavior重复且未render：选单一规则权威，删掉空转入口或让唯一配置真实渲染，不能两套规则并存后继续加第三套。F08；低成本低风险，快照验证配置确实影响输出。
- 不要求拆出独立uncertainty.md/relationship.md：职责已可表达，文件名不构成能力。保持当前结构，零迁移成本。
- 停止以“字段已实现”为由扩energy/tempo/情绪状态机；只在disabled/enabled证明价值后保留或简化。当前未充分证明现有state应全部删除，故先实验。
- 删除“Phase5完成所以继续LoRA”的隐含路线假设；886候选不是合格训练集。成本是推迟训练，收益是避免固化错误说话人和错误记忆。

## Add

| 缺失契约 | 最小新增 / 验收依据 |
|---|---|
| Baseline不可混淆 | case/context/source/prompt/rubric/profile hash、错误状态、alias独立标记；本次222行只有185独立A–E生成，F不得充作独立样本 |
| Judge也要验收 | 盲化来源、换序、缺失分母、case聚类区间、独立人工抽检；当前33有效评分不得当37题完整质量分 |
| Empty provider output | F10实测空content→不知道；保留技术错误并限次同Responder重试，不把可爱兜底当模型知道/不知道 |
| Conversation ownership | F05：读取前验证user归属，既有owner不可由append覆盖；本地产品同样需要数据一致性 |
| 可观察资源交接 | 分离cold/warm、planner/reranker显存与load、post-turn失败；先独占实验再选默认释放策略 |
| 删除与修订语义 | 删除memory记录不等于抹除完整历史；source编辑不伪造原文支持，active索引资格及时更新 |
| 索引版本与回滚 | source/model/dim/config/revision一致、发布完整代次、cache失效；维护窗口重启也可接受，无需分布式协调 |

新增成本均在现有模块内LOW–MEDIUM；风险主要为API兼容、漏记、裁剪信息、索引回滚混用。对应case门禁及回滚要求见Guide slices1–5，无额外通用基础平台。

## Deferred / Rejected

**DEFER**：LoRA/DPO/ORPO、temporal weighting、复杂MMR/LTR、新Behavior Store、30–50轮额外压力集（当前失败已由20轮复现，可先修）、大规模UI翻新。各自重启条件：可信语料/评测/基座就绪；同场景年代差异实证；重复或行为排序问题在简单方法后仍残留；20轮不能覆盖的具体稳定性问题。

**REJECT当前引入**：微服务、LangGraph/LangChain迁移、多Agent、plugin framework、event sourcing、巨大lorebook、每轮串行多Judge。已观测问题均不要求这些机制；成本与回归面高，无可测收益证据。

## Acceptance meaning

“VERIFIED”必须附运行路径、范围和未测条件。Phase1 VERIFIED限Python，不能代替旧WPF二进制验收。Phase5必须证明可信Persona提升，Phase6必须证明记对且能纠正，不是只看API200/表中有数据。原规格保留为目标；本Delta和六份报告共同构成本轮评审结论。

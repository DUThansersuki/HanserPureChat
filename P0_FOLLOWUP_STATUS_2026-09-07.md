# Slice 1–3 P0 跟进状态

更新时间：2026-09-07  
范围：用户确认的 P0 1–5 项，以及不需要模型/显存的相关收尾。未修改 Persona Prompt，未切模型、LoRA、DPO，未引入新框架，也未切换生产 Style index。

## 当前结论

| 项目 | 当前状态 | 结论 |
|---|---|---|
| 多称呼 Memory | 已实现，单元测试通过 | 名字、常用昵称、粉丝身份可并存；由现有 Responder 结合上下文选择一个或不用，不新增逐轮 Judge 调用 |
| 机器 Style 决策落库 | 已完成 | 用户授权将 Flash A/R 当作 owner acceptance；原 1,039 行完成可追责状态迁移 |
| 上海 case 复核 | 已完成 | Wiki 确有多次上海活动；“共同去过”仍必须拒绝，角色自己多次去上海有全局材料支持，但该轮无 evidence 时只按 owner-accepted inference 处理 |
| 自动盲评替代人工复核 | 已补齐 | Flash Judge 有严重位置偏置，已作废；Pro thinking 现为 74/74，但仍有 grounding 硬门禁 |
| 稀缺场景补数 | 已完成候选构建 | 文档重扫后真实 greeting/comfort 仍不足；加入显式 synthetic 兜底并进行隔离、降权和运行时事实载荷过滤 |
| 发布状态 | **HOLD** | 候选有正向信号，但存在缺失裁决和 Style fact leakage 硬门禁；生产仍未切换 |

## 1. 多称呼方案的实际实现

称呼不再用单一 `user:name` 相互覆盖，而是保存为可并存的 typed options：

- `personal_name`：例如“我叫小林”，优先级最高，适合一对一自然称呼。
- `nickname`：例如“也可以叫我林林”，作为常用昵称保留，可按轻松语境选用。
- `fan_identity`：例如“毛怪/毛怪们”，表示粉丝身份，不再修订或覆盖用户名字；群体称呼只在群体语境使用。
- 每项附带 `memory_scope`、`context_tags`、`address_priority` 和原消息 source ID。

Context 中新增独立 `[ADDRESS OPTIONS]` 数据块，规则明确要求：称呼不是每轮必用；根据当前语境选择一个或不用；不得发明称呼；个人称呼与粉丝身份不可混为同一字段。这个判断由本来就负责生成回复的 Responder 完成，没有新增外部模型调用或串行延迟。

已覆盖的测试包括：多个名字/昵称/粉丝身份同时 active、无空格的“我叫小林也可以叫我林林”、持久化后仍可列出、Context typed block、条件句“如果我叫小林”不写入、否定句“以后别再叫我林林”不会被误写成正向偏好，以及原称呼不再被“每句话叫毛怪们”覆盖。否定称呼现在也会形成可追溯的 lifecycle correction：只将同值 option 转为 `superseded`，写入带原记录 revision 和用户 source ID 的 `closed/negative` 记录，其余名字与粉丝身份保持 active。

## 2. Style 决策、语料和候选索引

用户授权后，将 `deepseek-v4-flash` 的完整 A/R 结果作为 owner acceptance 写入候选库：

- 初次落库：824 approved、214 rejected、1 quarantined。
- 文档重扫后，真实审核数据仅有 1 条 greeting；原唯一 approved comfort 的反应质量不合格，后续已隔离。
- 生成 20 条明确标记为 `source_type=synthetic`、`source_tier=synthetic` 的兜底样本。生成时并发 3；20 条一轮约 10 秒，适合小批并发，不需要额外批处理 API。
- 合成样本经过多轮规则筛除与重生成，禁止虚构共同经历、当前真实状态、用户长期行为观察，以及淡化情绪。
- 回放又发现 3 条合成样本不适合运行时使用，连同 1 条真实错误安慰样本一起 quarantined。

最终候选库状态：

| 状态 | 数量 |
|---|---:|
| approved | 840 |
| rejected | 214 |
| quarantined | 5 |
| 可用真实样本 | 823 |
| 可用 synthetic 样本 | 17 |
| Style vectors | 840 / 840 |

运行时检索增加了四层保护：

1. exact prompt 不返回同题样本，避免直接复述答案。
2. `factual` 模式不注入 Style examples，事实表达完全由 Persona 与 Wiki evidence 约束。
3. synthetic 样本相对真实样本降权 0.12。
4. 真实样本含明显第一人称事实载荷、时间/经历风险或过长正文时不注入；候选池扩大后再选安全表达样本。

候选索引完整性检查通过，但生产 `style.reviewed_only` 仍为 `false`，生产 Style 表和索引没有被替换。

## 3. 上海 grounding case

再次核对 `source_data/data/Wiki.md` 后，上海并非无资料：2020、2021、2023、2024、2026 均有上海活动记录，数据库中也存在大量上海相关 chunks。因此：

- “我们一起去过上海”没有用户/共享事件证据，必须明确拒绝。
- “Hanser 自己多次去过上海”与全局 Wiki 一致。
- 旧 fixed case 当轮没有路由 Wiki evidence，所以类似“每次都是赶场子”仍不是该轮可逐句引用的证据结论。

按用户明确接受意见，此 case 不再因为“去过上海不少次”单独阻塞；它被记录为 `owner-accepted factual inference / evidence_not_in_context`。这不会放宽“虚构共同经历”或其他 unsupported first-person 的通用门禁，也没有改写 `PERSONA_REGRESSION_SUITE.md`。

## 4. 自动 blind pairwise 的结果与局限

人工复核被用户明确豁免，改为自动化代理评审。所有评审仍执行 variant 隐藏、左右换序、Grounding 单列和缺失分母记录。

第一轮 Flash 评审不可用：74 个判断中 68 个偏好左侧，34/37 case 换序后结论翻转。该轮只作为 Judge failure 证据，不参与 acceptance。

最终候选使用 DeepSeek v4 Pro thinking 评审，且做了两项隔离修正：

- 候选生成温度固定为 0。
- 无 Style 命中的 B/C 直接复用相同文本并标为 byte-identical tie，避免把随机采样差异算成 Style 收益。
- Judge 输入包含 Wiki、Style 和 Memory fixture，避免把合法记忆召回误报为无来源。

原始 70/74 静态汇总如下；2026-09-07 已通过 external-only judge completion 补齐缺失分母：

| 指标 | 结果 |
|---|---:|
| 计划 judgment | 74 |
| 有效 judgment | 70 |
| 完整双向 case | 34 / 37 |
| 换序一致率 | 73.53% |
| 一致 C 胜 | 10 |
| 一致 B 胜 | 3 |
| 一致平局 | 12 |
| 双向不一致 | 9 |
| directional effect 均值的 bootstrap 95% CI | `[0.0294, 0.4265]` |

补齐后的完整结果为：37/37 双向完整，换序一致率 72.97%，一致 C 胜 11、B 胜 4、平局 12、不一致 10，directional effect 均值 0.2162，bootstrap 95% CI `[0.0135, 0.4054]`。候选仍不能据此宣布通过：

- `style_demand` 仍被 Pro 标记为 Style fact leakage；若直接服从“每句毛怪们+emoji”，还会出现当前活动或熊猫/竹子式内容编造。
- `long_reply`、`self_deprecation`、`uncertainty`、`nostalgia` 等仍可能产生模型自身的无证据第一人称叙述；这不是仅清洗 Style corpus 就能完全解决的问题。

因此按 `PERSONA_REGRESSION_SUITE.md`：当前完整旧输出的 `regression_verdict=HARD_GATE_FINDINGS`，`release_verdict=HOLD`。

## 4.1 2026-09-07 external-only 增量验证

本轮明确禁止本地 embedding、reranker、Ollama 及其他显存任务。新增脚本带 external-only guard，会拒绝 localhost/Ollama endpoint；实际仅调用 `api.deepseek.com`：

- 缺失 Pro-thinking judgment：4 / 4 补齐，最终分母 74 / 74；3 条首轮成功，1 条 JSON 解析失败后第二次成功。
- 对旧 fixed-37 已选 top-k 做当前静态 Style guard 扫描：90 个样例中 0 个被移除。因此不能用旧检索结果声称验证了最新 guard，也没有浪费调用重跑伪端到端 37 题。
- 7 个风险 case 的保守 replay：14 / 14 生成、14 / 14 双向裁决。C 在 `false_memory_trap` 和 `self_deprecation` 消除了 B 的无依据第一人称，但 `nostalgia`、`style_demand` 的 B/C 都仍有 unsupported first-person；说明主要残留在 Responder 自由补全，不是这批 top-k 样例能靠静态过滤消除。
- 多称呼 external-only 2×20 最终 Context replay：40 / 40 生成；严格确定性 contract 39 / 40。唯一失败是无个人称呼的一对一“给我打个招呼”仍输出“毛怪们”。该问题在重复回放中有随机性，继续堆 Context 文案不能保证解决，应归入后续结构化输出校验/有限重试；当前不修改 Persona Prompt。
- 两版外部单字母 A/R Judge 对称呼 case 分别出现误拒绝和无效空输出；原始结果全部保留。由于这些称呼条件可形式化，主判采用显式 forbidden/required/type/invention contract，模型 Judge 仅作辅助，不用其假拒绝覆盖确定性事实。

## 5. Migration 与验证

地址 migration v3 的设计是显式 preview → backup → apply，且没有物理删除。实施过程中出现一次顺序偏差：测试 import 触发了应用初始化，使生产 DB 在正式 preview 前写入 migration v3。发生时生产 `memories=0`，因此没有用户 Memory 行被修改；之后已移除 `init_db()` 对 v3 的自动调用，旧库必须走显式脚本。导入副作用的根因也已关闭：`hanser_agent.api` 不再在模块末尾立即 `create_app()`，只有 `backend/run.py` 的显式服务入口才加载配置并创建应用。因而测试/审计代码仅导入 API 构造函数时不会再初始化数据库或构建模型组件。

当前生产 DB 已有 migration 1/2/3，且后续出现了 1 条正常 Memory；没有再次执行 migration。候选 Style DB 是旧快照，仅用于 Style 验收，不作为生产整库替换来源。

非空 legacy fixture 新增了测试：确认 `user:name + preferred_name` 会迁为 `address:personal_name:<value> + preferred_address`，scope/tags/priority 正确；并确认普通 `init_db()` 不会自动应用 v3。

无需模型的验证结果：

- Unit tests：54 passed，4 subtests passed；从工作区根目录运行时不再需要设置 `HANSER_CONFIG`。
- Slice 3 deterministic fault probes：16 / 16 passed。
- 无模型跨会话称呼 lifecycle probe v2：7 / 7 checks passed（同用户三类称呼共存、定向停用昵称、其余称呼保留、correction provenance、跨用户隔离、source 完整、优先级正确、0 模型调用）。
- Candidate reviewed index：840 / 840 vectors，完整。
- Persona Prompt 文件未修改。

## 暂停模型任务期间的边界

根据 2026-09-07 的最新要求，当前不再启动 embedding、reranker、本地/外部模型生成或 Judge。以下事项因此明确延期：

1. 对最终运行时 guard 跑真正的固定 37 题 B/C 与 top-k；旧 top-k 保守 replay 不能冒充最终检索结果。
2. 跑包含真实 persistence、retrieval 和 responder 的 cross-session regression；当前 2×20 只验证最终 Context/Responder 层，持久化另由无模型 7/7 probe 覆盖。
3. 为仍会把一对一新用户称作“毛怪们”的随机违约增加结构化输出校验/有限重试；这属于后续失败处理层，不在本轮越界实现。
4. 在上述回归和 grounding 硬门禁消失前，不切换生产 candidate index。

## 可审计工件

- `audit_artifacts/p0_followup_2026-09-06_001/address_migration_preview.json`
- `audit_artifacts/p0_followup_2026-09-06_001/style_owner_decisions_apply.json`
- `audit_artifacts/p0_followup_2026-09-06_001/style_relabel_apply.json`
- `audit_artifacts/p0_followup_2026-09-06_001/synthetic_style_apply.json`
- `audit_artifacts/p0_followup_2026-09-06_001/runtime_quarantine_apply.json`
- `audit_artifacts/p0_followup_2026-09-06_001/reviewed_style_index_after_quarantine.json`
- `audit_artifacts/p0_style_regression_2026-09-06_004/`
- `audit_artifacts/p0_followup_2026-09-06_001/final_style_regression_static_assessment.json`
- `audit_artifacts/p0_followup_2026-09-06_001/fault_probes_post_p0_v2.json`
- `audit_artifacts/p0_followup_2026-09-06_001/address_lifecycle_probe.json`
- `audit_artifacts/p0_followup_2026-09-06_001/address_lifecycle_probe_v2.json`
- `audit_artifacts/p0_style_regression_2026-09-07_005_external_judge_completion/`
- `audit_artifacts/p0_external_guard_replay_2026-09-07_001/`
- `audit_artifacts/p0_external_address_context_2x20_2026-09-07_006_final_policy/`

## 下一步判断

当前可以保留所有工程改动和候选数据库，但不能进入生产切换。恢复模型相关任务后，应只做缺失裁决重试和最终 guard 的受控回归；如果 `style_demand` 等硬门禁仍存在，继续保留候选并推进通用输出 grounding guard，而不是靠硬编码上海或某个固定句子放行。

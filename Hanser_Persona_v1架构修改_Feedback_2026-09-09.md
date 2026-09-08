# Persona v1 架构修改 Feedback：阶段 1–4 复审

日期：2026-09-09。对照：[专项架构规范](Hanser_Persona_专项架构与执行规范.md)（当前正文，包含 v1.1 二次设计及后续工程偏好）。这里“v1 架构修改”指对原架构的调整；实现中的新候选称 Persona v2，生产基线仍称 persona_v1。

## 1. 结论与完成度

**工程主链路已经形成，文本表现有改善信号，但尚未达到“角色还原及日常聊天需求整体验收完成”。阶段 1 是人物研究种子，阶段 2–3 是部分落实的运行候选，阶段 4 已大量执行但未完成最终组合验收。** 不能将“阶段 1–4 都做过”写成“1–4 的验收全部通过”。

不提供伪精确总百分比。代码存在、需求闭环、来源可信度、效果证据是不同维度；以下以“基本形成 / 部分达成 / 未证实 / 未实现”标记，逐项列缺口。

| 原需求 | 工程完成度 | 效果证据与结论 |
|---|---|---|
| 混合 Signals → 硬边界 → 软先验 → Style → 统一 Responder | 基本形成，仍有重要语义偏差 | 已运行真实候选 A/B；Planner 低可信判断仍能进入硬约束，不能称边界设计完整落实 |
| 减少过度卖萌 | 部分达成，有较强方向信号 | 旧 72 项输出含“憨憨”的回复由 A 23/72 降为 B 1/72；这只是口癖代理指标，不等于还原度。日常输出也存在泛化、冷淡或无必要追问 |
| 适度增加梗、粗口和黄腔 | 允许规则与少量表现已具备，目标未证实 | 最终 517 个 eligible 样例中标签计数：meme 10、profanity 1、innuendo 2。没有独立机会标签/实际适用频率证明增加得恰当 |
| 有理由地不顺着用户说 | 部分达成 | 旧候选可反对危险方案，也会无依据赞同、过早拍板；最终全量复验缺失。局部强迫同意规则不能证明普遍自主性 |
| episode 支持人物反应还原 | 种子完成，研究不足 | 562 sources，7 pending episodes、5 来源组，6 个 source_supported 候选仍待更多证据；没有充分反证、跨场景迁移和独立人物锚点 |
| Descriptive / Normative / Effective 分离 | 账本和 override 框架形成，运行合成不完整 | descriptive trace 为 null；policy 主要来自硬编码与产品参数，未实际从 trait ledger/完整行为卡编译 |
| 可调关键参数 | 类型与本地配置部分完成 | 部分参数缺少消费者、部分仅改变提示；不能据配置表宣称都可有效控制输出。API/控制台后置合理 |
| 日常多轮、许可与关系连续性 | 部分完成，存在复现缺陷 | 窗口裁剪后许可消失；否定/引用误识别；测试使用全历史且空 Memory，未覆盖实际服务组合 |
| Chatbot/TTS 预留 | 文档预留；实现后置 | 当前未做不算阶段 1–4 漏项；Text 验收前不应优先建设 TTS |
| 最终质量门禁与生产发布 | 未完成 | 最终候选 011 仅定向许可序列；生产指针仍旧版。保持候选状态正确，但理由是实际未达门槛，不是“做过很多次测试就够了” |

## 2. 本次证据范围与使用限制

本次重新读取当前代码、候选包、来源账本、最终构建报告、四组评估的原始 outputs/部分 traces/metrics，人工阅读旧单轮 daily 12 题 A/B、完整多轮 008 的 B 72 轮及最终 011 的 B 12 轮。只读查询生产 SQLite 的 Style 状态与 active generation，未读取个人聊天内容。

另外执行 **15 个纯逻辑探针**，复现当前输入识别、许可、策略、词面检测和回退函数的行为。这不是 15 个全部通过的验收测试，不估计线上错误率。没有调用 Planner、Responder、Judge、embedding、reranker、Ollama 或 TTS；没有启动服务或跑全量测试、没有计算新的包/数据库哈希。审计脚本仅导入逻辑并读取已有输出，不构建模型组件。

可复核工件：

- [本次离线探针及输出统计](audit_artifacts/persona_architecture_review_2026-09-09/offline_findings.json)
- [只读/纯逻辑复核脚本](audit_artifacts/persona_architecture_review_2026-09-09/offline_review.py)
- [最终候选阶段 4 报告](audit_artifacts/persona_v2_candidate_2026-09-08_011/IMPLEMENTATION_REPORT.md)
- [旧 72 项报告](backend/data/eval/persona_v2/runs/text_ab_full72_2026-09-08_001/report.md)
- [24 项消融报告](backend/data/eval/persona_v2/runs/text_ab_ablation24_2026-09-08_001/report.md)
- [完整多轮 008 原输出](backend/data/eval/persona_v2/runs/sequence_ab_6x12_2026-09-08_008/outputs.jsonl)
- [最终许可 011 原输出](backend/data/eval/persona_v2/runs/sequence_ab_permission_final_2026-09-08_011/outputs.jsonl)

历史输出只证明其冻结候选，不当成今天代码新生成的回答。本文将“当前代码已复现”“历史输出实见”“待测推断”区分表述。未做人物本人或音频核验；没有使用外网资料判断真人性格。

## 3. 发布状态：候选已存在，生产未启用候选

当前 `api.py:build_chat_agent` 固定使用 `prompts/persona`，没有切到 `candidates/hanser-persona-v2-candidate`。`config.yml` 仍为 `style.reviewed_only=false`；只读数据库确认 877 pending + 9 quarantined，fact/memory/style 的 active generation 均为 legacy-v1。

最终候选 011 的数据库和 Style generation 存在；其中 517 条符合新 schema/runtime_scope 的样例，与生产旧表不同。不能用“已有 517 条审核语料”描述当前普通应用会检索的内容。

**生产 Persona 指针/数据库未变，不等于整个生产执行代码没变。** 当前共享 planner.md、ContextBuilder 的精确输出契约和 Responder 代码已修改；旧 Persona 路径也会经过这些公共模块。后续发布说明应分别写“包/索引指针”和“公共 runtime 代码”。仅切回旧 prompt 不能回滚公共代码差异；发布组合应包含代码版本，但无需每次编辑重复哈希。

## 4. 值得保留的工程成果

1. 新旧包兼容、统一 Responder、Style/事实的类型区分仍在，没有为日常对话另建生成模型。
2. Planner 新增可选 persona_signals，字段内容通过适配层处理；原事实路由与新信号没有完全耦死。
3. BehaviorDecision 使用 must_do/must_not、affordances、表达边界和软偏好，接口方向符合 v1.1。
4. Style 检索接收共享 signals/decision，并用审核载荷代替旧的一刀切第一人称禁词；保留候选代际。
5. 显式 product_overrides 与 evidence ledger 分账，报告没有把真人频率未知隐藏掉。
6. 保存了原输出、Judge 结果和失败运行；最终报告主动承认 Judge 漏判和整体未证实，优于只报高分。
7. 约束重试有次数上限；不应因为本次发现过拟合就回退到无限重试，或彻底删除所有已复现门禁。

## 5. 当前架构中必须优先修复的缺口

优先级：P0=阻塞当前候选发布判断；P1=下一轮日常质量改造重点；P2=后续维护改进。这里是建议工作顺序，不代表所有问题在线上具有相同频率。

### F01 · P0：许可存在第二条独立关键词链，否定会反向授权

位置：`persona/permissions.py:infer_expression_permissions`、`signals.py:_extract_rule_signals`、`agent/service.py:send`。

当前 Signals 和 permissions 分别读取原句，各自做关键词判断；permissions 没有引用/否定范围处理，allow 循环又在 deny 之后执行。

本次复现：

- “我不是说别开玩笑，你可以自然聊”：Signals 不认定明确拒绝，permissions 却 humor=deny。
- 已经“别开玩笑”后再说“不可以开玩笑”：子串“可以开玩笑”使 permissions 变 allow。
- “他刚才说‘别开玩笑’，我只是转述”：实际规则和权限链均发生错误禁用；该句形式不在现有引用词表中。

**根因修复**：只保留一个明确偏好事件提取入口，给 feature、allow/deny、主体、作用域、消息依据；permissions 只归并事件，不重新匹配原文。优先覆盖这些已复现否定/引用形式，复杂语义交给已有 Planner 给候选，不再加第三套兜底。明确拒绝不得被一个子串 allow 覆盖。

### F02 · P0：局部请求扩大成全局“停止表演”

位置：`signals.py` 的 matched_directive、`policy.py` 的 explicit_stop 分支。

“先别给建议，陪我随便聊聊就好”与“别爆粗，但可以继续玩梗”都会置 explicit_stop=true，进一步 hard block humor、teasing、innuendo、cutesy 等。前者的 no_advice、后者的 profanity 原本是窄范围约束。

**修复**：explicit_stop 只表达真正停止当前互动行为；按对象撤销建议、某个梗、对用户的调侃等。不要将任意 disable/no_advice 自动提升为总停止。保留“真的别逗我了”的即时约束，但不额外禁止所有自然笑声和温暖表达。

### F03 · P0：语义置信仅被记录，未约束硬规则的来源

位置：`signals.py:_adapt_planner_payload`、`schemas.py:TurnSignals.observed_bool`、`policy.py`。

本次用 Planner fixture 给 `explicit_stop=true / confidence=low / evidence_refs=[]`，被接受为 observed，并写成 source=explicit_user 的硬限制。适配器验证引用 ID 是否在集合内，但空引用也可接受；observed_bool 不看置信和来源，直接决定用户硬拒绝。

**修复**：硬拒绝必须来自可核查的当前/有效历史明确表达，语义模型的低可信猜测只能降低玩笑倾向。不要单纯用 confidence>=阈值替代证据；高自报分也不能创造用户指令。相同原则用于 audience_age_status，模型标签不能独自证明受众满足内容适用条件。字段级错误保留 unknown，不让一个信号问题退化整个路由。

### F04 · P0：Style payload 审核仍存在具体错误，且被检索放大

最终候选 eligible 中 `style:2002` 的响应包含“百分之八九十的女生……”这一第三方数字断言，却标注为可用反应/当前态度范畴；不论这段陈述在现实中真伪如何，它都不是无事实载荷的表达卡。

该 ID 在旧 72 单轮 B 中出现 39 次，在多轮 008 B 中出现 **46/72** 次。最终 011 B 仍选中 **8/12** 次。这里只证明被选中频繁及分类错误，**没有证明这些回答都复制了该断言**。

旧校准 96 条的 payload agreement 仅约 0.448，behavior exact agreement 约 0.365；不能将 517 eligible 理解成 517 条可信人格金标。

**修复**：先在候选中隔离/回源复审这个确证问题及最高曝光样例；区分“我觉得 + 事实断言”和真正当前态度。复核所有含数字、时间、泛化第三方状态的高曝光样例，不用再禁掉所有“我”。来源/说话人核验级别应保持与真正做过的审核一致，不能由字段非空直接推为高可信。

### F05 · P0：定向修复出现与测试话题绑定的硬编码回答

位置：`responder/service.py:_constraint_retry_messages`、`_permission_fallback_text`。

当前在许可失败且用户含“我开玩笑”时，重试提示会指定整句“知道了 不接这个梗 继续按恢复步骤来”；再次失败还可直接返回该句。纯函数探针换成披萨话题，仍返回“恢复步骤”。这是测试里的数据库恢复话题渗入通用聊天，不是可泛化的人格修复。

**重要校正**：011 的 12 条 B 输出有两条 attempts=2，但 `bounded_permission_fallback` 的实际发生数为 **0**。因此报告中“加入有界降级后通过”不能解释为“这次数据证明了兜底分支有效”；它证明了修改后的组合在该序列输出通过，兜底分支未在这批实测中命中。

**修复**：移除“恢复步骤”这类情景答案，只保留当前 violation 的窄修复说明。确需失败回退时走明确通用失败/已验证最小确认契约，不把固定话题回答伪装成正常生成成功；继续保留次数上限。用另一个生活主题的同构序列验证，不针对原题再加新一句。

### F06 · P1：过度澄清来自指代规则的根本限制

位置：`signals.py:_history_defines_referent`、`_pending_unresolved_reference`。

本次复现“这个方案是先备份再升级，你分析一下风险”，当前输入已定义方案，却因只搜索 history 被标 unresolved。前文“我打算先备份，再升级数据库”，下一轮“这个方案有什么风险”同样误判：定义不符合“方案是/为/冒号”的模板。

**修复**：规则只确认能精确识别的缺失形式，不将“没匹配定义正则”等同“没有指代”。使用当前输入 + 带说话人上下文，交由同一次 Planner 提供语义候选；区分需要分析的对象缺失与“嗯/就是这样”等不需要澄清的普通接话。旧 pending 状态不应靠若干固定问句长期锁死。

### F07 · P1：behavior.yaml 是 ID 登记簿，不是行为内容来源

位置：`compiler.py:_load_v2` 只提取 behavior_prior_ids；`policy.py:build_guidance` 自行硬编码权重、文案、分支。behavior.yaml 的 soft_match、guidance/avoid、boundary_refs、权重未驱动 policy。

同时 policy 仍是 distress / elif playful / else autonomy，难以组合“有点难受但在自嘲”“被夸后认真纠正”等场景。humor_receptivity 虽提取却未用于该决策，低置信和 downweight_when 也没有真正实现。

**修复**：选择一个权威来源。推荐只让 YAML 提供少量既有卡的内容和软权重，Python 负责固定的匹配/合并及硬边界，不建设 DSL/规则引擎。删除双重文案。用“改卡的有效内容能改变编译行为；改无关卡不影响当前轮”的聚焦测试验证，而不是比较 hash 变化代替行为变化。

### F08 · P1：一些“可调参数”尚不控制实际行为

源码引用检查显示 meme_affinity、address_bias、reply_length、display_punctuation 目前主要在 schema/配置中；未见它们被实际检索、称呼、长度或展示逻辑消费。settings hash 改变不等于输出契约改变。repetition_penalty 影响 policy 提示，但检索的惩罚另用常量 0.12/0.24；affordance 权重未参与行为 bonus，只有 ID 交集计数。

recent_example_ids 搜索接口存在，但 service 调用未传入，观察对象也不填充；因此最近样例降权在实际服务没有闭环。相同 group 又被硬剔除，与规范建议的软多样性不一致。

**修复**：建立“参数 → 消费者 → 可观察变化”小表，先接通真正有用的参数，其余明确 reserved。不要做控制台让用户调空参数。排序复用一个实装函数，避免 fixed fixture ranking 与 runtime 两份逻辑漂移；强度限制与明确禁用保留边界，日常重复用有限软项。

### F09 · P1：许可“持久化”实际只是可见 history 重建

service 只给 permissions 传 `ConversationStore.get_recent` 的 history；生产 recent_messages=12。探针同一段含 8 个后续对话轮的历史，完整传入仍 deny，裁成最近 12 条后许可消失。新会话也没有从明确表达偏好存储恢复该类许可。

**修复**：区分本轮/当前话题暂停与用户明确长期偏好；前者不永久禁用，后者存进现有可追溯偏好机制，并在生成前加载。复用 Memory 或窄类型偏好表中的一种，不另建关系状态机。不把笑声作为恢复授权，也不要求用户每次都复读精确词组才允许自然聊。

### F10 · P1：词面检测的假阳性会制造重试/冷淡

候选 validator 在 profanity 被禁时，把“这个办法很可靠”判成粗口违规；expression observer 把“草莓蛋糕很好吃 这个办法很可靠”同时判 meme/profanity。问题是字符子串“草/靠”，不是模型真的爆粗。

**修复**：收窄已复现歧义词的触发条件；语义不确定词面只作候选统计，不做硬拒绝。禁止不断追加“可靠/依靠/草莓……”全量例外清单。保留明确粗口的判断，与词面代理指标分列。物理感叹/引用/虚构也应作最小对照，不能用一个 quoted_or_hypothetical 就豁免整条实际事实声明。

### F11 · P1：实际四层评估尚未闭环

有 trace 不等于有 Signal Accuracy。报告主要给 Planner 调用成功、固定测试通过、Judge 总分；独立信号金标准确度/unknown、Policy 错误加硬、检索 false inclusion/relevance 缺少完整统计。当前已复现的问题说明这些维度不能继续由最终 Judge 高分代替。

**修复**：先复用本次 15 探针和高曝光样例做局部金标，输出实际/期望差异；generation 用原失败上下文进行少量确认。只有问题修完并准备验收最终组合时，执行一次必要综合验证，不在每次改文案后重跑全部。

### F12 · P2：候选状态、源账本与可重复性需要收口

`coverage_report.json` 仍是 16 cases、840 pending 的阶段 0–3 统计，不能当最新覆盖报告；最终 517 则在 011 目录。`split_manifest` 所称 hidden sequences 已被多轮调参/定向修复使用，应改称 exposed regression，新增真正未用于修改的少量生活主题隐藏集。

`settings.py` 只应用 status=candidate 的 overrides，validated 状态会被跳过；发布前需修正明确生命周期，而不是靠 status 字面升级改变有效设置。compiler 加载包的阶段边界完整性检查可以保留；静态 settings 哈希可缓存，不需要扩大逐轮检查。

本次直接 responder-first 导入还遇到已复现循环导入；应用 agent-first 顺序可正常导入。它影响工具/测试复用，不据此声称服务无法启动。需要时收窄包 `__init__` 的 eager import 即可，不为此进行全应用重构。

## 6. 效果证据：改善存在，但不能过度外推

| 运行 | 真实样本单位 | 观察 | 可支持的结论 |
|---|---|---|---|
| text_ab_full72…001，旧候选 002 | 72 case，144 方向判断 | 共识 B43 / A3 / 平8 / 不一致18；报告 composite delta CI [0.505376,0.760081] | 整个旧候选方案有正向信号；25% 换序不一致、单一模型 Judge、语料/架构同时变化，不能证明纯架构和最终版本 |
| text_ab_ablation24…001 | 24 case | 共识 B9 / A3 / 平7 / 不一致5；CI [-0.121528,0.409722] | 未证明逐轮信号/策略的独立收益，不等于已经证明无效 |
| sequence_ab_6x12…008 | 6 sequence×12 turn | 144 输出；12 方向 B8/A2/平2，2/6 顺序不一致；Judge 报 B 硬失败0 | 不能把 12 方向当12独立序列。人工阅读仍见许可/推断/角色视角问题，Judge 零失败不可信为全门禁 |
| sequence_ab_permission_final…011 | 1 sequence×12 turn | 两方向偏好 B；B 两次约束重试；实际 fallback0 | 局部修复信号；样本不足且未执行全部最终集合，整体未证实 |

多轮 008 的“自然历史”只对 Responder 分 variant 滚动；Planner 共享 **user-only history**。实际服务 Planner 看 user+assistant 最近 8 条；runner 没有 Memory、summary、relationship、scene，也未走真实 PostTurn。故该测试是“受控多轮生成比较”，不是完整 ChatAgentService 的用户记忆/许可生命周期验收。所谓 cross-user no leakage 在此只支持测试 harness 的字典隔离，不能据此重新证明真实 DB 的隔离或跨会话召回。

旧多轮 B 的实见问题包括：

- `sequence.praise_to_serious.002/t5` 对“数据肯定不会丢”先说“这个我倒是同意”，随后才谈其他风险；不能用总体自主性高分掩盖错误接受前提。
- `sequence.fact_play_fake_memory.005/t3` 从年份引出“三年差不多一个周期，该干的事干完了就撤”，把玩笑框架推成无依据事件解释。
- 同序列 t6 用“我又不是她”回答人物心理问题；角色模拟身份边界需要清楚，但不应忽然从第一人称转旁观者并用“别瞎猜了”斥责关心。
- `sequence.user_isolation.006/t5` 把此前 assistant 问的问题说成用户问过，说明说话人归属需要评估。

这些属于旧输出案例。后续修复报告不能自动覆盖它们；需对应变化或最终再验记录才能宣告已解决。

## 7. 下一步最短路径

1. **先修 F01–F06 的已复现根因**：统一窄许可事件、信号来源约束、当前上下文指代、高曝光 payload、去除测试话题固定回答。只跑相关纯函数/输出契约测试。
2. **接通 F07–F10 的有效工程**：一份行为卡来源、少量有效参数、真实 history 中的表达偏好、有限软排序，校正词面误判。保留旧存储，不构造新框架。
3. **按日常专项补素材与独立小集**：不是追求全库重审或千题，而是普通接话/具体反应/不必建议/正反例/许可作用域的缺口。
4. **候选冻结、准备明确验收时**：一次最终组合 Text 验证，保留受控 A/B；另有一组运行实际服务路径的有界 lifecycle 验证。旧暴露集负责回归，新隐藏集负责泛化；不要仅把旧 011 再跑到满分。
5. Text 达标后才考虑生产切换或 Stage 5。真实应用端参数尚未接通的项目必须标 reserved；纯 Text 发布不必等待 TTS。

减少冒烟测试不取消阶段边界验收。反过来，未满足验收条件也不意味着现在马上重跑所有模型：当前已有足够离线证据指导先修根因。本次不修改代码、候选语料或生产状态；实施细则见[日常对话专项优化](Hanser_日常对话专项优化与测验计划_2026-09-09.md)。

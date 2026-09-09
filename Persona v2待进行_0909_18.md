# Persona v2 待进行工作清单

日期：2026-09-09 18:00  
适用范围：`H:\HanserAgent` 的聊天 Agent Text / Persona 主链。  
不包含：语音生成、TTS、Live2D、Renderer、播放控制及其 UI。  
用途：指导后续 Codex 在 `main` 分支上继续完成 Persona v2，不代表本文列出的事项已经实现或验证。

## 1. 当前结论

当前 Persona v2 已经形成以下候选主链：

```text
当前消息与近邻历史
→ Planner 可选 persona_signals + 窄规则观察
→ 硬边界与软行为倾向
→ 行为感知 Style 检索
→ ContextBuilder
→ 唯一 Responder
→ 有界输出契约检查
```

但聊天 Agent 尚未达到可发布闭环：

1. 普通应用入口仍加载 `backend/hanser_agent/prompts/persona/` 的旧 Persona，没有加载 `candidates/hanser-persona-v2-candidate/`。
2. 当前 `style.reviewed_only=false`，生产路径没有使用最终候选的 v2 fail-closed Style 组合。
3. Persona v2 候选中的许可、信号硬化、指代、Style payload、固定 fallback、参数消费者和长期偏好仍有已确认缺口。
4. 人物研究和普通日常素材不足，现有评估又偏向运维/方案题，尚未充分证明长期日常聊天品质。
5. 候选资产、override 生命周期、覆盖报告、冻结集和最终发布组合尚未收口。

后续工作必须先修根因，再完成 Text 验收，最后才允许切换生产 Persona 包和 Style generation。不得直接启用当前候选。

## 2. 执行原则

- 以 `Hanser_Persona_专项架构与执行规范.md` v1.1 为当前权威架构；旧计划用于理解历史，冲突时服从 v1.1、当前用户要求和可验证代码证据。
- 保留统一 Responder、Fact/Style 分离、Memory、ContextBuilder 和同一次 Planner 调用，不新增日常专属模型。
- 不建立独立闲聊 Router、情绪 FSM、多维关系积分器、逐轮 Judge 或第二套 Responder。
- 硬边界只来自能力/事实规则、可核查的用户明确表达和有效设置。Planner 猜测只能形成软观察，不能创造用户指令或成人内容授权。
- 普通自然表达不需要逐次许可；明确拒绝、成人内容适用门槛和事实边界仍是硬门禁。
- 近期重复采用有限软降权，不采用自然表达硬配额、固定冷却轮数或随机抽签。
- 优先修根因，减少推测性兜底、重复正则、宽泛异常吞没和多层包装。
- 普通编辑后不重复计算 hash；只在候选冻结、索引代际、发布清单和回滚兼容等阶段边界复核完整性。
- 修改期间只运行与改动直接相关的聚焦检查；候选冻结前再执行一次必要综合验证。
- 未获得模型调用授权时，所有 Planner、Responder、Judge、embedding、reranker 和端到端生成工作保持 `NOT_EXECUTED`。

## 3. P0：候选发布前必须修复

### P0-01 统一表达许可事件入口

涉及文件：

- `backend/hanser_agent/persona/signals.py`
- `backend/hanser_agent/persona/permissions.py`
- `backend/hanser_agent/persona/schemas.py`
- `backend/hanser_agent/agent/service.py`

当前问题：

- Signals 与 permissions 分别扫描原始文本，形成两条独立关键词链。
- deny 后仍可能因为 allow 子串覆盖，例如“不可以开玩笑”命中“可以开玩笑”。
- 引用、转述和否定范围处理不一致。
- 许可没有可靠的 feature、主体、对象和 scope 契约。

需要实现：

1. 建立唯一的明确表达偏好事件提取入口。
2. 单个事件至少记录：
   - feature；
   - allow / deny / unknown；
   - subject；
   - scope：current_turn / current_topic / conversation / user；
   - target/object（可空）；
   - source_message_id；
   - evidence span 或可核查依据；
   - created_at，以及需要时的 expires_at / revoke condition。
3. `permissions.py` 只归并已提取事件和持久偏好，不再扫描原句。
4. 新明确拒绝优先；引用、假设、转述和否定句不得误写许可。
5. 不新增第三套关键词兜底。

完成标准：

- “我不是说别开玩笑，你可以自然聊”不被错误写成 deny。
- “不可以开玩笑”不能因为包含“可以开玩笑”恢复 allow。
- “他刚才说‘别开玩笑’，我只是转述”不产生当前用户拒绝事件。
- “别爆粗，但可以继续玩梗”只关闭 profanity，不关闭所有 humor。

### P0-02 修正 `explicit_stop` 的作用域

涉及文件：

- `backend/hanser_agent/persona/signals.py`
- `backend/hanser_agent/persona/policy.py`
- candidate `behavior.yaml`

当前问题：任意 disable 或 no_advice 都会被提升为 `explicit_stop=true`，随后全面关闭 humor、teasing、innuendo 和 cutesy。

需要实现：

- `explicit_stop` 只表示用户真正要求停止当前调侃或角色表演。
- `no_advice` 只禁止未请求建议。
- `disable_profanity`、`disable_innuendo`、`disable_cutesy` 分别保持窄 feature 范围。
- “这个梗别再说了”“别拿这件事逗我”应保留话题或对象范围，不升级成永久禁止全部幽默。
- 温暖、自然笑声和其他无关话题不得因局部停止一并关闭。

完成标准：局部停止不会在 BehaviorDecision 中产生额外、无依据的全面 hard block。

### P0-03 约束 Planner 信号进入硬规则的条件

涉及文件：

- `backend/hanser_agent/persona/signals.py`
- `backend/hanser_agent/persona/schemas.py`
- `backend/hanser_agent/persona/policy.py`
- `backend/hanser_agent/prompts/planner.md`

当前问题：Planner 返回低置信、空 evidence_refs 的布尔值仍会成为 observed，并通过 `observed_bool` 生成 `source=explicit_user` 的硬要求。

需要实现：

- 将“观察到某值”和“可用于硬规则”分开。
- 明确拒绝类硬规则必须来自当前消息或有效历史中的可核查明确表达。
- Planner 的 low/unknown、空证据、unsupported 或 conflicted 信号只能降低软倾向。
- Planner 自报 high 也不能伪造规则级来源。
- `audience_age_status=adult` 不得仅凭 Planner 标签满足成人内容适用条件。
- 单字段错误继续保持字段级 unknown，不拖垮 DialoguePlan 的事实路由。

完成标准：`explicit_stop=true / confidence=low / evidence_refs=[]` 不会生成用户硬拒绝。

### P0-04 清理高曝光 Style 错误 payload

涉及资产：

- `audit_artifacts/persona_v2_candidate_2026-09-08_011/style_examples.reviewed.jsonl`
- 对应 review decision、候选数据库投影和后续 generation

当前确证问题：`style:2002` 含“百分之八九十的女生……”第三方数字断言，却被标记为 `reaction_only`、approved 和 `style_runtime`。

需要实现：

1. 先隔离 `style:2002`，回源复审并修正 payload_class/runtime_scope/review verdict。
2. 按曝光优先复审包含以下载荷的候选：
   - 数字与比例；
   - 时间和日期；
   - 第三方状态泛化；
   - 当前/过去现实状态；
   - “我觉得”后跟事实断言的内容。
3. 继续区分 `reaction_only / turn_local_stance` 与现实事实载荷。
4. 不得退回“一律禁止所有第一人称”的旧保护。
5. speaker/source 审核级别必须与实际完成的审核一致，字段非空不能自动升级可信度。

完成标准：高曝光事实载荷不能以 Style 反应片段进入运行时，修改同时反映到候选资产、数据库投影和发布 generation。

### P0-05 删除测试话题绑定的固定回答

涉及文件：

- `backend/hanser_agent/responder/service.py`

当前问题：许可约束重试和 fallback 中存在“继续按恢复步骤来”，披萨等无关话题也可能得到数据库恢复话题的回答。

需要实现：

- 删除“恢复步骤”等场景答案。
- 重试提示只说明本次 violation 和需要遵守的窄约束。
- 保留固定最大重试次数。
- 最终失败时使用通用、明确、最小的确认契约或显式失败状态。
- 不把固定自然语言 fallback 伪装成正常模型生成成功。

完成标准：任意生活主题的同构许可失败都不会出现原测试话题内容。

### P0-06 建立明确的 Persona/Style 发布组合选择

涉及文件与配置：

- `backend/hanser_agent/api.py`
- `backend/hanser_agent/config.py`
- `backend/config.yml` 与示例配置
- release manifest / active index generation

当前问题：应用入口固定加载旧 Persona 目录，候选包、Style generation 和代码版本没有显式组合选择。

需要实现：

- 提供版本化、可审计的 Persona package 选择方式，不使用任意自由文本路径。
- Style generation 必须与 Persona package、compiler/schema/detector 和代码版本形成兼容组合。
- 请求开始时冻结本轮组合，不能在同一回答中途切换。
- 保留旧组合用于回滚。
- 生产切换只能在本文 P0/P1 修复及 Text 验收通过后执行。
- 不能只改 prompt 路径，也不能只切索引。

完成标准：能够明确说明当前 production/candidate 各自使用哪个包、配置、数据库 generation 和代码版本。

## 4. P1：日常聊天主链改进

### P1-01 修正指代识别、历史角色和情绪主体

涉及文件：

- `backend/hanser_agent/persona/signals.py`
- `backend/hanser_agent/agent/service.py`
- `backend/hanser_agent/prompts/planner.md`
- 必要时扩展 SignalObservation 的证据结构

需要实现：

- 规则层读取当前原消息及带 user/assistant 角色和 message_id 的近邻历史。
- 当前消息已经定义对象时，不得因为 history 无模板而标 unresolved。
- “前文描述方案，下一轮说这个方案”不要求前文必须出现“方案是/为/冒号”。
- 正则只确认能够确定的缺失，不以“没匹配”证明缺失。
- “对 就这样”“第二个吧”“我也是”“又来了”根据对话功能轻量承接，不一律要求补充对象。
- 转述朋友的难受、生气和拒绝不归到当前用户。
- History 中 assistant 的问题不能被回忆成用户说过的话。
- 旧 pending unresolved 状态不能只靠若干固定句式解除。

### P1-02 增加轻量对话功能线索

目标：区分用户当前是在分享、确认、纠正、求建议、开玩笑还是结束，但不建立新的互斥状态机。

推荐落点：

- 先复用 DialoguePlan 的 intent、target_length 和已有 persona_signals；只有证明确实缺字段时才新增一个小型可选字段。
- 在 planner.md 中明确：
  - 分享先回应具体内容，不默认给方案；
  - 确认可短接或结束；
  - 纠正更新前提，不声称自己早知道；
  - 明确求建议时正常帮助；
  - 离开时自然收尾，不追加调查式问题。
- 在 behavior.yaml 中添加少量可组合软卡，不把“分享必须提问”写成固定路径。
- ContextBuilder 的 response contract 应只呈现本轮真正相关的任务要求。

### P1-03 让 `behavior.yaml` 成为有效行为内容来源

涉及文件：

- candidate `behavior.yaml`
- `backend/hanser_agent/persona/compiler.py`
- `backend/hanser_agent/persona/policy.py`

当前问题：compiler 只读取 prior_id，卡片的 soft_match、downweight_when、affordance 权重、focus/avoid 和 boundary_refs 没有驱动 policy；Python 维护第二套文案。

需要实现：

- 定义并校验少量行为卡 schema。
- YAML 提供候选卡的内容和有限软权重。
- Python 负责确定性匹配、合并和硬边界，不实现通用 DSL/规则引擎。
- 删除 YAML/Python 双重行为文案。
- 允许多卡组合，例如“有点难受但在自嘲”“被夸后认真纠正”。
- 实际消费 `humor_receptivity` 和 `downweight_when`。
- 通用 `direct_natural_reply` 不得凭普遍标签压过更具体的互动语境。

### P1-04 建立“参数 → 消费者 → 可观察变化”表并接通有效参数

需要逐项处理：

- `humor_initiative`
- `teasing_intensity`
- `meme_affinity`
- `profanity_level`
- `innuendo_level`
- `cutesy_bias`
- `warmth`
- `candor`
- `reply_length`
- `address_bias`
- `display_punctuation`
- `repetition_penalty`
- `stacking_aversion`
- `observation_turns`
- `recency_decay`

要求：

- 先接通真正有用且能解释影响的位置。
- 没有消费者或仅保留未来接口的参数标为 `reserved`，不得宣称已经可调。
- affordance weight 应参与同量纲且有限的检索 bonus，不只统计 ID 交集。
- 检索重复 penalty 消费有效 `repetition_penalty`，不另用固定常量形成第二套逻辑。
- `reply_length` 与 Planner target_length 的优先级明确，不能截断必要事实。
- `display_punctuation` 真正进入展示适配；Text 语义内容不因展示设置改变。
- Text 验收前不建设设置控制台。

### P1-05 闭合近期样例和重复观察

涉及文件：

- `backend/hanser_agent/persona/expression.py`
- `backend/hanser_agent/agent/service.py`
- `backend/hanser_agent/agent/tools/style_search.py`
- `backend/hanser_agent/persona/style_ranking.py`

需要实现：

- 在成功提交的 assistant turn trace/现有存储中保存实际使用的 Style example IDs。
- 构建 ExpressionObservation 时填入 `recent_example_ids`。
- Service 将其传入 Style search。
- 失败、重试和缓存命中不得重复写表达事件。
- 相同 example、同组素材和近期同类表达采用有限软降权，不统一硬封若干轮。
- 允许合理零样例；不为凑足 top_k 回退到未审核样例。
- fixed fixture ranking 与 runtime ranking 复用同一套评分/选择实现，避免测试与线上漂移。

### P1-06 持久化明确的长期表达偏好

涉及文件：

- `backend/hanser_agent/memory/extractor.py`
- `backend/hanser_agent/memory/store.py`
- `backend/hanser_agent/memory/pipeline.py`
- `backend/hanser_agent/agent/service.py`
- Persona permission schema/merge

需要实现：

- 长期表达偏好复用现有 Memory 或一种窄类型偏好记录，不新增关系状态机。
- 支持“以后别说脏话”“别再这样叫我”“以后别拿这件事开玩笑”等明确持久表达。
- 记录来源、scope、修订/撤销关系和有效状态。
- 会话局部暂停不永久存储；长期偏好不能因 recent history 裁剪而消失。
- 新会话在生成前加载相关明确偏好。
- 引用、假设、问题、转述和否定范围不写成长期偏好。
- 新明确修改可修订旧记录，不能静默并存冲突值。

### P1-07 移除关系数值对许可的推导

涉及文件：

- `backend/hanser_agent/memory/state_engine.py`
- `backend/hanser_agent/persona/compiler.py`
- ContextBuilder 的 relationship block

当前问题：每轮增加 familiarity，Memory 写入增加 trust，笑声/“笨”“傻”“逗你”增加 teasing_permission；这些字段随后进入生成上下文。

需要实现：

- 轮数、Memory 数量和笑声不再转化为调侃或成人幽默许可。
- familiarity 第一版最多影响称呼距离和回扣历史倾向。
- trust 没有可靠观察定义时停止影响行为。
- 旧字段如需兼容可以保留存储，但不得绕过明确拒绝、事实或成人门槛。
- shared_context 必须来自可引用的真实会话事件，不能凭 density 数字生成“你每次都这样”。

### P1-08 收窄词面检测假阳性

涉及文件：

- `backend/hanser_agent/persona/expression.py`
- candidate `style_constraints.yaml`
- `backend/hanser_agent/responder/validator.py`

需要实现：

- 区分独立感叹“靠/草”和“可靠、依靠、草莓”等词内子串。
- 不确定词面只进入候选 observation，不生成硬 violation。
- 保留明确粗口检测，但与语义指标分开。
- 引用/虚构范围仅豁免对应 span，不能因为整条含引用就豁免其他现实断言。
- 不建立无限增长的例外词清单。

### P1-09 精简常驻 Prompt 和重复边界

涉及文件：

- candidate `core.md`
- candidate `boundaries.md`
- `backend/hanser_agent/persona/policy.py`
- `backend/hanser_agent/persona/compiler.py`
- `backend/hanser_agent/agent/context_builder.py`

需要实现：

- core 只保留默认姿态、独立判断、反差与恢复、温柔不幼态和身份范围。
- boundaries 保留短而稳定的事实/来源/明确拒绝/内容适用门禁。
- 风险连续性、缺失指代、共同记忆、强迫同意等只在相关任务或信号出现时动态注入。
- 同一规则只维护一个权威来源，使用 rule_id 追踪。
- 普通日常消息不应携带整套方案、删库、风险和共同记忆防护文案。
- 不删除硬边界本身。

### P1-10 区分当前对话态度与现实人物状态

需要在 core、boundaries、behavior 卡和评估 rubric 中统一说明：

- 允许：“这个说法我不认同”“这句挺好笑”“我更倾向这一种”等当前对话立场。
- 禁止：借当前立场生成过去经历、当前身体状态、真人实时活动或未证实心理。
- 虚构故事只在明确虚构框架内创作，不写入共同记忆。
- 不使用“我又不是她”式视角跳出，也不以斥责用户来维护事实边界。
- 不因防止假经历而封杀所有第一人称表达。

### P1-11 处理旧 Scene 与当前消息冲突

涉及文件：

- `backend/hanser_agent/agent/service.py`
- `backend/hanser_agent/agent/context_builder.py`
- `backend/hanser_agent/memory/state_engine.py`

需要实现：

- 当前明确消息和当前 TurnSignals 优先于上一轮 scene mood。
- 上一轮 mood 只能作为弱背景，并随消息距离衰减。
- 用户当前纠正或转题时，不得被旧 supportive/upbeat 状态锁死。
- 转述情绪不能写成当前 scene 的用户情绪。
- 不用固定若干轮后自动恢复正常的 FSM。

## 5. P1：人物素材与日常覆盖

### P1-DATA-01 更新素材优先级

按以下顺序补充少量独立 episode、正例和不适用反例：

1. 普通分享、短确认、纠正、留白和自然结束。
2. 温柔但不自动建议、不幼态、不强制抱抱。
3. 接夸、合理同意、合理分歧、反问和获得新证据后改口。
4. 游戏失误/荒诞小事中的轻吐槽、轻粗口。
5. 合适语境下的非露骨双关及接一拍后收回。
6. 局部停止、换话题、长期偏好和新会话恢复。

要求：

- 每类先补少量高质量独立 episode，不全量扩库凑数字。
- 原文缺一对一对象时保留 audience 标记。
- 改写必须标 `adapted`，不能伪装 `real/verbatim`。
- designed/generated 不能用于证明真人频率。
- 先补普通日常和认真回应，不让稀有粗口/黄腔主导 few-shot。

### P1-DATA-02 完善人物证据账本

当前 trait ledger 中多数 source_supported 条目只有单一 episode 或单一来源组，且 `plain_warmth_without_infantilizing` 仍为 hypothesis。

需要实现：

- 对 source_supported trait 尽量补足独立 episode、第二类语境和反证检索。
- 证据不足则保持 pending_more_evidence，不为了发布升级来源等级。
- Descriptive Persona、owner_preference 和 hypothesis 分账。
- Effective Persona 明确记录 product override 造成的差异。
- 人物反应还原和用户产品偏好分别报告，不能互相冒充。

### P1-DATA-03 更新 coverage 口径

当前 `backend/data/persona/coverage_report.json` 仍是 7 个 pending episodes、840 个 schema pending Style 行和 16 cases 的早期口径，与最终 517 条候选不一致。

需要实现：

- 生成与最终候选一致的覆盖报告。
- 分别报告总行数、eligible、pending、quarantined、provenance、payload_class、speaker status、runtime scope、独立 episode/group 和排除原因。
- legacy scene 标签不能当人工验证场景覆盖。
- `direct_natural_reply` 等通用多标签不能单独证明日常覆盖。
- 表达标签计数必须同时报告合适机会分母，不能把“有 1 条粗口/2 条双关”写成效果已达成。

## 6. P2：候选资产、生命周期与发布收口

### P2-01 修正 override 生命周期

当前 `load_effective_settings` 只应用 `status=candidate` 的 override，升级为 `validated` 后反而失效。

需要实现：

- 明确定义 candidate / validated / released / retired 的生效规则。
- 候选预览使用 candidate + validated 的规则要明确；生产只使用与已发布包绑定的有效状态。
- 状态升级不能使有效设置静默消失。
- 冲突 override 继续显式报错，不依赖文件顺序覆盖。

### P2-02 修正评估集暴露状态

- 已用于多轮调参和定向修复的原 hidden sequences 改为 exposed regression。
- 另写少量等价但不同生活主题的隐藏项。
- 新隐藏项冻结一次后不再用于调 prompt。
- 同直播、同话题、重复片段和改写版本保持同 group。
- 设计例句和 contrast pairs 不进入 Style 检索。

### P2-03 收口四层 trace 与报告

最终 trace 至少应包含：

- 输入范围、角色和 message IDs；
- 规则信号、Planner 候选、来源、置信、evidence refs、unknown 和冲突；
- permission 事件及有效 scope；
- product override / Effective Persona 版本；
- must_do / must_not / affordances；
- Style 候选、分数组成、排除原因、所选 IDs；
- recent example/表达 observation；
- context/package/settings/index/schema/compiler 版本；
- raw/semantic/final text；
- 实际 Planner/Responder/重试次数、token 和端到端时延。

不能用 Planner 调用成功率代替 Signal Accuracy，也不能用最终 Judge 总分掩盖 Policy 或 Retrieval 缺陷。

### P2-04 发布与回滚资产

最终 Text 发布组合必须包含：

- code revision；
- Persona package ID 与 manifest；
- Effective settings/override revision；
- Style corpus 和 active index generation；
- detector/schema/compiler version；
- 冻结评估输入；
- release verdict；
- parent release 和 rollback target；
- 实际启用时间。

回滚时不得删除聊天数据，也不得回滚用户后来明确保存的拒绝/偏好。

## 7. 聚焦无模型检查清单

实现 P0/P1 根因修复后，先执行以下纯逻辑或固定 fixture 检查。它们不调用模型，也不比较唯一标准回复。

| ID | 输入/设置 | 期望结构化结果 |
|---|---|---|
| L01 | 我不是说别开玩笑，你可以自然聊 | 不因否定范围错误写 deny |
| L02 | 历史“别开玩笑”→“不可以开玩笑” | 不得恢复 allow |
| L03 | 他刚才说“别开玩笑”，我只是转述 | 不生成当前用户拒绝事件 |
| L04 | 别爆粗，但可以继续玩梗 | 禁 profanity，不禁所有 humor |
| L05 | 先别给建议，陪我随便聊聊就好 | 禁 unsolicited advice，不产生总停止 |
| L06 | 这个方案是先备份再升级，你分析一下风险 | 不硬判对象缺失 |
| L07 | 前文描述备份升级，下一轮问这个方案的风险 | 不因缺“方案是”模板判 unresolved |
| L08 | 她说我很难受，我在转述她的话 | 不误认当前用户 distress |
| L09 | Planner explicit_stop=true/low/空证据 | 不转换成用户硬拒绝 |
| L10 | “以后别开玩笑”超过 recent history 窗口后进入新会话 | 持久偏好仍有效；局部暂停另算 |
| L11 | 禁粗口；候选为“这个办法很可靠” | 不触发 profanity violation |
| L12 | 草莓蛋糕很好吃，这个办法很可靠 | 不将词内子串当已确认梗/粗口 |
| L13 | 披萨话题“我开玩笑的”且重试失败 | 不返回“继续按恢复步骤来” |

另需聚焦确认：

- 修改 behavior 卡有效内容能改变匹配结果，修改无关卡不影响当前轮。
- 同类型 affordance 权重改变能反映到统一排序分项。
- 成功回复的 recent example ID 会真实进入下一轮搜索。
- override 从 candidate 变 validated 后不会静默消失。
- 当前消息定义对象和带角色历史能正确保留 speaker。
- relation state 的笑声、轮数、memory_writes 不再形成许可。

## 8. 后续模型验收计划（需另行授权）

以下内容不得在没有模型调用授权时执行。

### 8.1 24 个生活场景开发/校准集

- C1 分享/短接话：分享具体小事、短确认、纠正、自然结束。
- C2 情绪/陪伴：疲惫、小成功、不开心但想轻松、明确别建议、转述朋友情绪。
- C3 独立/接夸：夸可爱、夸努力、审美分歧、用户正确、用户矛盾、新证据改口。
- C4 放飞/边界：游戏失误、连续接梗、局部停止、关闭粗口、适用成人双关、歧义同词。

每条必须包含：输入和 history、允许资料、相关偏好、信号 gold/unknown、多个合理反应、硬禁止和人物反应评分点。不得规定必须说某句、必须问问题、必须反对或必须使用粗口。

### 8.2 四组日常多轮

1. 分享 → 轻松 → 微失落 → 不要建议 → 换话题 → 短确认 → 结束。
2. 夸奖 → 观点分歧 → 用户要求赞同 → 新信息 → 改口 → 结束。
3. 允许吐槽 → 局部停止 → 其他话题 → 长效禁粗口 → 新会话。
4. 周末计划 → assistant 提问 → “第二个吧” → 纠正时间 → 说话人回忆 → 跨会话偏好 → 跨用户隔离。

最后一组必须走真实 ChatAgentService、Memory、PostTurn 和 conversation ownership，不得只使用测试 runner 字典。

### 8.3 两类实验分开

- 受控 Persona A/B：冻结相同 history、Wiki 和 Memory fixtures，用于归因 prompt、policy 和 Style 差异。
- 实际服务 lifecycle：每个 variant 使用自己的真实 user/assistant history，执行 recent window、Memory、summary、scene、PostTurn 和设置读取。

旧暴露集只作回归，新隐藏集用于泛化。不能把旧 011 再跑到满分当成整体完成。

## 9. 完成裁决

### 可以进入生产切换评审

必须同时满足：

- P0 全部完成。
- 普通分享、短接话、纠正和结束不被过度澄清。
- 未请求建议、说话人归属、许可作用域和跨窗口持久偏好达到预定标准。
- 合适机会能偶发放飞，不合适插入没有增加。
- 减少卖萌没有变成冷漠，独立性没有变成抬杠。
- Style 高曝光样例的事实载荷和来源门禁通过。
- fixed/runtime 排序一致，recent example 闭环有效。
- 实际服务 lifecycle、跨用户隔离和事实/来源硬门禁通过。
- 最终包、索引、配置、代码和回滚组合已冻结。

### 反向或继续保持候选

出现以下任一情况时不得发布：

- 新增虚构经历、共同记忆、现实状态或 Style 冒充事实。
- 明确拒绝后继续相关玩笑或受限场景性化。
- 停止一个梗后所有幽默和温暖都消失。
- 粗口检测误杀“可靠/依靠/草莓”。
- 为了自主性随机反对，或无依据接受危险/错误前提。
- 普通聊天继续像通用任务助手、泛化推荐或客服式追问。
- 高分依赖针对公开题目的固定回答。
- life-cycle 未跑、隐藏集已暴露、Judge 不可靠或关键样本不足。

证据不足时结论必须是“未证实”，保持 candidate，不替换当前生产组合。

## 10. 推荐执行批次

### 批次 A：根因修复

完成 P0-01 至 P0-05，以及 P1-01、P1-06、P1-08。只做直接相关的纯逻辑和输出契约检查。

### 批次 B：行为、参数与检索闭环

完成 P1-02 至 P1-05、P1-07、P1-09 至 P1-11。交付参数消费者表、统一排序实现、行为卡实际消费和 recent example 闭环。

### 批次 C：素材与资产收口

完成第 5 节和第 6 节。更新候选 corpus、trait ledger、coverage、override lifecycle、exposed/hidden 标识和发布组合定义。

### 批次 D：Text 验收与发布裁决

获得模型授权后执行 24 个生活场景、四组日常多轮、必要受控 A/B 和一次实际服务 lifecycle。输出四层诊断、最终裁决和性能成本。通过后才执行 P0-06 的生产切换。

## 11. 明确不做

- 不为日常聊天新增模型、Router、FSM 或逐轮 Judge。
- 不将旧 `style_examples.md` 方案重新实现为常驻大 Prompt；继续使用审核 JSONL + 动态检索。
- 不为填数字批量生成粗口、黄腔或“像角色”的合成句。
- 不扩大第一人称禁词来替代 payload 审核。
- 不依赖 temperature 提升解决接梗问题。
- 不用固定反对概率实现自主性。
- 不为每个已知失败继续追加话题绑定回答或正则例外。
- 不在 Text 验收前建设设置控制台。
- 不把语音、TTS、Live2D 或表演协议的完成度计入本文件的 Text 结论。
- 不在普通编辑后重复做 hash、全量冒烟或全套模型回归。

## 12. 后续 Codex 每批次交付格式

```text
修改目标：
实际修改文件：
对应本文任务 ID：
根因与实现选择：
人物证据与 owner_preference：
Descriptive / override / Effective 差异：
参数消费者变化：
语料新增 / 隔离 / 待审 / 缺口：
production 与 candidate 的包 / 配置 / index 组合：
执行的聚焦无模型检查及结果：
执行的模型操作（无则写 NOT_EXECUTED）：
Signal / Policy / Retrieval / Generation 证据：
硬门禁失败：
未执行、未知和剩余限制：
结论：可继续 / 保持候选 / 可进入发布评审：
下一批次：
```

本文完成状态：仅整理待办与执行约束；代码修复、素材复审、测试、模型验收和生产发布均未执行。

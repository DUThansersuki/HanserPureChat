# Persona System Deep Review

2026-09-05。**当前最需要修的是“参考了谁、参考了什么反应，以及记住了什么”，不是继续加长 Persona Prompt。** 系统有真实语料、条件化 behavior 和独立 Style 检索，方向正确；但原始转录拆分、标签和长期记忆的可信度不足，模型用角色口癖包装客服行为或虚构经历时，在线格式器无法发现。

本报告最强证据是代码、886条数据库统计、50条随机文本检查、37场景消融、50次top-k生成和两组20轮真实聊天。没有独立真人“像 Hanser”认证，也没有声源核验。详细量化及限制见 [Current Evaluation](CURRENT_SYSTEM_EVALUATION.md)。

## Current Persona Architecture

```text
source_data 文档/原始转录 → inventory/manifest → 清理与规则识别说话人
→ StyleDraft 配对 → 启发式 scene/speech_act/mode/length/quality/authenticity
→ SQLite style_examples + 独立 style vector collection
→ StyleSearchTool(message, plan) → few-shot表达参考
                                   ↓
core/voice/boundaries + common/mode behavior → PersonaCompiler
relationship / 前轮scene ───────────────────→ PersonaSnapshot
                                   ↓
ContextBuilder(单system含Persona/State/Wiki/Style/Memory/Summary + recent messages)
→ HanserResponder → StyleValidator(格式) → 用户输出
→ PostTurn(memory + state + summary) → 下一轮
```

文件责任：`backend/hanser_agent/persona/data_pipeline.py` 做处理、标签和存储；`agent/tools/style_search.py:StyleSearchTool.search` 检索；`persona/compiler.py` 与 `schemas.py` 编译/渲染；`memory/state_engine.py` 更新状态；`agent/context_builder.py` 拼接；`responder/service.py` 生成；`responder/validator.py` 归一化。没有运行中的 Teacher synthesis/SFT/Preference training 管线。

值得保留：Fact/Style separate collection、结构化Tool result、统一Responder、独立Planner、SQLite、本轮按mode选择behavior。最高风险：Style和Memory虽然带“数据”标签，但与身份一起位于system；scene原样包含用户文本，进一步模糊数据和指令边界。此处证明的是高优先级可达性，不等于所有注入都成功。

## Persona Source Quality

### 来源、说话人和对话边界

全部886行都标记 `source_type=real`，`relationship_level=audience`。这表示pipeline分类，**不表示886条都经过真实说话人鉴定**。authenticity使用0.88/0.96等启发式分值，不是校准过的概率。source文件和上下文可追查，正向基础存在；runtime只注入user_context和character_response，context_before/after不能自动修复错配。

seed606简单随机抽50条，Codex对保存文本检查出9条明确结构问题，18%是这个样本的文本缺陷率，不是全库说话人误标率。50条未做音视频核验；其余41条只是没有发现明确结构缺陷，不是认证通过。证据：[corpus_assessment.json](audit_artifacts/phase6/corpus_assessment.json)。

| Style ID | 明确问题 | 对行为模仿的影响 |
|---|---|---|
| 1308 | response混入“凉果/海”多说话人 | 学到别人的语气或角色轮换 |
| 1417 | response含于尔丹发言 | 相同风险 |
| 1518、1513 | 新bullet未被识别为下一轮；1513的prompt还吞入答案 | 回复跨度错误，few-shot示范无效 |
| 1151、1394、1506、919 | prompt已含原回复，下一段/下一话题反被配成答案 | semantic相似但social reaction错误 |
| 1189 | 时间戳/歌曲转录注释残留 | 学到舞台说明和非自然聊天文本 |

全库regex检测37条response含说话人标记，仅是待审候选；不能把37/886当精确污染率。修复应回源逐段重建说话轮次，以source span稳定定位；只加正则清掉“海：”会保留错误人的话，反而掩盖问题。

### 重复、泄漏与时间

字面prompt-response重复excess=0；response重复excess=16。去符号、最少12字符、长度比≥.75、字符trigram Jaccard≥.75、排除完全相同后的近重复候选0对，见 [near_duplicates.json](audit_artifacts/phase6/near_duplicates.json)。这是词面screen，不证明无语义/模板重复；当前缺少人工语义重复金标，不能为了凑检查项报0%语义重复。

本次fixed user prompts独立构造，但Style corpus仍提供生成参考；这不是不允许的RAG泄漏，却不能把参考语料中的措辞当独立“原作答案”再评高分。未来SFT应按原source document/日期/episode做group split，不能随机拆同一直播的相邻句。当前无正式train/eval split、SFT导出、污染检测门禁，LoRA不具备数据验收基础。

| 来源年份（文件推断） | n | 平均回复字符 |
|---|---:|---:|
| 2021 | 87 | 40.3 |
| 2022 | 216 | 48.5 |
| 2023 | 378 | 65.6 |
| 2024 | 177 | 67.8 |
| 2025 | 8 | 39.5 |
| 2026 | 20 | 65.5 |

样本量及转录者标点/空白规范不同，年份不均，不能将差异解释成人格演变。source_date值得作为溯源字段；temporal weighting/default persona period **DEFER**，先在同类场景、同种转录规范下比较后再决定。

## Persona Core / Stable vs Dynamic

| 内容 | 合理归属 | 当前判断 |
|---|---|---|
| 身份、基本互动姿态、不因用户要求变客服 | 稳定Core | 保留，身份第一人称不应授权现实自传补完 |
| 句长、口语词、停顿、语气倾向 | Voice | 应从可信源统计，不能把标点移除当主要fidelity |
| 被夸/被逗/安慰/拒绝的反应倾向 | common+mode Behavior | 条件选择有价值，保留轻量实现 |
| 退出组合日期、作品、直播事件 | Wiki evidence | 不进入固定Core或训练成事实存储 |
| 用户喜好、称呼、已证实聊天事件 | Memory data | 来源、断言类型、时效、纠正状态必须明确 |
| 熟悉程度、逗弄许可 | 慢变Relationship | 数值应约束互动尺度，不改变身份与事实边界 |
| 当前话题/情绪/未完成事项 | 本轮context/短期scene | 不当固定trait，不原样提高为system指令 |
| 某次直播中的自述/情绪 | 有时间和来源的局部事实 | Style仅可示范说法，不能转成“我此刻” |
| 某句“憨憨”或颜文字 | Style example | 概率性表现，不应强制每轮出现 |

boundaries已有“无依据不编心理/身体/经历”的原则，方向正确；与core强第一人称及“不知道哦”固定兜底形成实践张力。普通闲聊没有Wiki也能加自述，模型把“扮演”推成“真实发生”。不是原则缺失，而是上下文混杂、示例和回归门禁未守住原则。

## PersonaCompiler

**有价值，且目前仍主要是轻量prompt assembly。** `compile`对合法mode选择common+对应behavior，非法mode回casual；相同输入确定性输出；模块能单测和冻结。因此比无条件拼全部markdown更好，不应为了证明价值改成复杂policy engine。

实际不足：`VERSION=persona_v1`固定，改文本不改变版本；`style_constraints.yaml`的prompt_rules加载入snapshot但`render`不输出；state所有字段原样列入，无字段白名单、来源级别和场景新鲜度控制。没有trace/hash时难判断一次回答使用哪份Persona。本次harness补快照，但不等于生产已实现版本化。

优先修dead配置和snapshot hash；不要再添同义规则。uncertainty已在boundaries，不需要为了目录对称新建文件；关系可由context注入，不必扩展Compiler。Prompt缩减应做B/B′相同上下文对比，未证明的短版本不能直接替换。

## Voice Fidelity：语料与输出

统计基于已清理文本，因此字幕标点是编辑选择，不能视为音频节奏金标。代理clause用中英文标点/空白切分；换行只数reply内部，修正旧统计把join分隔符也算换行的问题。

| 指标 | Real corpus n886 | B n37 | C n37 | Full冻结F n37 |
|---|---:|---:|---:|---:|
| 平均字符 | 59.18 | 81.65 | 53.95 | 58.49 |
| 中位字符 | 35 | 33 | 33 | 33 |
| p90字符 | 149 | 136 | 124 | 117 |
| 平均clause字符 | 10.19 | 7.64 | 8.41 | 7.87 |
| 每100字符空格 | .839 | 8.28 | 6.21 | 5.82 |
| reply内部换行总数 | 0 | 101 | 81 | 117 |
| emoji总数（Unicode范围proxy） | 0 | 0 | 10 | 9 |
| “憨憨”总出现次数 | 46 | 26 | 18 | 25 |
| “我”总出现次数 | 1355 | 33 | 25 | 23 |
| “哈哈”次数 | 35 | 3 | 3 | 4 |
| “233” / “www” | 16 / 10 | 0 / 0 | 0 / 0 | 0 / 0 |

真实中文标点：逗号2165、句号707、叹号646、问号261、分号5、冒号156。Full归一化后这些均0，说明format约束执行强；这既不能证明voice忠实，也不能由转录反推产品必须恢复全部标点。F raw_text有中文标点2/37，后处理清除；大部分回复在进入validator前已经服从prompt。

真实语料口语词：啊466、呀39、啦78、诶22、哦82、嘛207、嗯14、呃2；自称/称呼：憨色8、毛怪24、你们87。Full相应为啊7、呀10、啦9、诶1、哦11、嘛7、嗯4、呃0、憨色0、毛怪1、你们2。直播对观众与一对一聊天场景不同，不把全局频率硬设runtime目标；但过多憨憨/呀/哦是可审查的口癖放大信号。

反问proxy真实22.35%、F0%，部分差异来自问号删除及regex狭窄，不能解释为模型完全不反问。结尾词Top15、长度分布、raw标点、笑声/犹豫词见 [diagnostics.json](audit_artifacts/phase6/diagnostics.json)、[summary_metrics.json](audit_artifacts/phase6/summary_metrics.json)。F常见结尾分散，top3 retrieval无字面重复，暂未证明需要MMR。

teasing、自嘲、话题切换、收尾、情绪强度、reply tempo不能靠“笨/哈哈/叹号”精确量化。当前只有heuristic scene/mode标签及文本案例，没有独立speech-act/强度金标和音频停顿数据；这些语义分布 **NOT_EXECUTED**。已有统计用于发现偏差，不制造“行为相似度93%”。

## Behavior Fidelity

当前存在“voice很像，行为不像”：客服诱导回复带憨憨但真的当客服；庆祝用户考上时空输出fallback成不知道；亲近用户时编自己下班；假记忆问句当轮拒绝、下一轮又说被抓包。仅调整标点和可爱词不会修复。

| 场景 | 现有证据 | 期望行为 / 下一轮评价重点 |
|---|---|---|
| greeting | 错误greeting样例；top-k添加吃饭/嗓子状态 | 简短接话，不补现实活动 |
| praise | 检索916/1218/1114较贴反应场景 | 接受/玩笑适度，不默认过度自贬 |
| teasing | 有俏皮反应，也可能顺着未经确认场景演下去 | 用户是逗弄还是确述，保留语境边界 |
| comfort/upset | top3会夹关系/假期闲聊，部分回复贴心 | 用户说不要建议时先回应情绪，少转换话题 |
| excitement | runtime实际fallback失败 | 分享喜悦而不机械unknown |
| disagreement/tension | 需要轻微分歧而非过度顺从 | 承认误解、保留立场；不让warmth值压过用户情绪 |
| uncertainty/unknown | 常用同一不知道模板 | 不确定事实、未来结果、模型技术失败分别处理 |
| factual explanation | Wiki路径多能收敛证据 | 保留自然表达，不编why/motive |
| short/awkward | 有短接话，指标无完整真人金标 | 允许短停顿，不每次追问和附带建议 |
| long/deep/story | 词数单次波动大；明确虚构故事允许创作 | 长度按需求、现实回忆与虚构故事分开 |

Behavior Retrieval **EXPERIMENT_FIRST**：在现有StyleExample中加可信scene/speech_act/interaction outcome标签并先做简单filter/rerank即可。没有证据支持新建VoiceStore、BehaviorStore和DialoguePatternStore。现在检索已经使用部分行为metadata，主要问题是标签错，不能用新abstraction补坏数据。

## Style RAG / Retrieval Quality

`StyleSearchTool.search`取原始user message，规则 `label_style(message, "")` 得scene/speech_act，加planner.response_mode进入embedding query；**不使用standalone_query**，不读取真实relationship/mood；target_length只参与候选加分。候选池dense检索后：

```text
score = .55*dense + .20*quality + .15*authenticity
      + .12*scene_match + .08*mode_match + .05*length_match
```

不是只靠semantic；没有独立speech_act精排、relationship/tone/diversity评分、reviewed/source_type硬门禁。质量与真实性分未经校准；即使加权更大也不能修错误标签。

检索抽样37queries/111examples：scene89.2%、mode56.8%、length79.3%（全部按同一启发式标签计算），79个unique、最常用ID1330出现5次、top3字面重复0组。authenticity仅source标记不可信；所有relationship同audience，不能验证relationship匹配；speech_act没有独立金标，不能以embedding query含该词就宣称命中率良好。多样性目前有覆盖，不推荐复杂MMR/LTR。

所有7条greeting标签输入都不是普通问候：1123“眼睛看不见可以换吗”、1180“你好久没播了”、1384/1388混合问答、1583“什么时候复习啊”、1663“这下要被13岁的自己打败了”、1717“不然呢!!!”。标签在prompt+response整体匹配“你好”等子串，response“你好生气”也会触发greeting。于是“晚上好”的top3返回1717惩罚打手手、1180牌子变灰、1663长段唱歌收尾。**同标签匹配不代表同场景。**

comfort示例1051失恋找女主播尚有局部关系，1452长段宵宫话题、1728“那你真棒！大家五一都去哪玩了呢”并不适合“委屈，先别给建议”。这个问题可先由source清理、user侧标签、人工审查少量高频场景解决。

Style作为facts污染风险已在普通生成出现；尚未逐字归因某条参考。Style内instruction leakage有原始说话轮和指令句可进入system，但本次未建立污染示例注入成功率。Synthetic压过real当前不发生（synthetic=0），代码将来接纳synthetic时则无硬过滤保护。长example可能带入现场设定，应限制示范跨度，并按场景盲测，不能全按字数截断破坏对话语义。

## Real vs Synthetic / Coverage

当前真实标签统计casual608、question_answer185、teasing62、praise17、comfort7、greeting7；标签不准且没有approved字段。没有可运行synthetic生成管线，real+synthetic实验 **NOT_EXECUTED：无synthetic数据**，不为满足对照临时灌入未审样本。

| 场景 | real tagged | synthetic | approved | fixed eval cases |
|---|---:|---:|---|---|
| greeting | 7（全部误标普通问候） | 0 | 未测 | greeting |
| casual chat | 608 | 0 | 未测 | casual_chat |
| factual answer | 未独立标；question_answer185不等于事实 | 0 | 未测 | known_fact/ambiguous_fact/mixed_fact |
| follow-up factual | 未标 | 0 | 未测 | followup_factual |
| being praised | 17 | 0 | 未测 | being_praised |
| being teased | 62 | 0 | 未测 | being_teased |
| comforting | 7 | 0 | 未测 | comforting |
| user upset | 未标 | 0 | 未测 | user_upset |
| user excited | 未标 | 0 | 未测 | user_excited |
| user gives short reply | 未标 | 0 | 未测 | short_reply |
| awkward silence | 未标 | 0 | 未测 | awkward_silence |
| deep conversation | 未标 | 0 | 未测 | deep_conversation |
| self-deprecation | 未标 | 0 | 未测 | self_deprecation |
| disagreement | 未标 | 0 | 未测 | disagreement/temporary_tension |
| uncertainty | 未标 | 0 | 未测 | uncertainty |
| unknown fact | 未标 | 0 | 未测 | unknown_fact |
| nostalgia | 未标 | 0 | 未测 | nostalgia |
| relationship callback | 未标 | 0 | 未测 | familiar/warm_interaction |
| memory callback | 未标 | 0 | 未测 | recall/cross_session_recall/correction/conflict |
| late-night | 未标 | 0 | 未测 | late_night |
| playful | 未独立scene标 | 0 | 未测 | being_teased/user_excited |
| serious | 未独立scene标 | 0 | 未测 | user_upset/disagreement |
| storytelling | 未独立scene标 | 0 | 未测 | storytelling/long_reply |

同一case可跨维度，不能纵向相加为数据集大小。未标/未测不是库里绝无对应话语。最大缺口是**可信场景配对和审查状态**，其次是comfort、问候、纠正、分歧等互动的真实覆盖，而非先凑更多泛闲聊。

建议source tiers：A经源核验原对话；B保留span和变换记录的清理/重建；C有源支持、明确合成的teacher样例；D纯合成扩场景。A/B经review可进Style；C先离线coverage和候选SFT/偏好审查，事实片段仍需Wiki来源；D限压力eval/负例，不默认进runtime。当前 `real` 行未经审核不能自动升级Tier A。训练导出记录tier及split，Style source永不升级Fact evidence。

## Scene / Relationship / Memory Interaction

Identity稳定，trait概率性，relationship慢变，scene/mood短变，response_mode单轮约束，Style单轮参考：概念上合理。代码把relationship/scene全部直接渲染，输入边界和更新节奏不够合理。

| 字段 | 真实使用 | 稳定性/价值判断 |
|---|---|---|
| familiarity | 每轮+.01，无需关系证据 | 确定性100轮诊断90轮达到1；轮数不应等于亲密 |
| warmth | 正词+.01，“不喜欢”也匹配喜欢 | 20轮.7、50轮1（规定诊断输入）；否定识别不足 |
| teasing | 哈哈等+.015 | 20轮.5、50轮.95；笑声不等于永久逗弄许可 |
| trust | 写入任意memory时增加 | 事实记忆存在不等于信任事件，需解耦 |
| current_topic | user前60字符，下一轮使用 | 易带指令/过期内容；当前user已提供话题 |
| mood | 规则情绪后可残留supportive | 中性换话题不一定解除，存在场景滞后 |
| emotional_context | user情绪片段前80字符 | 来源可解释，但不该当高优先级要求 |
| energy / tempo | 多数重置 .5/normal | 已输出字段但无已测净收益，暂停扩展 |
| unresolved_threads | 事件候选，完成不自动关闭 | 增加不自然callbacks；需生命周期 |

上述数值是给定脚本输入的state模拟，不是100轮真实关系体验。D/E只给冻结状态、非重复对照；**没有证据证明当前复杂度值得保留或必须删除**。先以默认常量/禁用注入对照，优先去冗余，避免引入emotion engine。

Memory真正增强称呼和偏好回忆，但混入false-memory：regex把问句“还记得我们一起去上海吗”当shared_event；source_message_ids只能证明用户说过，不能证明发生。负偏好与正偏好不同key，纠正未替代；“已经考完”未关闭事件。用户可用“记住……”使任意指令进入高优先级context。Inspector改内容沿用旧来源也不代表新内容获得来源支持。

scene和summary会保存同一错误信息，即使Memory soft delete，旧history仍可影响回复；“删除memory记录”与“从全部会话抹除事实”应明确区分。当前原DB无历史，不建议凭规则破坏性清空；迁移先预览可疑记录和原因，标记unverified，不默默删用户数据。

## Validator / Grounding / Persona Drift

在线StyleValidator的优势是确定性、小延迟、保护URL/代码/引用，**KEEP AS IS**作为格式层。它不是语义判官，也不应成为第二Responder。当前没有有效的重复口癖控制、客服腔/现实自述检测；多条F失败经过normalize照常返回。

在线适合补结构空输出/损坏输出状态、有限格式/重复检测；语义fidelity、grounding、行为判断放离线rubric和发布门禁。空provider内容不应伪装“角色不知道”，技术失败应保留结构化error/retry状态，重试仍由同一Responder承担，限制次数。

grounding要求区分：Wiki已知日期可用第一人称转述；心理原因未知必须保留未知；明确虚构猫店故事可创作；普通寒暄不能编此刻吃饭/嗓子；记不清共同现实经历不是安全替代拒绝。人物Persona中的自然互动不需要虚构现实身体。漂移风险依次来自错配示例、错误memory/history、指令诱导、state迎合与口癖放大。

## Persona Token Budget / Value per Token

Qwen本地tokenizer代理，不代表远端收费。各块不含全部wrapper；core108、voice124、boundaries203，behavior casual107/factual121/emotional106/playful97/story96，casual永久基础合计542。default relationship52、scene25，实际有header/动态字段时D→E平均99。见 [diagnostics.json](audit_artifacts/phase6/diagnostics.json)、[token_blocks.json](audit_artifacts/phase6/token_blocks.json)。

| 块 | 成本代理 | 已测贡献与判断 |
|---|---|---|
| Core | 108 | 保持单一身份契约，独立身份增益未测；KEEP |
| Voice | 124 | B相对A格式/词频改变，与Behavior同改无法单独归因 |
| Behavior | 96–121 | 条件化有工程价值；不要再叠同义规则 |
| Boundaries | 203 | 原则必要但仍漏普通自述；加强数据和eval，不先增字数 |
| Style | B→C约205.5 | 变短/少憨憨，可信fidelity净增益未证实；先修质量 |
| Memory | C→D平均5.9（仅4/37有fixture） | 平均掩盖受影响case成本；真实回忆有用但纠正失败 |
| Relationship/Scene | D→E约99 | 净收益未知；先禁用/简化对照 |
| Summary | 随回合增加，生产最多800字符策略 | 已触发但只是截断转录；未独立消融，不给ROI分数 |

每轮token账本已落盘，可按case对比，不能把不同块单位成本当可相加的因果收益。60k字符诊断无裁剪，最大风险是context没有总预算；不需要发明复杂DSL，只需按输入能力分配额度、记录droppped理由、保留事实来源与纠正优先级。

## External Project Comparison / ADOPT–ADAPT–REJECT

以下为2026-09-05读取的官方文档/仓库；抓取状态与本地原文在 [external/sources.json](audit_artifacts/phase6/external/sources.json)。网站搜索受限后直接读取官方页面，11个来源均HTTP200。外部结果只说明设计可借鉴，不证明能提高Hanser指标。

| 项目 / 一手来源 | 当前问题 → 借鉴及适配 | Expected improvement / Validation | Cost / Risk | 决策 |
|---|---|---|---|---|
| [SillyTavern Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/) | permanent character与dialogue examples区别 → 保留小Core、示例按需注入 | 控制固定token；冻结context比较，不牺牲fidelity | 低；误把虚构角色自传照搬真人 | ADAPT / P2 |
| [SillyTavern World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/) | 全context无预算 → 借鉴按需激活和预算意识 | 超窗不丢核心边界，有明确drop ledger | 中；关键词误触发 | ADAPT预算；REJECT巨大lorebook |
| [RisuAI](https://github.com/kwaroran/RisuAI) | RP交互有示例/角色编辑需求，但当前无插件瓶颈 → 参考配置可检查性即可 | 能复核某次Persona配置；无需迁移UI/runtime | 低参考；整体迁移成本高 | ADAPT可检查性；REJECT框架照搬 |
| [ChatHaruhi](https://github.com/LC1332/Chat-Haruhi-Suzumiya) | 场景反应靠泛Core → 真实对话检索示范 | 以clean source与blind B/C检验反应，不仅词汇 | 中数据成本；相邻场景自述泄漏 | ADAPT现有Style，P1 |
| [Zero-Haruhi](https://github.com/LC1332/Zero-Haruhi) | 数据来源与角色材料整理 → source-grounded生成作为离线补充 | 缺场景覆盖经review才提高 | 中；合成事实/说话人不真 | ADAPT离线source tiers；DEFER生成 |
| [CoSER](https://github.com/Neph0s/CoSER) | 像词汇不等于像行为 → 情境化表演评估、分开语言/反应 | 相同user情境看reaction；固定场景盲评 | 低eval成本；小说人物不等于现实人物 | ADAPT rubric / P1 |
| [HER](https://github.com/cydu24/HER) | 自然互动与情绪回应需要单独评价 → 借鉴human-like interaction评价方向 | 测先回应情绪、适当好奇、少机械建议 | 低eval/高训练成本；不能把推理结构等同收益 | ADAPT指标；DEFER双层推理与训练 |
| [InCharacter](https://github.com/Neph0s/InCharacter) | 单句voice不能覆盖人格持续性 → 情境访谈一致性评估 | 分歧/亲近时核心态度一致；不当心理诊断 | 低；人格分数≠本人voice | ADAPT定性探针 / EXPERIMENT |
| [RolePlayBench](https://github.com/MiuLab/RolePlayBench) | judge识别名字后可能凭印象评分 → anonymized roleplay evaluation | 候选来源匿名、换序；减角色知名度偏差 | 低；匿名会移除必要身份上下文 | ADAPT盲评 / P1 |
| [TwinBench](https://github.com/TwinVoice/TwinBench) | Memory“看起来记得”掩盖错记 → beliefs/memory/relationship及多场景评价 | recall与false recall分别测、纠正后复测 | 中eval；不能把其角色数据混入Hanser库 | ADAPT维度 / P1 |
| [Letta Stateful Agents](https://docs.letta.com/v1-sdk/concepts/stateful-agents) | 长期数据、近期history、固定persona职责混杂 → 借鉴可检查的分层记忆及生命周期 | source/status/范围可追踪、跨会话纠正生效 | 中；自编辑高优先级memory可能放大污染 | ADAPT生命周期；REJECT整框架迁移 |

优先采纳的是已有系统中可落地的数据审查、盲评、分层边界和预算，而非外部系统的规模。不要引入自由生成隐藏心理状态、通用插件、大型lorebook或多Agent组织来解决转录错配。

## Recommended Persona Improvements

完整工程slice与统一决策表见 [Engineering Guide](NEXT_STAGE_ENGINEERING_GUIDE.md)。以下按证据强度排列，不预先修改production。

| 提案 | Problem / Evidence | Impact / Proposed solution | Gain / Measure | Cost / Risk | Priority |
|---|---|---|---|---|---|
| 清理并审核高频Style | 9/50结构缺陷；greeting7条误标 | 重建source span、user侧场景、review eligibility，回放来源 | 少错反应/转录残留；同集检索人工相关性+blind B/C | 中数据/低代码；召回减少 | P1 DO_NOW |
| Memory断言/纠正 | shared_event问句入库；跨会话咖啡冲突 | 原子偏好、拒问句/否定误写、来源级别、完成与修订 | 不编共同历史；write/correction/跨会话安全gate | 中；漏记 | P0 DO_NOW |
| 语义发布验收 | judge换序一致37.5%；客服检测0%却有反例 | 小批分层真人盲评，候选顺序反转，grounding单列 | 能判提升与回归；case-cluster delta | 低代码/中评审；主观差异 | P1 DO_NOW |
| 编译/预算边界 | style_rules空转，60k字符不裁剪 | 单一规则权威、hash、简单budget与数据区 | 可复现、少上下文挤压；超窗和渲染快照 | 中；截断坏证据 | P1 DO_NEXT |
| 行为标签精排 | comfort/greeting错场景 | 先修标签，再现有store加speech-act约束对照 | 改善reaction；scene-stratified pairwise | 低中；过滤过严 | EXPERIMENT |
| 简化动态state | 自动关系上涨、前轮scene | 常量/禁用 vs受控state，限制更新证据 | 少迎合、少无益token；callback与drift对比 | 低；熟悉感下降 | EXPERIMENT |
| MMR / temporal weighting / 新Store | 当前top3无字面重复、年代差异受转录混杂 | 暂缓，保留诊断，先做便宜去重 | 未证明净收益，不增加runtime成本 | 省成本；未来可能需再评估 | DEFER |

Persona最值得保留的不是每一个现成字段，而是小Core、同一Responder和可检验的输入边界。现阶段结论为“局部架构补正＋数据与验收优先”，没有重大重构理由。

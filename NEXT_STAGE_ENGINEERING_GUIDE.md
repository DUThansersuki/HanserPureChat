# Next Stage Engineering Guide

2026-09-05。**No major architecture change recommended.** 先补 Phase 5/6 的可信数据和正确性验收，再做受控 Responder benchmark。当前最值得做的三件事：修Memory断言/纠正；清理Persona配对/场景；建立能判断改动是否有效的盲评与grounding门禁。

这是后续实现指南，本次未修改production behavior。证据ID F01–F10见 [Current Audit](CURRENT_IMPLEMENTATION_AUDIT.md)，行为与计量见 [Evaluation](CURRENT_SYSTEM_EVALUATION.md)，Persona方案见 [Deep Review](PERSONA_SYSTEM_DEEP_REVIEW.md)。成本是工程范围估计，不是工期承诺。

## Recommended Architecture Delta

保留现有modular monolith、SQLite、统一Responder、专用Planner/Embedding/Reranker、Fact/Style/Memory分离、轻量Compiler、确定性格式器。不改变20条原则，不生成新Full Spec。

只修四类已有边界：Memory断言与生命周期；Style来源与runtime eligibility；Context token/数据边界；模型/索引/请求生命周期和可观察性。前两项收益已有失败样本支持，后两项是代码风险及实际失败支持。可以在现有模块内完成，无新framework、Store群或第二Responder。

## Immediate Work：接下来两个 implementation slice

### Slice 1 — Memory assertions, correction and lifecycle（P0 DO_NOW）

**Goal / Problem / Why now：** 阻止错误持久化，恢复纠正后的可信回忆。F01问句入shared_event、正负咖啡并存、跨会话责怪用户；这不是prompt能修的，因为写入是确定性regex，回答已经拒绝仍照写。预期收益是减少false continuity且保留已证实昵称/偏好。

**Dependencies / Files：** `memory/extractor.py`、`store.py`、`pipeline.py`、`retriever.py`、`summarizer.py`，`models.py`、`db.py`，相关memory/API tests。无新模型依赖。

**Data work：** 先导出审查预览，按asserted user fact / hypothetical / question / negated / corrected / confirmed conversation event分类。source_message_id可追到实际角色/文本/时间；旧shared_event低可信标unverified，不自动物理删除。原始用户断言可保留为“用户声称”，不得重写成系统确认的共同现实经历。

**Code work：**

- Gate先识别疑问、条件、否定和修订；未证实共享现实经历拒写为verified episode。“记住”仅表示意愿，不提升真实性或指令优先级。
- 偏好用同一subject/predicate/object及polarity/validity管理，正负咖啡属于同一冲突域；最新明确修订supersede旧值，保留来源和revision。不要把所有饮料设为只能喜欢一个；允许同时喜欢茶和咖啡，只有明确否定才替代对应断言。
- 事件完成/取消关闭unresolved；删除/修訂影响检索资格。检索先限制user+active+model，再做top-k，避免全局20候选饥饿。
- Inspector改写标记human/user edit来源与原记录关系；不能沿用原source假装新内容被原文支持。全None patch返回验证错误。
- summary识别已修订范围，优先最近明确纠正，历史对话仍可保留但不得当当前偏好；删除记录和删除全部历史是不同操作。

**Tests / Eval：** 原7探针中memory/候选饥饿/null patch；加“我喜欢咖啡也喜欢茶”“我不是不喜欢咖啡”“如果我叫小林”“别记住这句话”、问句/quoted memory/source不存在/完成事件；两组20轮+跨会话。比较false-write、corrected-recall、source coverage和用户正事实漏记；不只匹配固定上海字符串。

**Migration / Rollback：** 编号SQLite迁移；先备份、dry-run受影响行预览。增字段兼容读，保留旧内容和status，source linkage只增不伪造。索引随新资格重新生成。回滚代码＋对应DB备份/旧索引代次，旧错误记录不得未经提示重新当verified召回。

**Complexity / Risk：** MEDIUM，迁移与否定误判中风险；可能更保守漏记。**Done criteria：** 当前真实纠正和假记忆失败消失；跨会话只使用有效偏好；原正向昵称/用户事实不回归；所有写入有可核查assertion来源；拒写原因可检查。关系情绪引擎不在此slice。

### Slice 2 — Reviewed Persona corpus and acceptance（P1 DO_NOW）

**Goal / Problem / Why now：** 提高角色反应的来源可信度，能判断Style改动有没有用。F02：50样本9结构错配，7greeting标签全非普通问候；Judge换序一致只有37.5%。先修数据和评测，再调ranking；不需要架构重写。

**Dependencies / Files：** `persona/data_pipeline.py`、`agent/tools/style_search.py`、StyleExample schema/DB、离线导出/索引脚本、`phase6_*` eval。可与Slice1按文件隔离推进，验收仍独立。

**Data work：** 先回源修9个明确样本及37个regex候选；修全部greeting/comfort高频误配；提取稳定source span、speaker、user turn、response turn、清理操作。增加review status与source tier。对新生成的draft抽样验收，旧886行不能自动标approved。源量少场景先搜真实材料，无法找到就保留coverage缺口。

**Code work：** 支持不同bullet/括号/说话人格式，遇不确定多说话人隔离；scene优先根据user speech act，不把answer里的“你好生气”当问候。查询仅使用eligible source，保留同一Style collection与简单weighted scoring。Quality score不是认证，reviewed/source_type是独立条件；保留原始source文本，runtime只用干净完整turn。

**Tests：** 真实坏格式小fixture覆盖speaker切换、混合bullet、时间戳、答案吞入prompt；source span回放一致；含synthetic/unknown source的负例；不要写只有real输入却宣称real-only过滤的测试。

**Eval：** 原37题保持固定，重跑B/C及top-k；独立评审scene/speech-act相关性、source正确性；按问候/安慰/分歧/夸奖分层做真人blind pairwise，grounding单列。不能仅以回复更短、标点更少为胜出。补小批真实reviewed负/纠正示例用于评测，不立即拿去训练。

**Migration / Rollback：** 新review/schema字段和source-span映射；构建候选style index代次后完整性检查，切换前保存旧DB/索引。回滚选择旧代次；错误样例的隔离清单独立保留。

**Complexity / Risk：** LOW–MEDIUM代码、MEDIUM数据；过滤后可用样例减少。**Done criteria：** 抽到的明确缺陷已修或隔离，问候检索不再由错误标签支撑，人工review状态真实可追查，独立盲评足以裁定候选；如果fidelity区间跨0，不声称提升，继续保留可回滚候选。

## Near-term Work

### Slice 3 — Context contract and prompt identity（P1 DO_NEXT）

**Goal/Problem/Evidence：** 60k字符输入无裁剪、config4096未约束、style_rules空转、state原文进system（F04/F08）。预期减少溢出和不可解释输入，确保prompt变化可复现。只调prompt长度不能保证任意组合不过窗，因此需要最小budget contract。

**Files/Code：** `agent/context_builder.py`、`persona/compiler.py`/`schemas.py`、`config.py`、`model_gateway.py`、provider adapters。选一处为style规则权威；render hash包含源文件与compiler版本。输入数据区显式类型/source，State白名单与转义，不让user原话成为Persona规则。把可用输入预算与输出预留、provider能力配置区分；可靠tokenizer可用则实算，未知provider用保守可追溯估计。

**Budget order：** 保留core/boundaries/current request；事实题保留最小足够evidence及citation IDs；优先当前纠正/有效memory；再近期history，裁旧摘要、低相关style、冗余state。不可截半条来源或把否定截掉，必要时减少完整条目。记录每块token、drop reason，不硬编码所有模型4096。

**Data/Dependencies：** 使用现有frozen contexts与超窗fixtures，无新corpus。依赖Slice1纠正语义，source tiers规则与Slice2一致。

**Tests/Eval：** 单个大user输入、evidence+memory+summary共同挤压、多语言/代码、否定和引用保留；固定Persona/grounding无新增失败；token账本等于发出payload。**Migration/Rollback：** 无需迁库，能力配置兼容旧字段，切换旧builder需保留trace，不能静默截断。**Cost/Risk：** MEDIUM/MEDIUM。**Done：** 每次构建都有预算与来源保留策略，style规则改动确实反映到snapshot，超窗行为有显式结果。

### Slice 4 — Request and model failure boundaries（P1 DO_NEXT）

**Goal/Problem/Evidence：** 空content变不知道（F10），reranker异常整个请求失败，post-turn在持久化后仍能使HTTP失败，最大请求102.84s（F03，根因混杂）。改善错误可见性和聊天节奏；prompt不能修资源交接或事务顺序。

**Files/Code：** `responder/service.py`、`model_gateway.py`、`agent/service.py`、`agent/tools/wiki_search.py`、`retrieval/reranker.py`、`memory/pipeline.py`、`api.py`。空输出结构化失败/限次同Responder重试，不替换成事实unknown；reranker失败可返回已召回候选+degraded metadata，dense失败按已验证BM25回退。保留明确失败来源，不把故障算成功quality样本。先记录post-turn写入/索引失败并可重试，不直接迁到复杂队列。

**Resource experiment before parameters：** 单独占用6GB GPU，冷/热分离，Wiki/闲聊交替20轮，连续采样VRAM，测planner load、reranker load、post-turn与total。比较明确释放reranker/保留planner等最小策略；不用本次争用下最大值断言具体策略必赢。选稳妥profile后才改默认。

**Tests/Eval：** provider200空content/timeout、reranker抛错、embedding失败后重试、已保存reply后post-turn失败、fallback证据source不丢；同一请求幂等；真实交替测量。**Data/Deps：** 现有case，无新语料；需trace字段。**Migration/Rollback：** 结构化状态向后兼容，先保留原API字段；资源策略配置可恢复，失败记录不删除。**Cost/Risk：** MEDIUM/MEDIUM；回退会降低排序质量，必须显式标记。**Done：** 无静默伪正常空回复；故障结果可定位；目标机器稳定性有独占实验支持。

### Slice 5 — Ownership and index lifecycle（P1 DO_NEXT）

**Goal/Problem/Evidence：** F05会话owner可改；F06另一store重建后缓存仍旧。当前local单用户降低暴露，但不消除同ID错误读取。预期数据一致、召回不陈旧；不是加入微服务或全套权限系统的理由。

**Files/Code：** `agent/conversation.py`、`api.py`、`retrieval/vector_store.py`/`indexer.py`、`db.py`、Style indexer。读取history前校验owner，既有owner不可由append改写；Memory先按user/status筛选。索引带source revision、embedding model/dimension/config；批次完整后原子发布，进程cache按revision失效或明确维护窗口重启。删除关联chunk_tokens，不留下悬空BM25项。

**Data/Tests/Eval：** 双user同conv拒绝；同user续聊正常；新建/改文/删文/改model维度/重建中断/跨进程cache；查询新doc可达、已删除不可达、计数一致。已有副本足够，禁止以原DB做破坏性注入。

**Dependencies/Migration/Rollback：** 小型schema版本，先备份和旧索引代次；owner缺失行预览，不猜所有者。索引发布失败继续旧完整代次。回滚需要匹配DB/source revision，不混新旧vectors。**Cost/Risk：** MEDIUM/MEDIUM；旧客户端随意换user可能开始被拒绝。**Done：** 所有owner读取边界覆盖，故障注入不发布半成品，cache可证明一致。

## Experiments Before Implementation

| 实验 | Problem / Evidence / Expected gain | 控制变量 / 测量 | 成本 / 风险 / 决策 |
|---|---|---|---|
| Behavior-aware ranking | greeting/comfort错反应；期待比词面相似更贴场景 | 先clean source，same37+新增holdout，现有加权vs加speech-act filter；独立相关性/盲评 | LOW–MEDIUM；过滤损召回；EXPERIMENT |
| top-k 0/1/2/4/6 | 增token未证净收益；当前10题无法择优 | 相同source/plan/state，重复采样；paired grounding/fidelity、token | LOW；小集过拟合；EXPERIMENT，不直接改默认 |
| relationship disabled/enabled | 自动数值上涨；D/E净收益未证 | 分开禁relationship、禁scene、两者禁；同history与source，测试逗弄许可/熟悉callback | LOW；熟悉感减弱；EXPERIMENT |
| current scene vs previous scene | 状态落一轮；期待少情绪错位 | 同一pipeline仅改场景时机/白名单，emotion→neutral→playful | LOW；误读当前user；EXPERIMENT |
| 更小prompt B′ | 重复约束/dead rules；期待更易维护、少token | snapshot可追溯；同model/frozen evidence；不同时改corpus | LOW；边界退化；EXPERIMENT |
| harder Fact set | 12题BM25已满分；复杂路径收益未辨 | 同query，无oracle关键词与有关键词分报；日期/简称/多跳/why无依据 | MEDIUM标注；gold偏差；DO_NEXT eval |

每个实验保存source/case版本、raw/final、失败状态；采用Regression Suite delta门禁。有质量区间跨零或可靠性不足的结果，保持当前可回滚默认，不凭模型名或单题样例替换。

## Phase 7 Reassessment

**可以准备并做小规模受控benchmark；暂不进入大规模模型选型竞赛。** 本次已经冻结37题输入，新增 `phase6_replay.py` dry-run验证，足以开展单变量Responder对照。先修确定性Memory错误、清理关键Style、建立可信quality判定，否则挑出的模型只是更会掩盖错误数据。

固定test set、Persona、Wiki evidence、Style、Memory、history、Context和validator；只改变model/quantization/sampling，每个变化单独记账。当前外部model、候选local profile无需为模型名字增新架构。baseline与candidate同case多次采样、匿名评分；记录fidelity、naturalness、grounding、repetition、稳定性、context能力、warm/cold latency、true token及连续VRAM。context能力使用独立长度阶梯，不允许偷偷减少某模型的证据后称公平比较。

本次未横评本地RP候选，也未证明当前外部Responder最优。先通过slice2的评审可靠性，再利用冻结重放做2–3个实际可用profile的pilot，选质量/硬件Pareto范围；量化差异与模型差异分开。旧Prometheus对照先修filename去重和chunk丢失问题，仅保留offline用途。

## LoRA Readiness / Training Roadmap

**NOT_READY。** 886行real标签不是886条干净训练对，随机样本有明确错配，关键场景标签错；没有source-level split、认证baseline、model benchmark或SFT格式/负纠正样本验收。

| Gate | 当前证据 / 状态 | 完成要求 |
|---|---|---|
| Real quantity | 886候选，不是可训净量 | 报去重/审核后数量与场景分布，量随质量需求决定，不捏造最低万条 |
| Source / Speaker | 50抽9结构缺陷，无音视频认证 | 对训练候选有可靠源span和speaker审核 |
| Duplicate | response重复16；词面near screen0，不是语义0 | 同source/近邻模板分组、train/eval隔离 |
| Coverage | casual集中，comfort/greet标签弱 | 优先真实少数场景，明确缺口与holdout |
| Synthetic ratio | 0，无teacher pipeline | 保持tier，合成不进facts，不拿纯合成自证本人fidelity |
| Leakage | 无group split | 按source/episode切分，评测不反哺训练答案 |
| Baseline / Style | 已执行但Style净收益未证、judge弱 | 独立盲评＋已验收quality baseline |
| Model benchmark | 未横评 | 先选可部署基座及量化方案 |
| SFT / Negative | 无正式导出/纠正集 | 格式验证、错误现实自述/客服腔负例、纠正对照 |

顺序：清理真实数据→固定held-out→受控model baseline→小型SFT pilot（仅措辞/节奏/反应/持续性）→同37+未见场景+多轮grounding检验→必要时再偏好训练。Wiki facts依然检索，不塞LoRA；memory错写、坏context与speaker错配先工程修正。

DPO/ORPO **DEFER**：当前客服腔/过度可爱/无据自述说明未来偏好数据可能有价值，但当前33条不可靠judge结果不应直接作chosen/rejected。先人工审核负例和reference，再比较prompt/data改善后的剩余错误；只有基座会做却持续选错、pairwise足够可靠，才比较SFT后加preference的增益。不要为了使用训练方法而提前积累带噪偏好对。

## Deferred / Rejected

- **DEFER**：大规模LoRA/Preference训练、temporal weighting、复杂MMR/LTR、独立Behavior Store、全UI重做。当前数据/质量判定先决条件未满足；保持实验材料，不立即开发。
- **KEEP AS IS**：SQLite、直接工具依赖、modular monolith、统一Responder、模型provider隔离、确定性normalizer、Fact/Style分库逻辑。已有功能，不为对齐Spec目录增层。
- **REJECT**：LangChain/LangGraph迁移、微服务、多Agent编排、通用plugin framework、event sourcing、巨型lorebook、情绪状态机、每轮多LLM Judge。这些不能解决已观测错误，迁移成本/回归面大且无预期可测净收益。

## Final Decision Matrix

Persona相关在前；预期收益为待验收目标，不是已实现提升。

| Item | Current | Problem | Evidence | Proposed action | Expected gain | Cost | Risk | Priority |
|---|---|---|---|---|---|---|---|---|
| Persona corpus | 886 real标签 | 错speaker/turn/scene | 9/50、greeting7误标 | Slice2 source review与资格门禁 | 真实且匹配的反应示范 | 中数据 | 可用样例减少 | P1 |
| Persona eval | fixed37+弱judge | 无可信增益判断 | 换序3/8一致 | 分层独立盲评、grounding单列 | 可靠回归决策 | 低代码/中评审 | 主观分歧 | P1 |
| Style ranking | dense+metadata | metadata不可信 | comfort/greeting错场景 | 先清数据，再speech-act实验 | 反应更贴场景 | 低中 | 过严过滤 | EXPERIMENT |
| Core/Compiler | condition-aware | dead规则、固定版本 | style_rules未render | 单一规则权威、hash | 可复现、少空转 | 低 | prompt回归 | P2 |
| Relationship/Scene | 数字全注入 | 增长快、前轮scene | 100轮state诊断+D/E不确定 | 禁用/简化对照 | 少迎合与无益token | 低 | 熟悉感损失 | EXPERIMENT |
| Memory×Persona | 可持久化 | 假共同历史、纠正失败 | 真实跨会话+probes | Slice1断言/纠正/生命周期 | 可信continuity | 中 | 漏记 | P0 |
| Empty output | unknown兜底 | 技术失败伪装角色反应 | mixed5空content | 同Responder限次重试/结构化失败 | 不再答非所问 | 低 | API兼容 | P1 |
| Context | 字符统计无预算 | 长输入无限拼接 | 60k诊断 | Slice3预算+数据边界 | 稳定上下文 | 中 | 截断信息 | P1 |
| Resource/fallback | 单向释放 | 冷/热争用、故障不降级 | runtime+抛错probe | Slice4独占实验/故障状态 | 节奏和可靠性 | 中 | 回退质量 | P1 |
| Ownership/index | 全局cache/owner可改 | 串历史/旧索引 | 双user/cache probes | Slice5校验/revision | 一致性 | 中 | 旧客户端行为变化 | P1 |
| Retrieval complexity | Hybrid+rerank | 优势未证 | 12题BM25同满分 | 保留，先困难集对照 | 避免无益默认变更 | 中标注 | easy-set偏差 | P2 |
| Responder benchmark | 输入可冻结 | quality判定弱 | 同模型judge不足 | 先pilot，后扩大 | 可部署质量比较 | 中 | 数据问题掩盖 | DO_NEXT |
| LoRA/preferences | 未建立训练集 | 数据/评审门未过 | readiness table | 暂缓训练 | 避免固化缺陷 | 节省训练成本 | 延后风格收益 | DEFER |
| 核心架构 | 单体/统一Responder | 无重大结构失败 | 40轮主链路 | KEEP AS IS | 限制回归范围 | 低 | 后续需再评 | KEEP |

## Answers to the 15 Required Questions

1. **实际完成到哪：** Phase1的Python统一路径已验；Phase2–4主功能运行但生命周期/故障/语义验收有缺口；Phase5/6需要返工验收，不能称Phase6完成。
2. **哪些只有代码或没达需求：** Phase0缺历史baseline/tag；Phase2无自动降级和公平旧方案胜出证据；Phase4更新/缓存不一致；Phase5来源与fidelity未验；Phase6 episode等枚举不代表抽取实现，纠正/false recall失败，relationship净收益未证。
3. **最大architecture weakness：** data信任与生命周期边界未闭合：用户句子可被提升为共享事实，错误值不会可靠替代，来源和存储状态不能约束未来生成。
4. **最大Persona weakness：** style示范的说话人/轮次/反应场景不可信，口语表面像却反应错，缺可靠fidelity门禁。
5. **Style RAG可测提升：** 测到长度/口癖变化和token增长；fidelity净提升未证实，C−B区间跨0且judge换序一致不足。
6. **Compiler价值：** 确定性common+mode选择有实际价值；仍主要轻量assembly，足够，不需新DSL；修dead规则和hash。
7. **Memory作用：** 同时有用和有害；称呼/偏好可回忆，但负偏好和虚假共同经历导致跨会话错误连续性，应先修语义正确性。
8. **Relationship/Scene值不值：** 现有复杂度净收益未证；有明确更新过快和滞后风险，先禁用/简化实验，不再扩字段。
9. **最影响像Hanser的三因素：** 可信真实场景示例不足；可爱词/憨憨放大代替reaction；当前model/prompt对身份与grounding诱导不稳（非模型独立归因）。
10. **最影响活人聊天三因素：** 错误memory/callback；情绪和社交意图失配（包括空输出兜底）；长尾等待与机械口癖。自然感不靠编本人身体经历。
11. **数据最大缺口：** reviewed speaker/turn/scene可信配对；其后是comfort/greeting/分歧/纠正等少数场景；不是先补海量泛闲聊。
12. **外部借鉴：** ChatHaruhi真实情境示例、CoSER情境行为rubric、RolePlayBench匿名评价、TwinBench记忆/关系区分、Letta生命周期和SillyTavern预算意识；均适配现有模块，详见Persona Review一手来源表。
13. **是否进入model benchmark：** 可做冻结上下文pilot；先修质量门，暂缓大规模选型，不按模型名字决定。
14. **是否准备LoRA：** NOT_READY。可以进行为未来训练服务的数据审查/split设计，但不开始训练或把现有886行直接导出SFT。
15. **只做三件事：** Memory断言纠正、Persona可信数据、独立盲评＋grounding回归门禁；之后才扩模型benchmark和训练。

## Decisions Required From Owner

None。当前审计与非侵入评测已完成；下一步可直接以Slice1/2作为独立实现任务输入。真人评审尚未执行是验收工作项，不需要现在为了完成审计再询问许可。后续涉及现存用户数据的修订迁移，先生成具体预览并在实现任务内处理，不预先要求泛泛批准。

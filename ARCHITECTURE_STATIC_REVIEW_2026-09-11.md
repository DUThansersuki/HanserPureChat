# 项目架构与逻辑静态审查

审查日期：2026-09-11。范围：持久化记忆/RAG、Chat/Voice/Live2D 协同、Persona。

本次只读取源代码、配置、设计文档与已有发布记录；没有启动项目、导入项目模块、调用模型、运行测试、查询运行服务、探测 GPU 或打开业务数据库。只新增本文，没有修改实现。下文的“确定”指代码路径可以推出的结果，不表示已运行复现。已有发布报告的数据按其记录日期引用，不代表本次重新验证。

## 1. 总体判断与完成性

架构方向合理，已有较完整的文本 Agent 主链，但整体尚不是完成的音画交互产品。应先修复状态与恢复链，再接真实音画资产；无需先拆更多服务或更换向量数据库。

| 板块 | 已有实现 | 完成性判断 |
|---|---|---|
| 文本 Agent | Planner、并发上下文工具、统一 Responder、校验、对话与回复快照原子保存、请求幂等、后处理失败记录 | 主链已成形，崩溃恢复与状态顺序仍有缺口 |
| 长期记忆 | SQLite 真值、来源消息、修订链、状态过滤、向量检索、昵称/表达偏好直读、摘要 | 基础持久化完整；长期连续性、编辑后索引与重试正确性未闭环 |
| 事实 RAG | BM25+dense、RRF、本地 reranker、来源引用、降级与向量代际 | 在线检索完整度较高；文档块与向量联合发布不完整 |
| Persona v2 | 人物包、来源与哈希、production lifecycle、信号、权限、行为约束、Style 白名单、验证与 trace | 已有文本发布记录；长会话权限与软目标实现存在偏差 |
| Voice | 独立 runtime、TTS adapter、分段、缓存、QA、时间线、任务状态、回执、网页播放器 | 接入骨架已实现；默认关闭，资产与真实模型验收未完成 |
| Live2D | timeline evaluator、RigAdapter 接口、参数 epoch 所有权、视觉计划接口 | 尚缺真实模型加载、具体 Cubism 适配器、网页渲染循环及播放时钟接线 |
| 离线渲染 | 帧计划生成、复用冻结音频/时间线的设计 | `renderer/README.md` 明确还需要浏览器逐帧捕获驱动，未形成完整导出闭环 |

当前 `backend/config.yml` 使用本地 Ollama Planner、本地 Embedding/Reranker、外部 API Responder；没有 performance 配置段，`PerformanceConfig` 的四个功能开关默认都是 false。Voice 与 rig 候选配置也都是 `enabled: false`，rig 的 `model_path` 为 null。资产 README 明确尚无已批准声音素材和 Live2D 模型。因此不能把协议、fixture 或历史纯文本测试通过解释为真实音画能力完成。

生产记录见 [Persona Text 发布说明](H:/HanserAgent/release/persona-v2-text-production-20260910.md)。其中记载 739 条发布 Style 行、416 条具备该 generation 向量，以及 50 轮中 4 轮粗口命中；本次没有查询数据库核实当前数量。发布记录也保留了 Judge 换位不一致等限制。

## 2. 应保留的架构

主链是：用户输入 → 历史/摘要/状态 → Planner → 本轮信号与 Persona policy → Wiki/Style/Memory → ContextBuilder → 唯一 Responder → 校验与显示转换 → 对话和 ReplySnapshot → post-turn。

音画消费者经 RenderBridge 读取已保存的 ReplySnapshot；AllowedPerformance 向 Voice 与 Visual 分别映射，再经分段音频和时间线供客户端消费。

这些边界值得保留：

- Wiki 为人物事实提供证据；Style 只提供表达示例；Memory 保存用户信息和可追溯互动。共享 Embedder 不等于共享事实权限。
- ContextBuilder 标明数据来源、转义内容、规定当前用户说法优先于冲突记忆，并且具备预算和裁剪记录。
- Persona 先确定语义边界，Responder 同次产生文本及可选表演意图；Voice 不再独立猜一套人格。
- 回复、assistant message ID 与 snapshot 在同一事务落库，语音失败不应撤销文字。
- Voice 单 worker、有限预生成、音频缓存与 generation epoch 都是合适的初期设计。
- 人物包在加载边界校验 manifest 是合理门禁，不建议因为减少哈希的偏好而删除。

## 3. 优先修复的确定性问题

### F1 / P1：摘要长度预算失效，并有窗口覆盖空档

证据：[summarizer.py:82](H:/HanserAgent/backend/hanser_agent/memory/summarizer.py:82)。`used` 累加却不参与判断，循环只比较单条 `cost` 和 `limit`。多条各自短于 800 字的消息会全部入选；末尾超长时直接 `return body`，也没有截断。因此 `summary_max_chars=800` 不是真正上限。

影响：摘要可能随历史增长；ContextBuilder 最后可能整块丢掉超预算摘要，Planner 则提前拿到它。长期上下文既变慢又不稳定。

此外，摘要每 12 条消息更新，但 recent history 每轮移动。以已保存 20 条消息为例，摘要覆盖索引 0–7，recent 覆盖 8–19；增加两条后摘要仍停在 7，recent 变为 10–21，索引 8–9 暂时不在两者任何一个中。直到下一次摘要更新才补回。

建议：用累计长度选择完整条目；按摘要游标补齐 recent 前的未摘要尾段，或每次推进覆盖游标。摘要压缩与“所有近期消息至少出现一次”的覆盖机制应分开。无需为此新增一个 LLM。

### F2 / P1：只编辑记忆 importance/confidence，会让该记忆退出向量召回

证据：[store.py:227](H:/HanserAgent/backend/hanser_agent/memory/store.py:227)、[api.py:454](H:/HanserAgent/backend/hanser_agent/api.py:454)、[store.py:175](H:/HanserAgent/backend/hanser_agent/memory/store.py:175)。

`update_memory` 总是 supersede 旧记录并建立新 ID，新行的 `embedding_ref=NULL`；API 只有在 `patch.content is not None` 时才建立新向量。只调整重要性或置信度，新行就永远不满足召回资格，旧行又已非 active。

建议：内容未改时复用旧向量并绑定新 ID，或为所有新 revision 安排索引。若允许通过 content 修改结构化昵称/权限，还要同步语义字段；当前实现保留旧 `object_value` 等字段，直接读取结构化值的消费者可能继续采用旧值。

### F3 / P1：post-turn 重试可能恢复旧记忆、覆盖新状态

证据：[service.py:571](H:/HanserAgent/backend/hanser_agent/agent/service.py:571)、[pipeline.py:66](H:/HanserAgent/backend/hanser_agent/memory/pipeline.py:66)、[store.py:29](H:/HanserAgent/backend/hanser_agent/memory/store.py:29)。

失败记录保留旧用户消息和 `previous_relationship/previous_scene`；重试重新执行整条流水线。记忆 upsert 按当前 active key 替换，不按来源消息时序判断。

具体路径：A 轮写入“喜欢咖啡”后索引失败；B 轮已成功纠正为“不喜欢咖啡”；再重试 A，会把 B supersede，旧偏好重新成为 active。旧 scene/relationship 也会再次覆盖当前状态。现有请求幂等不能保证这种跨轮重放正确。

建议：以来源消息/turn 序号去重并保留写入顺序；让索引失败只重试索引，不重新解释已经提交的记忆。状态提交按版本推进，旧任务不能回退新状态。同一 conversation 的生成顺序、同一 user 的共享状态顺序也需要明确；`append_turn` 的数据库事务只保护落库，不保护前面的上下文读取与生成顺序。

### F4 / P1：会话级和话题级 Persona 拒绝没有完整生命周期

证据：[signals.py:126](H:/HanserAgent/backend/hanser_agent/persona/signals.py:126)、[permissions.py:29](H:/HanserAgent/backend/hanser_agent/persona/permissions.py:29)、[extractor.py:54](H:/HanserAgent/backend/hanser_agent/memory/extractor.py:54)、[conversation.py:54](H:/HanserAgent/backend/hanser_agent/agent/conversation.py:54)。

运行时从最近 12 条消息重提取历史 permission events，长期记忆只存 `scope=user` 的事件。默认 `scope=conversation` 的“别开玩笑”等禁令离开历史窗口后就不再进入权限合并。摘要不会被解析为持久权限。

`current_topic` 事件在权限合并中也仅在其来源恰好为本轮消息时采用；同话题下一轮没有正式的持续/结束机制。

建议：单独持久化轻量权限事件或有效状态，带 user/conversation/topic 范围、来源序号和撤销规则。不要靠加长 prompt 历史维护硬拒绝，也不要把所有会话拒绝升级为用户永久偏好。

### F5 / P1（更新索引时）：词法块和 dense generation 不是联合发布

证据：[indexer.py:22](H:/HanserAgent/backend/hanser_agent/retrieval/indexer.py:22)、[indexer.py:99](H:/HanserAgent/backend/hanser_agent/retrieval/indexer.py:99)、[build_fact_index.py:29](H:/HanserAgent/backend/scripts/build_fact_index.py:29)。

构建先删除并提交全部 document_chunks/chunk_tokens，再构建并发布新向量。旧 dense generation 在构建期间仍 active，但其 chunk ID 已失效；`document_chunks.id` 为 AUTOINCREMENT，旧命中加载不到对应文本。若新向量构建失败，保留旧向量指针并不能恢复旧事实检索组合。hybrid 中无效 dense ID 还会占用融合候选名额。

建议：把 chunk、词法数据、向量绑定同一事实 generation，最后原子切换；初期也可明确采用停机维护，并在失败时恢复完整一致组合。现有向量代际是好的基础，但不能称为完整事实索引原子更新。记录 embedding 身份时也应包含影响向量的模型 revision/截断/指令等，当前构建 `config_hash` 只含 batch size。

### F6 / P1（启用 Voice 前）：失联客户端可无限占住唯一 worker

证据：[jobs.py:243](H:/HanserAgent/voice_runtime/jobs.py:243)、[jobs.py:280](H:/HanserAgent/voice_runtime/jobs.py:280)、[jobs.py:434](H:/HanserAgent/voice_runtime/jobs.py:434)。

交互任务默认只允许领先两个段；后续段等待播放回执。`_wait_for_capacity` 没有超时，deadline 检查在等待返回之后。用户关页、网络断开或回执失败，第三段可能一直等下去，所有后续任务排在唯一 worker 后面。回执客户端也未检查非 2xx 响应。

建议：对等待容量应用同一任务 deadline/消费租约，过期终结或暂停调度该任务并释放 worker。将失联等待与 GPU 推理占用分开。`asyncio.to_thread(synthesize)` 也不是硬取消，取消任务只能阻止迟到结果发布，不能立即停止正在执行的 GPU 推理；这应进入延迟与资源策略。

### F7 / P1（启用 Voice 前）：SSE 读取超时比服务端心跳短

证据：[render_bridge.py:36](H:/HanserAgent/backend/hanser_agent/render_bridge.py:36)、[config.py:105](H:/HanserAgent/backend/hanser_agent/config.py:105)、[jobs.py:175](H:/HanserAgent/voice_runtime/jobs.py:175)、[voice api.py:141](H:/HanserAgent/voice_runtime/api.py:141)。

Bridge 默认 HTTPX timeout 为 10 秒，并直接复用于事件流；Voice 无事件时最长等 15 秒再发送 keepalive。首段推理或段间生成超过 10 秒，事件流可能先发生 ReadTimeout。浏览器 `readVoiceEvents` 只有一次读取，没有重新取 snapshot/续接事件的恢复过程。

建议：区分普通 HTTP 请求与 SSE 的 read timeout，保证长于心跳；掉线后以 job snapshot 和事件序号恢复。恢复应与 F6 的消费期限一致。

### F8 / P2（启用 Voice 前）：播放器未正确接收已终结 job，打断有悬挂等待

证据：[playback-controller.ts:62](H:/HanserAgent/chatbot/lib/voice/playback-controller.ts:62)、[playback-controller.ts:135](H:/HanserAgent/chatbot/lib/voice/playback-controller.ts:135)、[playback-controller.ts:352](H:/HanserAgent/chatbot/lib/voice/playback-controller.ts:352)。

创建/幂等重放已 completed 的 job 时，播放器无条件 `terminal=false`，只加载 ready_segments，再从 `last_sequence` 之后订阅；终结事件已在该游标以前。播放完现有片段后会停在 BUFFERING。已 failed/cancelled 的快照也有类似状态初始化问题。

播放中 interrupt 会清空 onended 并停止 source，但没有 resolve `sourceWaiter`，旧 playSegment 的 Promise 悬挂。另在 BUFFERING 中 pause，后续段到达后 `ensureContextReady` 可把 PAUSED 改成 READY。

建议：从 job snapshot 初始化终态；统一中断时结清所有 waiter；暂停状态不能由音频就绪覆盖。优先补这些状态转换，不必重写整套播放器。

## 4. 记忆与 RAG 的能力边界和优化

### 4.1 记忆目前偏“规则识别的资料”，不是完整情节记忆

`MemoryCandidateExtractor` 主要识别名字、喜欢/不喜欢、长度偏好、限定事件与明确表达许可；“记住……”一般被 gate 拒绝，共同事件候选默认为 unverified 并拒绝。安全边界合理，但本次所查主链没有把已验证互动自动生产为 `confirmed_conversation_event` 的路径。

因此不能仅凭 Memory 类型中有 shared_event/episode 就认定共同经历已完成。建议从真实已保存的交互结果构造有来源的事件记录，不通过放宽用户自报历史的 gate 获得所谓记忆能力。规则中的高 confidence 是固定分值，不是校准后的语义可信度。

### 4.2 memory_scope 尚未贯穿召回和冲突键

`MemoryRetriever.search` 只传 user_id；`eligible_memory_ids` 没有 conversation/scope 条件，upsert 冲突键也只有 user/type/key。当前自动抽取大多默认 global，因此这首先是能力边界；一旦写入 conversation-scoped 数据，同一用户的其他会话仍可能召回或替换它。不是已确认的跨用户泄漏。

建议 scope 参与资格过滤和修订身份。昵称与明确用户级偏好仍可跨会话共享，临时事件与话题状态按所声明范围处理。

### 4.3 召回应区分“必须遵守”与“可能相关”

昵称与表达许可已绕过语义检索直读，方向正确。其他影响行为的长期偏好，如回答长度，目前仍可能依赖 Planner 的 need_memory 和 dense top-k。一般记忆排序无最低语义门槛，重要性/新近性会抬高弱相关结果。

建议将少量结构化行为偏好作为确定性上下文，将情节/事实记忆做相关性检索；允许检索返回空集。对短昵称、专有词、精确事件可先使用 SQLite 精确匹配或词法候选，再与 dense 合并，不必先迁移外部向量服务。

### 4.4 先修缓存和全量扫描，再考虑 ANN

证据：[vector_store.py:202](H:/HanserAgent/backend/hanser_agent/retrieval/vector_store.py:202)、[vector_store.py:217](H:/HanserAgent/backend/hanser_agent/retrieval/vector_store.py:217)。普通 `_matrix` 先读取全部向量 BLOB 才判断缓存；filtered search 每次重建 tensor。Style/Memory 路径主要使用 filtered search，不能充分利用矩阵缓存。

建议先读取轻量 active generation/revision，未变化就复用矩阵，再按 ID mask 过滤；保留索引发布边界校验。数据规模较小时 exact search 完全可保留。当前没有延迟实测，不能断言它比模型推理更耗时。

## 5. Chat / Voice / Live2D 的资源与时序

| 模块 | 当前资源安排 | 问题与取舍 |
|---|---|---|
| Planner | Ollama；need_wiki 时主动 unload，其他轮次 keep_alive=5m | 闲聊后也可能继续占显存；并发请求之间没有租约协调 |
| Embedding | 一个共享实例，CUDA float16，线程锁串行，延迟加载 | 没有卸载接口；Style/Memory/Wiki 的 gather 不会使同一实例并行推理 |
| Reranker | 延迟加载，auto device，独立推理锁 | `release_after_request` 默认 false，首次事实查询后可持续驻留 |
| Responder | 外部 API | 当前不消耗本机模型权重显存；不能按本地双 LLM 估算 |
| Voice | 独立进程，启用时加载模型，单 worker | 与 backend/Ollama 没有统一资源协调；单 worker 只约束自身 |
| Live2D | 未来网页 WebGL 消费者 | 还没有真实接线；若使用同一 GPU，也应预留纹理/帧缓冲和桌面显示余量 |

所以当前是“局部卸载和实例锁”，不是跨进程显存调度。无法在不运行情况下给出峰值显存、首音时间或支持显卡容量的结论。

建议在现有进程边界内加入轻量资源策略，不先引入 Redis 或新微服务：

1. 对指定 GPU 明确可同时常驻的模型组合，区分常驻权重与并发推理峰值。容量足够时保留热模型；容量不足时按阶段卸载或将小模型移至 CPU，后续用真实数据选取舍。
2. Voice 正在播放/生成、下一轮 Planner 与 RAG、新用户并发请求应共享准入规则。串行推理本身不会释放已驻留权重。
3. 生成中的旧语音只允许迟到结果被丢弃，GPU 回收发生在当前推理返回后；不要让 UI 的“已停止”冒充设备已空闲。
4. 普通文字仍要等 post-turn 才返回，Next 只在 await 完 JSON 后写一个 text-delta。首音包含整个 Planner、检索、Responder、持久化、post-turn、创建 job 和首段 TTS 的串行延迟。可复用现有 pending snapshot 构建可恢复 post-turn 队列，先修 F3，再评估是否移出返回路径。
5. 先接一个实际模型的 RigAdapter、timeline 读取与 requestAnimationFrame，再做动态静音视觉和离线导出；当前只有接口，不能通过打开开关完成这些能力。

## 6. Persona 专项结论

已完成的核心价值包括：人物描述与产品 override 分开、production 只加载 released 设置、manifest 来源校验、严格审核 Style、禁止 Style 充当事实、本轮信号先于 policy、真实持久化回复驱动近期表达观察，以及 semantic_text/display_text 分离。旧“熟悉度每轮自动增长、熟悉就放开许可”的行为已不在当前 state_engine 中，不应重复报告为现存问题。

仍需处理：

- **软目标实现偏成条件性强制表达。** [policy.py:358](H:/HanserAgent/backend/hanser_agent/persona/policy.py:358) 按会话 key 和 20 轮 horizon 选择槽位；满足 playful/频率等条件时加入 `must_do: expression.use_light_profanity`，要求实际选一个粗口。它不是每轮硬配额，但也不是文档声明的纯软反馈。`product_overrides.yaml` 与参数消费者表仍说“不逐轮抽签、不强制输出”。建议保留用户已接受的 15% 目标与 8% 观察结果，将输出要求回归有语境的 soft affordance；如确实要保留条件性强制，则明确更新产品语义与评估，不能靠“已发布”掩盖不一致。
- **观察窗口与历史读取耦合。** observation_turns 允许到 12，但主链只读 12 条消息，通常最多 6 条 assistant 回复；调大参数没有完整效果。表达观察应单独按成功 assistant turn 读取，权限状态则不能依赖此窗口。
- **production 和 candidate 共用可变目录。** [api.py:64](H:/HanserAgent/backend/hanser_agent/api.py:64) 将两者映射到同一路径。哈希能发现文件与旧 manifest 不一致，但候选工具更新文件及 manifest 后，下一次生产加载也会看到新包。建议发布后使用独立版本目录，候选构建写另一目录，再在发布边界切换 selector。
- **人格成长没有实质完成。** relationship 的多数值维持原值，仅 tension 更新；这是比随轮数增长更合理的保守现状，但不等于已完成长期关系建模。只有产品确实需要时才引入来源明确的互动事件与可解释更新，不能拿对话次数作信任或许可。
- **Style 可用性要有显式状态。** generation 不兼容会返回空 examples 并在 excluded 记录原因，但主响应不一定标为 degraded。可在 readiness/trace 中明确“正常空召回”和“发布组合不可用”。不用每轮重复全包哈希。

## 7. 恢复能力与验证边界

请求在回复落库前崩溃，会留下 `in_progress` 且无 snapshot；claim 只返回 RequestInProgress，没有租约过期/启动恢复。回复落库后、post-turn 失败记录写入前崩溃，则虽有 pending snapshot，但现有待重试列表只查 post_turn_failures，没有扫描 pending snapshot 的补偿入口。建议复用现有两类状态建立明确恢复过程，避免新建第二套聊天历史。

当前 Memory API 的 PATCH/DELETE 仅按 memory_id 修改，不验证请求 owner；在文档声明的可信本地单用户范围内属于部署边界，不能据此宣称多用户权限完成。若开放网络或多用户，应从可信身份贯彻所有权，不信任任意自报 user_id。

本次不执行测试。后续修复可只补与风险直接对应的聚焦用例：摘要累计长度与覆盖空档、仅改重要性的记忆可召回、旧 post-turn 不覆盖新修订、超过 6 轮后会话禁令仍有效、索引构建失败保持完整旧组合、无回执释放 worker、SSE 长间隔、终态 job 重放及播放中打断。通过后再在启用真实音画资产的阶段做一次必要综合验证。

建议顺序：先 F1/F2/F3/F4 与请求恢复，再补索引联合发布；启用 Voice 前必须处理 F6/F7/F8 与资源准入；随后完成实际 Live2D 接线和资产验收。性能调参、ANN、复杂关系成长和更多表现风格排在这些正确性工作之后。

# Current System Evaluation

日期：2026-09-05。结论：Python 主链路可运行，但 Persona 提升尚未获得可信验收；Memory 的错误写入和纠正失败已经在真实跨会话中出现。30 项单测通过不能抵消这些行为失败。

本文 **MEASURED** 是真实执行、计数或保存文本的直接检查；**INFERRED** 是解释或代码风险；**NOT_EXECUTED** 是没有有效测量。失败的 judge 请求虽实际发出，但 JSON 无法解析，其评分状态记为 NOT_EXECUTED，不能算模型候选失败或评分为零。

## Test Environment / Model / Quantization

| 项目 | 本次实际条件 |
|---|---|
| 工作区 | `H:\HanserAgent`；无 Git 历史可查；原规格未覆盖 |
| 数据隔离 | SQLite backup 到 `audit_artifacts/phase6/audit.db`；实际 source DB 562 documents、4,197 chunks、886 style examples、5,083 vectors；原会话/记忆/eval 表均空 |
| Python | 全局 3.13.2；项目 .venv 缺 torch；torch 2.6.0+cu124 |
| GPU | RTX 3060 Laptop，6 GiB；可用性真实验证 |
| Responder | 配置及响应 model 字段 `deepseek-v4-flash`，OpenAI-compatible 外部服务；远端权重/量化未知，不能由名称推定 |
| Sampling | temperature .3、top_p .9、max_tokens 8192；服务未提供可验证 seed，非逐 token 可复现 |
| Planner | Ollama `qwen3.5:4b`，tags 返回 Q4_K_M、4.7B |
| Embedding | Qwen3-Embedding-0.6B，CPU float32，1024维，本地缓存 |
| Reranker | Qwen3-Reranker-0.6B，auto GPU/fp16，max_length 768 |
| Harness 偏离 | HF offline/local-files-only、torch threads=4；真实 provider / factory / SQL，HTTP 为 ASGITransport，不含 TCP/WPF |
| 计时 | 完整非流式请求 wall time；阶段 wrapper；网络和模型内部计算未拆分 |

环境证据：[environment.json](audit_artifacts/phase6/environment.json)、[snapshot.json](audit_artifacts/phase6/snapshot.json)、[availability.json](audit_artifacts/phase6/availability.json)。凭据仅从原配置读入内存；隔离配置已脱敏。

## Dataset / Test Cases

固定 37 个场景，覆盖 Basic、Style、Factual、Memory、Relationship 和对抗 Persona；完整输入见 [fixed_cases.json](audit_artifacts/phase6/fixed_cases.json)，逐案意图与判定见 [Regression Suite](PERSONA_REGRESSION_SUITE.md)。测试输入为本次构造，不是 37 条认证真人金标准答案。

执行量：185 条独立 A–E 生成；37 条 F 引用 E，共 222 行；top-k 10题×5档=50次生成；真实两组20轮=40次请求；重建 store/factory 后新会话1次。Judge v2共74次尝试、33有效评分；初版74次中仅1次有效，单独归档，不混入结果。

消融冻结 Wiki evidence、Style examples、memory fixtures、history 和 state。Planner 使用受控 plan，避免路由波动掩盖 Persona 组件贡献。冻结结果见 [frozen_contexts.jsonl](audit_artifacts/phase6/frozen_contexts.jsonl)。实际 Planner、检索和演变中的 Memory/State 在独立 runtime 集中验证。

## Ablations

A=Core；B=A+Voice/Behavior；C=B+Style(top3)；D=C+Memory；E=D+Relationship/Scene；F=当前完整冻结 context，**与 E 相同，是 alias，没有第六组独立调用**。A 保留所有组相同的 grounding boundaries 和输出契约，故不是把安全/事实约束也删除的字面 bare core。Memory 仅4个fixture相关case有变化；其余33题 D/C 条件相同。表中 N/A 表示没有可信、完整评分，不是零分。

| Variant | Fidelity | Naturalness | Grounding | Continuity | Repetition：含憨憨回复率 | Generation p50 | 平均输入 token代理 |
|---|---|---|---|---|---:|---:|---:|
| A | N/A | N/A | 文本抽检，未全量评分 | 无memory | 78.4% | 2.783s | 1010.5 |
| B | 4.21† | 4.36† | 4.79† | 未独立评分 | 56.8% | 2.624s | 1244.4 |
| C | 4.33† | 4.45† | 4.88† | 未独立评分 | 43.2% | 2.240s | 1449.9 |
| D | N/A | N/A | 文本抽检 | fixtures非长期验收 | 40.5% | 1.977s | 1455.8 |
| E | N/A | N/A | 有无依据第一人称 | state净收益未证实 | 54.1% | 2.333s | 1554.8 |
| F | 同 E | 同 E | 同 E | 真实链路另测 | 54.1% | 2.333s | 1554.8 |

†33个**有效评审方向**的同模型 Judge 1–5分均值，包含重复case、缺失非随机，**不能作为已验证的质量增益**。B/C voice=4.42/4.48，behavior=4.30/4.52；unsupported-first-person flags 均2/33。这是 judge 输出计数，不是人工确认的发生率。

各组均37题，平均字符 A105.1/B81.6/C53.9/D89.8/E58.5。B→C多约205.5 token代理，输出变短27.7字符，口癖出现减少，是可测变化；是否“更像 Hanser”尚未证明。D 的长输出尾部显著影响均值，在33题 context 不变、单次随机采样条件下，不能把全部变化归因于 Memory。E/D 也不是足以证实 relationship 价值的独立实验。

输入代理由本地 Qwen tokenizer 计算，不含服务端隐含包装，不等同 DeepSeek tokenizer 或账单；本组未保存 provider usage，不伪造真实 token 成本。各组采样随机交错、并发3；这里的 generation 不是端到端延迟。

### Style top-k

| k | 题数 | 平均字符 | 输入 token代理 | generation p50 |
|---:|---:|---:|---:|---:|
| 0 | 10 | 38.6 | 936.8 | 1.956s |
| 1 | 10 | 36.8 | 1025.8 | 2.326s |
| 2 | 10 | 40.1 | 1085.6 | 1.690s |
| 4 | 10 | 36.5 | 1198.7 | 1.885s |
| 6 | 10 | 29.4 | 1330.7 | 1.979s |

MEASURED。没有单调质量收益，也没有可靠最优 k。晚安场景 k1 编“刚吃饱”，k4 编“白天直播太久、嗓子哑”，k0/k6 没有该附加事实；不能以多样性或短回复代替 grounding。首次并发运行发生 SQLite locked，已保留失败 log；正式50条为停止其他生成后的顺序重跑。证据：[topk_outputs.jsonl](audit_artifacts/phase6/topk_outputs.jsonl)。

## Judge Reliability / Human Review Candidates

v2 judge：deepseek-v4-flash，与候选同模型；temperature0、top_p1、max_tokens4096；prompt/rubric `phase6_pairwise_v2`；日期、哈希见 [judge_metadata.json](audit_artifacts/phase6/judge_metadata.json)。每对 B/C 左右各一次、匿名显示；judge 可看 source/context，避免仅凭熟悉的名字判断。

74次中33次合法 JSON，41次解析失败。有效方向 B胜10、C胜17、平6；只有8题两个方向都完整，其中结论一致3题，全部为C胜：**换序一致率37.5%**。按case聚类、两方向平均，character delta C−B=+0.125，2,000次 seed606 bootstrap 95%区间[-0.25,+0.50]。样本小、缺失偏倚、同模型评自己，一致率低，不能接受“Style RAG 已提升”的结论。

初版 schema 提示不充分且预算1000；修订版仍大量失败。同一失败题用相同4096预算重试可成功，不能断言全由预算耗尽导致。少量诊断还见理由与分数不一致。停止把不可靠 judge 调用堆成更多“证据”。

[human_review_samples.jsonl](audit_artifacts/phase6/human_review_samples.jsonl) 保存74个匿名、有顺序的 B/C 比较；[human_review_key.jsonl](audit_artifacts/phase6/human_review_key.jsonl) 为分离答案键。当前无独立真人评审，也无第二个独立 judge。优先人工看 greeting、comforting、mixed_fact、warm_interaction、false_memory_trap、customer_service，再按场景分层抽其他样本；先锁定评分再揭盲。不得将本报告作者的文本审查称为用户认证。

## Retrieval Scores

真实本地模型，现有12题文件名相关性测试，脚本 `backend/scripts/eval_hybrid_retrieval.py`：

| 路径 | Recall@5 | Recall@20 | MRR | NDCG@5 | p50 | 整轮耗时 |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 1.000 | 1.000 | 1.000 | 1.000 | .04s | 1.61s |
| Dense | .583 | .667 | .450 | .480 | .24s | 14.68s |
| Hybrid | 1.000 | 1.000 | .678 | .754 | .27s | 3.18s |
| Hybrid+Reranker | 1.000 | 1.000 | 1.000 | 1.000 | 2.32s | 30.59s |

MEASURED，见 [retrieval_benchmark.log](audit_artifacts/phase6/retrieval_benchmark.log)。测试含手工关键词、按document filename判相关，不能衡量“证据是否足以支持心理动机”；query较容易，reranker恢复RRF排序损失，尚未超越BM25。不据此立刻删除 dense 或 reranker。旧 `eval_reranker.py` 未去重 filename 可能使 NDCG>1，Legacy Prometheus 又按filename覆盖同文件chunk，因此旧新直接质量比较 **NOT_EXECUTED：现有对照协议不公平**。应先修离线评测，不伪报本地优于旧方案。

Planner 25题、强制local profile无fallback：schema/intent/wiki precision/recall/F1均1.000，rewrite .875、response_mode .857，p50=2.13s。误例“今天好累啊”“我们现在算什么关系”“哈哈哈哈哈哈”判casual；“为什么”“后来呢”未满足rewrite关键词。规则验收不是完整语义判定。见 [planner_benchmark.log](audit_artifacts/phase6/planner_benchmark.log)。

Style37次检索，共111个返回、79个不同example；同一启发式scene匹配89.2%、mode56.8%、length79.3%，top3字面完全重复0组；最常见example使用5/37次。scene标签已知有错，89.2%不是独立相关性精度。详情见 Persona Review。

## Memory Results / Multi-turn Results

MEASURED：两组20轮全部HTTP200，且新factory、新conversation、同user的跨会话请求HTTP200。会话 `continuity` 覆盖偏好、否定、纠正、上海虚假共同经历、考试、昵称；`mixed` 覆盖Wiki追问、情绪切换、重复问题、风格诱导与虚假经历。每轮完整输入、输出、plan、context、读写memory、state、timing在 [runtime_traces.jsonl](audit_artifacts/phase6/runtime_traces.jsonl)。

| 测试 | 观察 | 判断 |
|---|---|---|
| 用户喜好/昵称 | 能入库并在后续调出；改昵称后新名字生效 | 基础连续性有用，但不等于全部记对 |
| “不喜欢咖啡，喜欢茶” | positive coffee 与 negative coffee不同key，旧肯定仍active | FAILED：纠正语义 |
| 假上海问句 | 回答可能拒绝，却在post-turn写shared_event，confidence达gate | FAILED：说法安全不代表存储安全 |
| 用户明确否认 | 后续仍受上海/小笼包污染；考试话题出现无益callback | FAILED：history污染和memory生命周期 |
| “考完试了” | 不关闭旧unresolved event | FAILED：完成事件处理 |
| 跨会话回忆 | 同时召回咖啡/茶，自称答案“反复横跳” | FAILED：把系统纠正缺陷归给用户 |
| 回合后摘要 | 确实生成；截断拼接，不验证主谓/否定 | IMPLEMENTED，语义验收不足 |
| relationship | 普通轮数也加familiarity，正关键词含否定仍加warmth | 状态数值行为已测，用户体验净收益未证实 |

摘录（/ 表示换行）：

> continuity 12 用户：你还记得我们之前一起去过上海吗？
>
> 模型当前回复拒绝无依据共同经历；同轮 post-turn 却接受 shared_event。
>
> continuity 13 回复：哈哈被你抓包了确实没去过……
>
> cross-session 回复：毛怪们这个我记得 / 喝什么嘛 你之前给的答案有点反复横跳 / 咖啡和茶我都看到过 / 你到底是哪派的啦

“毛怪们”是用户在第18轮明确要求的新称呼，不能误判成改名失败。错误是偏好纠正后的歧义。完整未截断文本见 [cross_session.json](audit_artifacts/phase6/cross_session.json)。

7个隔离故障探针全部成功复现其预设缺陷：reranker异常不降级、假共同事件写入、偏好正负并存、null patch SQL错误、conversation owner变更串历史、全局vector top20导致本user候选饥饿、另一store更新后首store缓存过期。探针不是7个“产品通过测试”。见 [fault_probes.json](audit_artifacts/phase6/fault_probes.json)。

## Grounding Results / Failure Cases

以下为保存输出的直接文本审查；列举明确失败，不把挑出的案例除以37冒充随机总体发生率。Grounding 独立于 Persona 可爱程度判定。

| Case / 条件 | 原输出片段 | 独立判定 |
|---|---|---|
| mixed_fact / F | “之前直播还把17年记成别的日子” | Unsupported first-person event；所给Wiki不足以支持该自述 |
| warm_interaction / F | “我刚下班那阵也喜欢找人瞎聊” | Invented personal routine；模仿亲近感损害真实性 |
| greeting / top-k4 | “白天直播是不是有点太久了 嗓子都哑了” | Invented current event + physical condition |
| false_memory_trap / F | “憨憨真不记得啦 是不是梦到跟我去过上海呀” | 未肯定旅行，但以记不得包装现实共同经历，不符合严格边界 |
| identity_challenge / F | “偷偷把憨憨歌单循环到凌晨的冒牌货” | 接受诱导并另造经历，身份持续性失败 |
| customer_service / F | “好的呢亲 憨憨客服为您服务” | 有角色词汇但行为转客服 |
| style_demand / F | 配合改称呼和大量emoji | 用户风格要求压过稳定voice约束 |
| mixed runtime 5 | 用户“今天我考上啦”；回复“憨憨不知道哦” | 空provider内容被fallback伪装成不确定，不是合理庆祝 |

最后一项 provider HTTP200、finish_reason=stop，completion72 tokens均为reasoning计数、可见content空；本次未保存隐藏推理文本。`HanserResponder` 用固定unknown填空，是实际观测。其他消融中同样字符串不能在没有原始provider记录时一概判空输出。

invented_emotion / invented_condition 等明确Wiki心理/身体追问多能拒绝无证据解释；这是局部正例。普通寒暄、亲近互动中的附加自述更容易漏过。没有证据证明具体虚构逐字来自某条 Style example；应称上下文相关风险，不能断言逐条复制泄漏。

## Persona Scores / Persistence

14维覆盖状态：character/voice/behavior/naturalness/grounding有低可靠B/C judge分；persistence/context sensitivity/relationship continuity/memory fidelity有真实多轮及故障证据；assistantese/catchphrase/diversity有词面统计及文本反例；anthropomorphism不设“越像真人越高”的单维分，以自然社交回应与无依据现实自述分别判断；unsupported-first-person单独见上表。尚无全部六variant的可靠14维总分，**NOT_EXECUTED**，不合成漂亮雷达图。

确定性assistantese字串检测六组均0%，但实际customer_service输出明显客服腔；这是检测器召回不足，不能宣称客服腔消失。F含憨憨20/37条，真实corpus共886条仅46次该词；分布提示放大倾向，场景不同不能直接估计“过用率”。两组20轮可见身份/口癖/话题污染，没有做每5轮人工盲评分曲线，不能声称持续性分数随回合下降多少。

## Latency / Token Cost

40轮实际链路；多工具重叠、共享embedder，不将阶段分位数相加。

| 阶段 | n | p50秒 | 最大秒 |
|---|---:|---:|---:|
| Planner | 40 | 2.469 | 70.080 |
| Wiki | 4 | 4.633 | 8.033 |
| Style | 40 | .299 | 16.912 |
| Memory | 38 | .557 | 4.049 |
| Context | 40 | .000136 | .00514 |
| Responder | 40 | 2.493 | 18.237 |
| Validator | 40 | .000038 | .000450 |
| Post-turn | 40 | .0162 | 51.115 |
| Total | 40 | 6.012 | 102.840 |

真实provider usage：prompt均1784.675、max4077、合计71387；completion均189.85、max1303、合计7594；total均1974.525、max4217、合计78981。completion含provider报告的reasoning tokens，不能与可见输出字数等同。此表只计40runtime轮，不是整个审计API总账单。

TTFT **NOT_EXECUTED**：当前非流式。GPU曾见5849/6144MiB占用，初始快照781MiB，未连续采样，不是峰值认证。曾有并发审计模型工作；冷启动、GPU争用、CPU与SQLite都可能影响最大值。代码存在单向卸载和同步post-turn问题，**INFERRED**其贡献；不能把102秒全部归因reranker。需独占GPU、交替Wiki/闲聊20轮复测再定资源参数。

Context config的4096未在兼容API中强制执行，诊断60,000字符照单全收。该风险已由构建结果证明；真实远端上下文溢出异常本次没有复现。

## Confidence / Limitations

高置信：真实执行数量、输入输出、存储缺陷、结构标签错误、缺token裁剪。中置信：给定case的Persona/grounding文本判定。低置信：总体fidelity改善、组件因果收益、全年风格变化及延迟根因分摊。

NOT_EXECUTED：WPF用户操作链路、生产并发压测、30–50轮真实生成压力集、独立真人/第二judge、real+synthetic对照（库内无synthetic）、LoRA训练/模型横评、音视频说话人核验、正确去重后的旧Prometheus公平A/B。100轮relationship诊断为确定性状态更新，不能充作100轮LLM对话。

源DB完整性与脚本语法核验见 [final_verification.json](audit_artifacts/phase6/final_verification.json)。生产代码没有修改；本次仅新增审计脚本、数据和报告。

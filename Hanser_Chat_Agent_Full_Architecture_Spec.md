# Hanser Chat Agent 全量工程架构设计规格
## 面向 Codex 的实现蓝图：从 HanserWiki RAG Pipeline 扩展为高真实感、本地优先、可训练、可评测的角色聊天 Agent

> 文档版本：v1.0  
> 调研与设计日期：2026-09-04  
> 目标读者：Codex / 项目开发者 / 后续模型与数据工程人员  
> 基线项目：`zhukongqwq/HanserWiki` 及其 Python Agent Migration 版本  
> 核心目标：保留现有 Wiki 事实检索能力，在此基础上构建具有稳定 Persona、自然多轮对话、长期记忆、Style RAG、动态关系状态、本地模型分工、可观测与可评测能力的完整角色聊天 Agent。

---

# 0. 文档目的

本文件不是“功能愿望清单”，而是项目级工程规格。Codex 应把它视为目标架构、模块职责、数据契约、实现顺序和验收标准的统一来源。

项目最终要从当前的：

```text
用户
 ↓
Bunny（关键词/路由）
 ↓
BM25
 ↓
Prometheus（文档筛选）
 ↓
Hanser（Persona + 文档 → 回答）
```

演进为：

```text
                           User / Client
                                │
                                ▼
                        Conversation Gateway
                                │
                                ▼
                        Agent Runtime / State
                                │
                   ┌────────────┴────────────┐
                   │                         │
             Recent History            Persistent State
                   │                         │
                   └────────────┬────────────┘
                                ▼
                        Dialogue Planner
                  intent / rewrite / tool plan
                                │
             ┌──────────────────┼────────────────────┐
             │                  │                    │
             ▼                  ▼                    ▼
       Fact Retrieval      Memory Retrieval      Style Retrieval
       WikiSearchTool      MemorySearchTool      StyleSearchTool
       BM25 + Dense        long-term memory      dialogue examples
             │                  │                    │
             └──────────────────┼────────────────────┘
                                ▼
                         Context Builder
             persona + state + memory + facts + style
                                │
                                ▼
                         Hanser Responder
                  唯一的用户可见自然语言生成器
                                │
                                ▼
                    Style / Grounding Validator
                                │
                                ▼
                             Answer
                                │
                      ┌─────────┴─────────┐
                      ▼                   ▼
               Save Conversation    Memory Extractor
                                      / Summarizer
```

最重要的架构原则：

> **Routing 只决定“需要哪些上下文和工具”，不能决定 Persona。**  
> 普通闲聊与 Wiki 事实问答最后必须进入同一个 Responder，使用同一份 Persona、同一套 Style 规则、同一套 History 与 Memory 组织逻辑。

---

# 1. 当前基线与主要问题

## 1.1 当前已有能力

Python Migration 当前已经或正在具备：

- FastAPI 后端；
- OpenAI-compatible LLM Client；
- SQLite Wiki 文档库；
- BM25 检索；
- Planner/Router；
- Query Rewrite；
- Conversation State V1；
- `WikiSearchTool` 抽象；
- Persona Prompt；
- RAG 最终回答；
- WPF 可继续作为临时客户端；
- 外部大模型作为当前模型供应源。

原始 HanserWiki 的三个 LLM 模块可以抽象为：

| 原模块 | 当前作用 | 最终目标 |
|---|---|---|
| Project Bunny | 关键词提取、轻量路由 | `DialoguePlanner`，由本地小模型负责 |
| Prometheus | 候选文档筛选 | 改为专用本地 Reranker，不再使用生成式大模型 |
| Hanser | 最终自然语言回答 | `HanserResponder`，使用本地可部署 RP 模型 |

## 1.2 当前主要缺陷

### A. Persona 只靠一个 System Prompt

结果容易出现：

- 同一个问题多次回答风格变化明显；
- 普通 Chat 和 RAG Chat 风格不一致；
- 标点、口癖、句长等强风格特征漂移；
- 模型为了“证明自己像角色”而过度卖萌、过度玩梗；
- History 中旧 Assistant 输出会反向污染 Persona；
- Persona 越写越长但实际控制力并不一定提高。

### B. Persona 与事实混杂

角色“是谁”和角色“怎么说话”混在同一大 Prompt 中，导致：

- Wiki 事实被模型转换成未经支持的第一人称回忆；
- 角色心理活动、身体状态、动机被脑补；
- 事实类问题中模型容易“演过头”。

### C. RAG 只解决“知道什么”，没有解决“怎么自然地说”

当前 Wiki RAG 擅长找：

- 经历；
- 日期；
- 配音作品；
- 直播事件；
- 人物关系。

但不会自动找到：

- 类似场景下 Hanser 实际是怎么说话的；
- 打招呼的节奏；
- 被调侃时如何回应；
- 不确定时怎么表达；
- 长回答和短回答的自然比例；
- 口癖真正出现的频率。

### D. 记忆层次不足

仅有最近若干条 History 不等于完整 Chat Agent Memory。

还缺：

- 会话摘要；
- 跨会话长期用户记忆；
- 共同经历；
- 关系进展；
- 未完成话题；
- 角色与用户之间的共同梗；
- 记忆冲突和过期机制；
- Memory Inspector / 删除能力。

### E. 三个模块都依赖外部生成式大模型，成本和延迟不合理

Bunny 和 Prometheus 本质上都不是用户可见自然语言生成任务。

尤其 Prometheus 更适合专业 reranker，而不是让通用 LLM 输出文档名。

---

# 2. 最终产品目标

项目目标不是简单“角色卡 + LLM”，而是一个可以长期演进的角色 Agent Runtime。

## 2.1 必须达到的体验

### Persona Fidelity

- 普通闲聊和事实 RAG 的 Persona 一致；
- 强格式特征稳定，例如 Hanser 的标点使用规则；
- 不出现明显客服腔；
- 不机械重复“毛怪们”等口癖；
- 不每轮都强行表现所有人格标签；
- 角色风格应表现为统计分布，而不是 checklist；
- 长对话后 Persona 不明显漂移。

### Conversational Naturalness

角色应具备：

- 自然短回应；
- 合理的长短句变化；
- 能接住前文；
- 能回调早先聊天内容；
- 能主动延续未完成话题；
- 不总以“还有什么想问的吗”结尾；
- 不总是回答得像百科；
- 能根据关系和场景调整调侃程度；
- 不因添加 Wiki 资料而切换成报告体。

### Factual Grounding

- Wiki 事实问题必须优先依据检索证据；
- Style Example 不得作为事实来源；
- Synthetic Persona 数据不得作为事实来源；
- 无足够证据时允许角色化地表达不知道；
- 不允许 Persona 自动补出“我当时觉得”“我后来后悔”等来源中不存在的事实。

### Memory

- 同一 Conversation 多轮连续；
- 跨 Conversation 可记住用户明确表达的重要信息；
- 可记住共同经历和 shared joke；
- 可忘记、覆盖、修正旧信息；
- 用户可以查看和删除长期记忆；
- 记忆必须带 provenance 和 confidence。

### Local-first

正常聊天链路尽量本地化：

```text
Planner            → Local Small LLM
Embedding          → Local Embedding Model
Reranker           → Local Reranker
Hanser Responder   → Local RP Model
Memory Extractor   → Local Small LLM
```

外部大模型保留为：

- 开发期 Teacher；
- Persona 数据生成；
- 离线高质量标注；
- Benchmark Judge；
- fallback，而不是日常核心依赖。

---

# 3. 开源项目调研与可借鉴设计

以下调研重点不是“直接 fork”，而是抽取已验证的设计模式。

## 3.1 SillyTavern

参考：

- GitHub: https://github.com/SillyTavern/SillyTavern
- Character Design: https://docs.sillytavern.app/usage/core-concepts/characterdesign/
- World Info: https://docs.sillytavern.app/usage/core-concepts/worldinfo/
- Data Bank / RAG: https://docs.sillytavern.app/usage/core-concepts/data-bank/
- Chat Vectorization: https://docs.sillytavern.app/extensions/chat-vectorization/
- Prompts: https://docs.sillytavern.app/usage/prompts/

值得借鉴：

1. Character Permanent Context 与 Example Dialogue 分离。Character Description / Personality / Scenario 是持续上下文，而 Example Messages 更适合直接展示实际写作与说话风格。因此本项目必须把 `persona_core` 与 `style_examples` 分开。
2. First Message / Example Messages 对风格极重要。抽象的“嘴硬但关心人”不如真实 few-shot。
3. World Info / Lorebook 动态注入。不是所有信息每轮塞入，而是按当前语义激活。
4. Data Bank 按 global / character / chat scope 区分。本项目应采用 namespace。
5. Prompt Anchors。不同信息块应有固定注入位置，而不是全部拼成一个巨大字符串。

## 3.2 RisuAI

参考：

https://github.com/Kyrosarg/RisuAI

值得借鉴：

- 多 API / Provider 抽象；
- Lorebook；
- Prompt 顺序可配置；
- Variables；
- Regex output processing；
- Group Chat；
- Emotion image；
- 插件系统。

关键启发：Model Provider 不应和 Agent Logic 耦合；Output Post-processing 是 Persona Harness 的合法组成部分。

## 3.3 ChatHaruhi / Zero-Haruhi

参考：

- https://github.com/LC1332/Chat-Haruhi-Suzumiya
- https://github.com/LC1332/Zero-Haruhi

这是与 Hanser 项目最值得重点借鉴的一类项目。

其关键思路可概括为：

```text
Persona
+
根据当前问题检索历史角色对话
+
把角色原始 Dialogue 作为 RAG few-shot
+
LLM 回复
```

ChatHaruhi 还构建了大量角色对话数据，并在真实数据不足时使用大模型生成补充角色对话。

对 Hanser Agent 的直接启发：

> **Wiki RAG 与 Style RAG 必须分开。**

```text
Fact RAG:
Hanser 当年发生过什么

Style RAG:
类似场景 Hanser 通常怎么说
```

## 3.4 Letta

参考：

https://github.com/letta-ai/letta

重要思想：

```text
Core Memory
+
External / Archival Memory
```

本项目也应采用：

```text
Core Persona / Core Relationship
            永久注入

Long-term Episodic Memory
            按需检索
```

不要把全部长期记忆塞入 System Prompt。

## 3.5 LangGraph

参考：

https://github.com/langchain-ai/langgraph

LangGraph 区分 thread-level short-term memory 与 cross-thread long-term store，并通过 checkpointer 持久化状态。

本项目可借鉴概念，但第一阶段不建议为了框架而整体改写成 LangGraph。应先自己保持 `AgentState / Planner / Tool / Executor` 清晰，未来再提供 adapter。

## 3.6 SillyTavern Memory 社区扩展

参考：

- MemPalace: https://github.com/ShinRalexis/silly-tavern-mempalace-extension
- Memory Layers: https://github.com/SAWA-25/SillyTavern-MemoryLayers
- Memory Books: https://github.com/snietzl2/SillyTavernMemoryBooks

可借鉴：

- Identity Anchor 永久独立；
- Retrieved Memory 是“数据”而不是“指令”；
- Timeline / Episodic Memory 对长期 RP 连续性非常重要；
- 记忆应允许检查、修订和删除。

## 3.7 CoSER

参考：

https://github.com/Neph0s/CoSER

可借鉴：

- 角色数据构建；
- Role-play SFT；
- 多轮一致性；
- Given-Circumstance Acting；
- Storyline Consistency / Anthropomorphism / Character Fidelity / Storyline Quality 等评测维度。

## 3.8 HER

参考：

https://github.com/cydu24/HER

HER 的重要启发不是照搬自由文本思维链，而是：RP 应模拟角色在当前情境中的立场、情绪和目标。

本项目建议采用结构化 Character State，而不是展示或持久化自由文本 CoT：

```json
{
  "mood": "relaxed",
  "energy": 0.55,
  "warmth": 0.70,
  "teasing_permission": 0.45,
  "current_topic_attitude": "interested",
  "unresolved_threads": ["用户明天下午考试"]
}
```

## 3.9 RolePlayBench / InCharacter / TwinBench

参考：

- https://github.com/LeviTheWeasel/rp-benchmark
- https://github.com/Neph0s/InCharacter
- https://github.com/MiuLab/RolePlayBench
- https://github.com/TwinVoice/TwinBench

重要启发：单轮测试远远不够。必须测试 10–20 轮后的角色一致性、记忆、挑战 Persona、话题切换和混合事实场景。

---

# 4. 目标架构

```text
┌────────────────────────────────────────────────────────────────────┐
│                           Client Layer                             │
│ WPF / Web / CLI / Test Harness                                    │
└─────────────────────────────┬──────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                        FastAPI Gateway                             │
│ request id / streaming / conversation id / debug flag             │
└─────────────────────────────┬──────────────────────────────────────┘
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                         Agent Runtime                              │
│   Load State → Planner → Tool Execution → Context → Respond        │
└───────┬──────────────────────┬───────────────────┬─────────────────┘
        │                      │                   │
        ▼                      ▼                   ▼
 Conversation Store      Dialogue Planner      Tool Registry
        │                                      │
        │                         ┌────────────┼─────────────┐
        │                         ▼            ▼             ▼
        │                    WikiSearch    MemorySearch   StyleSearch
        │                         │            │             │
        │                         ▼            ▼             ▼
        │                     Fact RAG     Memory RAG    Style RAG
        │                         └────────────┼─────────────┘
        │                                      ▼
        │                                Context Builder
        │                                      │
        └──────────────────────────────────────┤
                                               ▼
                                      Hanser Responder
                                               │
                                               ▼
                                  Validator / Normalizer
                                               │
                                               ▼
                                          User Reply
                                               │
                                               ▼
                                  Post-turn Memory Pipeline
```

---

# 5. Agent 一轮请求的标准生命周期

1. Receive User Message
2. Load Conversation State
3. Load Recent History
4. Planner 输出 intent / standalone query / tool plan / response mode
5. 并行执行所需 Tool
6. Retrieve Wiki facts / Long-term memories / Style examples
7. 构造 Character State Snapshot
8. ContextBuilder 按 token budget 组装上下文
9. 唯一 HanserResponder 生成回答
10. StyleValidator 检查 Persona 硬规则
11. GroundingValidator 检查事实模式
12. 保存 user + assistant messages
13. 更新 summary / memory / relationship state
14. 写入 trace 与 metrics

---

# 6. 核心数据结构

建议全部使用 Pydantic。

## 6.1 DialoguePlan

```python
class DialoguePlan(BaseModel):
    intent: Literal[
        "chitchat",
        "wiki_fact",
        "followup_fact",
        "user_memory",
        "relationship",
        "mixed",
        "unknown",
    ]

    standalone_query: str

    need_wiki: bool = False
    need_memory: bool = True
    need_style_examples: bool = True

    wiki_query: str | None = None
    memory_query: str | None = None
    style_query: str | None = None

    response_mode: Literal[
        "casual",
        "factual",
        "emotional",
        "playful",
        "storytelling",
    ] = "casual"

    fact_sensitivity: Literal[
        "low",
        "medium",
        "high",
    ] = "low"

    target_length: Literal[
        "short",
        "medium",
        "long",
    ] = "medium"
```

Planner 永远不生成最终用户回答，也永远不决定 Persona 是否启用。

## 6.2 AgentState

```python
class AgentState(BaseModel):
    conversation_id: str
    user_id: str | None
    recent_messages: list[ChatMessage]
    conversation_summary: str | None
    scene_state: SceneState
    relationship_state: RelationshipState
    unresolved_threads: list[str]
    plan: DialoguePlan | None = None
    wiki_evidence: list[EvidenceChunk] = []
    memories: list[MemoryItem] = []
    style_examples: list[StyleExample] = []
    trace_id: str
```

## 6.3 EvidenceChunk

```python
class EvidenceChunk(BaseModel):
    source_id: str
    filename: str
    document_id: int
    text: str
    retrieval_score: float
    rerank_score: float | None
    source_type: Literal["wiki", "transcript", "document"]
    source_date: str | None
```

## 6.4 StyleExample

```python
class StyleExample(BaseModel):
    id: str
    user_context: str
    character_response: str
    scene: str
    speech_act: str
    tone: list[str]
    relationship_level: str | None
    energy: float | None
    source_type: Literal["real", "synthetic"]
    source_ref: str | None
    authenticity_score: float
    quality_score: float
```

## 6.5 MemoryItem

```python
class MemoryItem(BaseModel):
    id: str
    user_id: str | None
    conversation_id: str | None

    type: Literal[
        "user_fact",
        "user_preference",
        "shared_event",
        "relationship_event",
        "promise",
        "shared_joke",
        "episode",
        "unresolved_thread",
    ]

    content: str
    importance: float
    confidence: float
    source_message_ids: list[str]
    created_at: datetime
    last_accessed_at: datetime | None
    status: Literal["active", "superseded", "deleted"]
```

---

# 7. DialoguePlanner

## 职责

只负责：

- intent；
- Query Rewrite；
- 决定调用哪些工具；
- 判断事实敏感度；
- 判断 response mode 和目标长度。

永远不能：

- 输出用户可见答案；
- 修改 Persona；
- 决定是否启用 Persona。

Persona 永远启用。

## 本地模型

优先 benchmark：

```text
Qwen3.5-4B
```

建议：

- temperature 0–0.2；
- JSON Schema constrained output；
- max output 512 左右；
- Pydantic validation；
- 解析失败 deterministic fallback。

输入只使用 Conversation Summary + 最近 4–8 条消息 + Current Message，不要塞全部历史。

---

# 8. Tool Registry

最终不要在 Runtime 里散落硬编码逻辑。

```python
tool_registry = ToolRegistry([
    WikiSearchTool(...),
    MemorySearchTool(...),
    StyleSearchTool(...),
    TimeContextTool(...),
])
```

通用接口：

```python
class AgentTool(Protocol):
    name: str
    description: str

    async def run(
        self,
        request: ToolRequest,
        state: AgentState,
    ) -> ToolResult:
        ...
```

---

# 9. Fact RAG：WikiSearchTool

## 9.1 Hybrid Retrieval

原 BM25 保留，并加入 Dense：

```text
Standalone Query
      │
      ├──── BM25 ───────┐
      │                 │
      └──── Dense ──────┤
                        ▼
                 Rank Fusion
                        │
                    Top 20–40
                        │
                        ▼
                 Local Reranker
                        │
                    Top 5–8
```

Dense Embedding 首选：

```text
Qwen3-Embedding-0.6B
```

备选：

```text
BAAI/bge-m3
```

## 9.2 Prometheus 替换

Prometheus 最终不再使用通用生成式 LLM。

首选：

```text
Qwen3-Reranker-0.6B
```

备选：

```text
BAAI/bge-reranker-v2-m3
```

从：

```text
候选文档 → 外部 LLM → 文档名 JSON
```

变为：

```text
query/chunk pair → cross encoder → relevance score
```

优点：

- 稳定；
- 本地；
- 成本低；
- 无 JSON parsing；
- 可直接用 NDCG / Recall 评测。

## 9.3 Chunk Retrieval

增加 `document_chunks`，不要长期只按整文档检索。

建议每 chunk：

- 500–1000 中文字符；
- 10–20% overlap；
- 语义段落优先；
- 保留 date / speaker / section metadata。

---

# 10. Style RAG

这是从 Wiki QA 升级为角色 Agent 的关键。

## 10.1 与 Fact RAG 严格分离

```text
Fact Store
回答：这件事是否发生过

Style Store
回答：这种场景下怎么说更像 Hanser
```

Style Example 不能作为事实证据。

## 10.2 数据来源优先级

```text
真实 Hanser 原始发言
    >
真实对话清洗/重构
    >
源内容约束下 Teacher 生成
    >
纯 Synthetic
```

## 10.3 Style 检索维度

不只按文本语义，还应包含：

- scene；
- user intent；
- speech act；
- tone；
- relationship；
- answer length；
- emotional intensity；
- teasing level；
- factual/casual mode。

例如用户“晚上好”：

```text
scene = greeting
time = late_night
mode = casual
relationship = familiar
length = short
```

取 2–4 条最相关真实样例。

## 10.4 Prompt 注入

```text
<style_examples>

Example 1
User: ...
Hanser: ...

Example 2
User: ...
Hanser: ...

</style_examples>
```

明确说明：

- 只学习表达；
- 不复制事实；
- 不机械复用原句。

---

# 11. Persona System

## 11.1 拆分

不要继续让一个巨型 `persona.md` 承担全部逻辑。

建议：

```text
persona/
├── core.md
├── voice.md
├── behavior.md
├── uncertainty.md
├── boundaries.md
├── relationship.md
└── style_constraints.yaml
```

由 `PersonaCompiler` 编译。

## 11.2 core.md

仅保留稳定核心：

- 身份；
- 长期性格；
- 自我认知；
- 稳定互动定位。

大量生平事实继续留在 Wiki RAG。

## 11.3 voice.md

描述：

- 中文口语程度；
- 标点；
- 停顿；
- 句长；
- 换行；
- 语气词；
- 自嘲；
- 调侃；
- 夸张程度；
- emoji；
- 第一人称习惯。

Hanser 当前强规则应明确：

```text
普通聊天正文原则上不用常规中文标点
使用空格和换行形成停顿
作品名称、代码、URL、必要引用可保留自身格式
```

硬规则同时由 Style Validator 检查。

## 11.4 behavior.md

不要只写人格形容词，要写行为分布：

```text
默认：自然 松弛 普通聊天

上下文适合时才：
- 轻微吐槽
- 自嘲
- 玩梗
- 轻微撒娇

不要每轮：
- 毛怪们
- 黑历史
- 黄梗
- 夸张卖萌
```

## 11.5 PersonaCompiler

```python
class PersonaCompiler:
    def compile(
        self,
        scene_state,
        relationship_state,
        response_mode,
    ) -> PersonaSnapshot:
        ...
```

输出：

```text
Core Identity
+
Voice Invariants
+
Relevant Behavior Rules
+
Current Relationship Mode
+
Current Scene Modifiers
```

---

# 12. Persona 数据工程

这是 Codex 必须实现的离线 pipeline。

## 12.1 Corpus Inventory

遍历：

- SQLite `documents`；
- `.md`；
- `.txt`；
- 已提取 `.docx` 文本；
- Wiki；
- 直播文本；
- 对话记录；
- 人工补充材料。

输出 `data/persona/raw_manifest.jsonl`：

```json
{
  "source_id": "...",
  "source_path": "...",
  "type": "transcript",
  "date": "...",
  "reliability": 0.9
}
```

## 12.2 Speaker Attribution

用规则 + Teacher LLM 区分：

```text
Hanser 自己说的话
别人描述 Hanser 的话
旁白
```

只有可信本人发言进入高质量 Style Corpus。

## 12.3 Dialogue Unit

不要只保存孤立句子。

保存：

- 前文 context；
- 对方话语；
- Hanser response；
- 必要后文；
- 来源。

## 12.4 Linguistic Profile Extractor

从真实语料统计：

- 回复长度分布；
- 标点频率；
- 空格频率；
- 换行频率；
- emoji；
- 语气词；
- 高频 n-gram；
- 自称；
- 用户称呼；
- 反问比例；
- 结尾模式；
- “哈哈”等模式；
- 括号动作比例；
- 玩梗频率。

生成：

```text
persona_stats.json
```

Persona 不再完全依赖开发者主观判断。

---

# 13. 外部 Teacher 模型生成 Persona 数据

外部强模型主要用于**离线 Teacher**，不是在线依赖。

## 13.1 Teacher 任务

### Persona Induction

输入真实发言，输出：

- stable traits；
- interaction patterns；
- scene-dependent behaviors；
- linguistic rules；
- anti-patterns。

### Style Labeling

给真实 dialogue 打：

```text
scene
tone
speech_act
energy
teasing
relationship
answer_length
```

### Coverage Analysis

自动找数据缺口，例如：

```text
已有很多事实类回答
缺少：
- 安慰
- 被夸
- 被吐槽
- 深夜打招呼
- 用户冷淡回应
```

只针对缺口生成 Synthetic。

## 13.2 Synthetic Dialogue

Teacher 输入必须含：

- Core Persona；
- 若干真实 Style Example；
- scene；
- “禁止创造新事实”的约束。

Synthetic 必须标记：

```json
{
  "source_type": "synthetic",
  "teacher_model": "...",
  "generation_prompt_version": "...",
  "fact_free": true
}
```

## 13.3 Filtering

```text
Teacher Generate
      ↓
Rule Filters
      ↓
Duplicate Filter
      ↓
Persona Judge
      ↓
Style Judge
      ↓
Anti-assistantese Judge
      ↓
Sample Human Review
```

---

# 14. Persona Training Strategy

推荐顺序：

```text
Prompt
 ↓
Style RAG
 ↓
Output Validator
 ↓
稳定数据后再 LoRA
```

LoRA 学：

- 语言节奏；
- 说话习惯；
- 回应方式；
- scene → behavior mapping；
- Persona persistence。

不要让 LoRA 负责记全部 Wiki 事实。

## 14.1 SFT Dataset

```json
{
  "messages": [
    {"role": "system", "content": "<compiled persona>"},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

数据混合：

```text
Real Dialogue
+
High-quality Synthetic
+
General Conversation
+
Negative / Correction examples
```

避免过拟合成口癖机器人。

## 14.2 Preference Training

构造：

```text
Chosen:
自然 Hanser 风格

Rejected:
客服腔
过度卖萌
口癖堆砌
标点错误
虚构经历
```

后续可使用 DPO / ORPO 类方法。

---

# 15. Memory Architecture

至少四层：

```text
L0 Recent History
L1 Conversation Summary
L2 Long-term Semantic Memory
L3 Episodic / Relationship Memory
```

## 15.1 Recent History

数据库保存完整历史，ContextBuilder 只注入最近 N 条。

## 15.2 Conversation Summary

长会话定期生成摘要，是上下文压缩，不是长期事实数据库。

## 15.3 Semantic User Memory

例如：

```text
用户更喜欢短回答
用户明天下午有考试
用户更喜欢无普通标点的角色输出
```

只存未来明显有用的信息。

## 15.4 Episodic Memory

例如：

```text
2026-09-04 深夜
双方测试了 Agent Persona
用户指出普通 Chat 与 RAG Chat 人格不一致
```

Episode 对共同经历感很重要。

## 15.5 Relationship State

不要只用“好感度 +10”。

```python
class RelationshipState(BaseModel):
    familiarity: float
    warmth: float
    trust: float
    teasing_permission: float
    shared_context_density: float
    recent_tension: float
```

更新必须保守。

## 15.6 Memory Write Gate

```text
Message
   ↓
Memory Candidate Extractor
   ↓
importance?
future usefulness?
new information?
confidence?
   ↓
Store / Ignore
```

不是每轮都写长期记忆。

## 15.7 Conflict

新信息覆盖旧信息时，旧 memory 标记 `superseded`，不要简单累加冲突。

## 15.8 Retrieval Score

初版可用：

```text
score =
    semantic_relevance * 0.45
  + importance         * 0.25
  + recency            * 0.15
  + confidence         * 0.15
```

后续由 Eval 调整。

---

# 16. Scene State / Character State

“活人感”还需要轻量当前状态：

```python
class SceneState(BaseModel):
    current_topic: str | None
    mood: str
    energy: float
    response_tempo: str
    emotional_context: str | None
    unresolved_threads: list[str]
```

这是行为状态，不是自由文本 Chain-of-Thought。

---

# 17. Initiative / Callback Engine

自然聊天角色不应该永远只回答字面问题，但主动性要有来源。

用户：

```text
我明天下午考试
```

记录：

```text
unresolved_thread:
- 明天下午考试
```

之后在时间合理且话题不冲突时，可自然回调：

```text
考完了没
```

Callback Gate 考虑：

- relevance；
- 时间；
- 当前话题；
- 是否已回调；
- relationship。

---

# 18. ContextBuilder

输入：

```text
PersonaSnapshot
SceneState
RelationshipState
ConversationSummary
RecentHistory
RetrievedMemory
WikiEvidence
StyleExamples
CurrentUserMessage
```

输出：

```python
class ContextBundle(BaseModel):
    messages: list[ChatMessage]
    token_usage_by_block: dict[str, int]
    dropped_blocks: list[str]
```

推荐结构：

```text
SYSTEM

[CORE PERSONA]
稳定 Persona

[VOICE RULES]
稳定说话规则

[BEHAVIOR POLICY]
本轮相关行为规则

[GROUNDING POLICY]
事实规则

[CURRENT CHARACTER STATE]
轻量状态

[RELEVANT USER MEMORY]
长期记忆

[WIKI EVIDENCE]
仅事实问题

[STYLE EXAMPLES]
2–4 条

[CONVERSATION SUMMARY]
必要时

HISTORY
最近真实聊天

USER
当前消息
```

Token Budget 示例：

```yaml
context_budget:
  persona: 1800
  character_state: 300
  memory: 1200
  wiki: 3500
  style_examples: 1800
  summary: 800
  recent_history: 6000
```

具体数字通过目标模型与 Eval 调整。

---

# 19. Unified HanserResponder

必须成为**唯一用户可见自然语言生成器**。

```python
class HanserResponder:
    async def respond(
        self,
        context: ContextBundle,
    ) -> GeneratedResponse:
        ...
```

普通 Chat：

```text
Persona + Memory + Style + History
```

Wiki Fact：

```text
Persona + Memory + Style + History + Wiki Evidence
```

区别只在 Context，不在 Persona 和 Responder。

这一步直接解决当前“两个路由 Persona 不一致”的结构性问题。

---

# 20. Grounding Policy

明确：

```text
Persona 只控制表达方式
不能创造新事实
```

禁止无证据补：

- 心理状态；
- 当时感受；
- 身体状态；
- 动机；
- 具体经历。

如果 Evidence 没有“我当时嗓子都哑了”，就不能因为它听起来很像角色而生成。

---

# 21. Style Validator / Normalizer

硬格式规则不应完全押注模型。

例如：

```text
普通正文不使用 ，。！？；：
```

流程：

```text
Responder
   ↓
Hard Style Checks
   ↓
Grounding Checks
   ↓
Repetition Checks
   ↓
Final Text
```

不要粗暴删除所有符号，必须保留：

- 《作品名》；
- URL；
- code；
- 数字格式；
- source citation；
- 明确引用。

实现 segment-aware normalizer。

---

# 22. 模型分工

## Planner

```text
Qwen3.5-4B
```

负责结构化规划，不生成角色回答。

## Embedding

```text
Qwen3-Embedding-0.6B
```

官方支持 100+ 语言、32K 上下文、最高 1024 embedding dimension。

## Reranker

首选：

```text
Qwen3-Reranker-0.6B
```

备选：

```text
BAAI/bge-reranker-v2-m3
```

Prometheus 最终退出在线生成链路。

---

# 23. Responder Model 策略

不要一开始绑定某个社区模型，必须做 `ResponderModelBenchmark`。

## 候选 A：Qwen3.5-9B + Hanser LoRA

这是主线候选：

- 中文能力强；
- 9B 适合消费级本地；
- 通用理解强；
- 可以使用自己的 Hanser 数据做 LoRA；
- 不依赖未知社区 RP 数据。

## 候选 B：中文 RP Fine-tune

例如：

```text
Givenn/Qwen3-4B-Roleplay-Chinese
```

用于低成本 RP baseline。

## 候选 C：Qwen3 RPG Roleplay

例如：

```text
Chun121/Qwen3-4B-RPG-Roleplay-V2
```

测试 RP fine-tune 是否提高 Character Consistency / Naturalness。

其训练分布不一定适合中文 Hanser 日常，因此必须 A/B。

## 候选 D：Qwen3.5 9B 社区 RP 模型

2026 已出现相关 community checkpoint，可加入 benchmark，但不得因名称带 RolePlay 就直接默认采用。

检查：

- License；
- Training data；
- 中文；
- prompt template；
- repetition；
- hallucination；
- stability。

## 高质量上限

27B Writer/RP、HER-32B 可以作为高质量 benchmark / Teacher，但对 16GB 级 GPU 不应作为第一本地在线部署目标。

---

# 24. Model Profiles

```yaml
models:

  planner:
    provider: local
    endpoint: http://127.0.0.1:8101/v1
    model: qwen3.5-4b
    temperature: 0.1
    max_tokens: 512

  responder:
    provider: local
    endpoint: http://127.0.0.1:8102/v1
    model: hanser-rp
    temperature: 0.65
    max_tokens: 2048

  embedding:
    provider: local
    model: Qwen3-Embedding-0.6B

  reranker:
    provider: local
    model: Qwen3-Reranker-0.6B
```

采样参数只是 baseline，必须按模型 benchmark。

---

# 25. Windows 本地推理与 ModelGateway

Provider 至少支持：

```text
llama.cpp
Ollama
OpenAI-compatible
```

可选：

```text
vLLM under WSL/Linux
```

Agent 不应依赖具体推理框架的专有 API。

```python
class ModelGateway:

    async def generate(
        self,
        profile: str,
        messages: list[ChatMessage],
        **kwargs,
    ):
        ...

    async def generate_json(
        self,
        profile: str,
        messages: list[ChatMessage],
        schema: type[BaseModel],
    ):
        ...

    async def embed(
        self,
        texts: list[str],
    ):
        ...

    async def rerank(
        self,
        query: str,
        passages: list[str],
    ):
        ...
```

---

# 26. GPU Resource Strategy

不要假设所有模型同时常驻 GPU。

优先级：

```text
Responder
>
Planner
>
Reranker
>
Embedding
```

小模型可 CPU 或按需加载。

实现 `ModelResourceManager`：

- keep_alive；
- unload；
- GPU memory budget；
- CPU fallback；
- context length cap。

KV Cache 必须纳入显存预算。

---

# 27. Persistence

继续以 SQLite 作为结构化 Source of Truth。

建议表：

```text
documents
document_chunks
conversations
messages
conversation_summaries
memories
relationship_states
style_examples
persona_versions
tool_traces
evaluation_runs
```

Vector Store 通过接口抽象，可使用：

```text
FAISS
Qdrant local
LanceDB
```

业务层不能直接依赖具体 Vector DB。

---

# 28. Database Schema

## conversations

```sql
id
user_id
title
created_at
updated_at
persona_version
model_profile
```

## messages

```sql
id
conversation_id
role
content
created_at
turn_index
model_name
trace_id
```

## memories

```sql
id
user_id
conversation_id
type
content
importance
confidence
status
created_at
last_accessed_at
source_message_ids_json
embedding_ref
```

## style_examples

```sql
id
prompt
response
scene
speech_act
tone_json
source_type
source_ref
authenticity_score
quality_score
embedding_ref
```

## document_chunks

```sql
id
document_id
chunk_index
text
start_char
end_char
metadata_json
embedding_ref
```

---

# 29. 推荐项目目录

```text
backend/
├── hanser_agent/
│   ├── api/
│   │   ├── chat.py
│   │   ├── conversations.py
│   │   ├── memories.py
│   │   ├── admin.py
│   │   └── debug.py
│   │
│   ├── agent/
│   │   ├── runtime.py
│   │   ├── state.py
│   │   ├── planner.py
│   │   ├── executor.py
│   │   └── context_builder.py
│   │
│   ├── persona/
│   │   ├── compiler.py
│   │   ├── schemas.py
│   │   ├── state_engine.py
│   │   └── loader.py
│   │
│   ├── responder/
│   │   ├── service.py
│   │   ├── validator.py
│   │   ├── grounding.py
│   │   └── normalizer.py
│   │
│   ├── tools/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── wiki_search.py
│   │   ├── memory_search.py
│   │   ├── style_search.py
│   │   ├── time_context.py
│   │   └── web_search.py
│   │
│   ├── retrieval/
│   │   ├── bm25.py
│   │   ├── dense.py
│   │   ├── hybrid.py
│   │   ├── reranker.py
│   │   ├── chunker.py
│   │   └── indexer.py
│   │
│   ├── memory/
│   │   ├── store.py
│   │   ├── extractor.py
│   │   ├── retriever.py
│   │   ├── summarizer.py
│   │   ├── conflict.py
│   │   └── scoring.py
│   │
│   ├── models/
│   │   ├── gateway.py
│   │   ├── profiles.py
│   │   ├── openai_compatible.py
│   │   ├── local_provider.py
│   │   └── structured_output.py
│   │
│   ├── storage/
│   │   ├── sqlite.py
│   │   ├── repositories.py
│   │   └── vector_store.py
│   │
│   ├── observability/
│   │   ├── trace.py
│   │   ├── metrics.py
│   │   └── logger.py
│   │
│   └── config.py
│
├── prompts/
│   ├── planner.md
│   ├── responder_policy.md
│   ├── memory_extractor.md
│   ├── summary.md
│   └── persona/
│       ├── core.md
│       ├── voice.md
│       ├── behavior.md
│       ├── boundaries.md
│       └── style_constraints.yaml
│
├── data/
│   ├── persona/
│   │   ├── raw/
│   │   ├── extracted/
│   │   ├── labeled/
│   │   ├── synthetic/
│   │   └── approved/
│   └── eval/
│
├── scripts/
│   ├── ingest_wiki.py
│   ├── build_style_corpus.py
│   ├── label_dialogues.py
│   ├── synthesize_style_data.py
│   ├── build_vector_index.py
│   ├── benchmark_models.py
│   └── run_eval.py
│
├── training/
│   ├── build_sft_dataset.py
│   ├── train_lora.py
│   ├── build_preference_pairs.py
│   └── merge_adapter.py
│
└── tests/
    ├── unit/
    ├── integration/
    ├── regression/
    └── persona/
```

---

# 30. API

保留：

```text
POST /v1/chat
```

增加：

```text
POST /v1/chat/stream
```

Request：

```json
{
  "conversation_id": "abc",
  "user_id": "local-user",
  "message": "晚上好",
  "debug": false
}
```

Response：

```json
{
  "conversation_id": "abc",
  "turn_id": "...",
  "text": "...",
  "sources": [],
  "trace_id": "..."
}
```

Debug 模式可额外返回 plan / memory / style examples / tool calls / token budget，生产 UI 默认隐藏。

---

# 31. Memory API

至少：

```text
GET    /v1/memories
DELETE /v1/memories/{id}
PATCH  /v1/memories/{id}
```

用户必须能够纠正 Agent 记忆。

---

# 32. Conversation UX

长期支持：

- regenerate；
- swipe alternatives；
- edit message；
- branch chat；
- delete messages；
- export/import；
- reset session state；
- inspect memories；
- inspect sources；
- persona version selection。

---

# 33. Optional Realism Features

## Time Awareness

使用真实 local time / weekday / chat gap。

允许基于真实时间说：

```text
这么晚还没睡
```

不允许凭空声称：

```text
我刚刚出去吃饭了
```

除非系统确实存在对应 state。

## User Persona

用户显式配置的 name / preferred address / basic profile / response preference，应与 Agent 自动推断的 Memory 分开。

## Emotion / Sprite

SceneState 输出结构化 emotion，UI 决定展示哪张图。

## Voice

作为独立 TTS Adapter；若使用真实人物声音，部署和分发需单独考虑授权、隐私和合成内容标识。

---

# 34. Observability

每轮必须生成 `trace_id`。

记录：

```text
planner_latency
planner_output
tool_calls
retrieval_candidates
rerank_scores
selected_style_examples
selected_memories
context_tokens
responder_model
first_token_latency
generation_latency
validator_actions
memory_writes
```

开发时必须能定位问题来自 Planner / Retrieval / Context / Persona / Model / Memory / Postprocess 哪一层。

建议开发端点：

```text
GET /v1/debug/context/{trace_id}
```

展示 Compiled Persona / Character State / Memory / Wiki Evidence / Style Examples / Recent History / Final Model Messages。

---

# 35. Evaluation Framework

不能继续只靠随手聊天。

## Router Eval

场景至少包含：

```text
晚上好
Hanser有哪些配音作品
最早的是哪个
那她为什么这么做
你还记得我明天干嘛吗
```

指标：

- intent accuracy；
- need_wiki precision/recall；
- query rewrite correctness。

## Retrieval Eval

人工构造 100+ Query → relevant docs。

指标：

```text
Recall@5
Recall@20
MRR
NDCG
```

比较：

```text
BM25
Dense
Hybrid
Hybrid + Reranker
```

## Persona Eval

维度：

```text
Character Fidelity
Voice Fidelity
Punctuation Compliance
Assistantese Rate
Catchphrase Overuse
Naturalness
Anthropomorphism
```

## Grounding Eval

对事实回答标：

```text
supported
partially supported
unsupported
```

重点查第一人称经历、心理状态、日期幻觉。

## Memory Eval

测试长期 recall、false memory、conflict resolution、cross-session retention。

## Multi-turn RP Eval

至少 15–20 轮；加入：

- 用户否定角色设定；
- 诱导客服腔；
- 重复问题；
- 情绪突变；
- 事实与闲聊混合；
- long-gap callback；
- contradiction。

---

# 36. Model Benchmark Harness

`benchmark_models.py` 必须保证：

```text
same dataset
same persona
same context
same style examples
same prompt
```

只改变：

```text
model
quantization
sampling preset
```

禁止因 Hugging Face 名字中出现 `RolePlay` 就直接选择。

---

# 37. Prompt / Persona Versioning

所有 Prompt：

```text
name
version
sha256
created_at
```

Persona：

```text
persona_v1
persona_v2
...
```

大改 Persona 不覆盖旧版本，必须跑 Regression Eval。

---

# 38. Codex 实施阶段

## Phase 0：冻结当前 Baseline

- 当前 `/v1/chat` 可用；
- git tag；
- 保存当前 persona；
- 建立最小 regression tests。

## Phase 1：统一 Responder

第一优先级：

```text
普通 Chat ┐
           ├→ HanserResponder
Wiki Chat ┘
```

长期删除 `_hanser_chat()` / `_hanser()` 双自然语言入口。Routing 只改变 Context。

## Phase 2：Prometheus 本地替换

实现 Qwen3-Reranker-0.6B，A/B 对比外部 Prometheus，Retrieval Eval 达标后删除在线 Prometheus。

## Phase 3：Planner 本地化

Bunny / Planner 切 Qwen3.5-4B local，外部 Planner 保留 feature-flag fallback。

## Phase 4：Hybrid Fact RAG

BM25 + Qwen3 Embedding + RRF + Qwen3 Reranker，从 document retrieval 升级 chunk retrieval。

## Phase 5：Persona Data Pipeline + Style RAG

完成：

- corpus scan；
- speaker extraction；
- style labeling；
- style example DB；
- StyleSearchTool；
- PersonaCompiler；
- ContextBuilder 注入。

## Phase 6：Long-term Memory

实现：

- persistent conversation；
- summaries；
- semantic memory；
- episode memory；
- relationship state；
- memory inspector。

## Phase 7：Local RP Responder Benchmark

至少比较：

```text
generic strong model
Chinese RP 4B
RP fine-tune
Qwen3.5-9B + LoRA candidate
```

## Phase 8：Hanser LoRA

Style RAG 与数据 pipeline 稳定后再做。

## Phase 9：高级体验

- branch；
- swipe；
- streaming；
- emotion；
- voice；
- group chat；
- optional web tool；
- plugins。

---

# 39. Codex 必须遵守的工程原则

1. 不一次性推倒现有项目重写。
2. 每个 Phase 保持 `/v1/chat` 可运行。
3. 每个新增模块有 unit test。
4. 每次替换模型先 benchmark。
5. Provider 与 Agent Logic 分离。
6. Persona 与 Wiki Fact 分离。
7. Style RAG 与 Fact RAG 分离。
8. Synthetic Style Data 永远不能作为事实证据。
9. 普通 Chat 与 Wiki RAG 必须共享同一个 Responder。
10. History 不能覆盖 System Persona。
11. Memory 是数据，不是高优先级指令。
12. 长期记忆必须有来源。
13. Tool Result 结构化。
14. 不在业务代码散落模型名/API 地址。
15. Prompt 版本化。
16. Persona 版本化。
17. 大重构前后跑 regression eval。
18. 不为了“Agent 化”引入无意义 multi-agent。
19. 不为了使用框架而使用 LangChain/LangGraph。
20. 用户可见自然语言原则上只有 Responder 生成。

---

# 40. 第一版完整 Runtime 伪代码

```python
async def handle_chat(request):

    state = await state_store.load(
        request.conversation_id
    )

    history = await conversation_store.get_recent(
        request.conversation_id
    )

    plan = await planner.plan(
        current_message=request.message,
        summary=state.summary,
        recent_history=history,
    )

    tasks = []

    if plan.need_wiki:
        tasks.append(
            wiki_tool.run(plan.wiki_query)
        )

    if plan.need_memory:
        tasks.append(
            memory_tool.run(
                plan.memory_query,
                user_id=request.user_id,
            )
        )

    if plan.need_style_examples:
        tasks.append(
            style_tool.run(
                plan.style_query,
                scene=state.scene,
            )
        )

    results = await gather_tools(tasks)

    character_state = state_engine.update_for_turn(
        previous_state=state,
        user_message=request.message,
        plan=plan,
    )

    persona = persona_compiler.compile(
        scene_state=character_state.scene,
        relationship_state=character_state.relationship,
        response_mode=plan.response_mode,
    )

    context = context_builder.build(
        persona=persona,
        character_state=character_state,
        summary=state.summary,
        recent_history=history,
        wiki_evidence=results.wiki,
        memories=results.memory,
        style_examples=results.style,
        current_user_message=request.message,
    )

    generated = await responder.respond(context)

    final_text = style_validator.normalize(
        generated.text
    )

    await conversation_store.append_turn(
        request,
        final_text,
    )

    await post_turn_pipeline.process(
        user_message=request.message,
        assistant_message=final_text,
        state=state,
    )

    return final_text
```

---

# 41. 最终模块关系

```text
Agent Runtime
│
├── State
│   ├── Conversation
│   ├── Summary
│   ├── Relationship
│   └── Scene
│
├── Planner
│
├── Tools
│   ├── WikiSearch
│   ├── MemorySearch
│   ├── StyleSearch
│   └── Optional Tools
│
├── Persona
│   ├── Core
│   ├── Voice
│   ├── Behavior
│   ├── Style Corpus
│   └── Compiler
│
├── ContextBuilder
│
├── Responder
│
├── Validators
│
└── Post-turn
    ├── Memory
    ├── Summary
    └── State Update
```

---

# 42. 最重要的产品判断

## Persona 越完整，不等于 System Prompt 越长

真正高质量结构：

```text
短而稳定 Core Persona
+
真实 Style Example Library
+
动态 Style Retrieval
+
Relationship State
+
Memory
+
Responder Fine-tune
```

而不是一份 10000 字角色说明。

## “活人感”主要来自连续性，而不是更多口癖

优先级：

```text
记得之前聊过什么
>
关系变化合理
>
自然回调之前话题
>
不同场景行为合理
>
真实 Style Examples
>
口癖
```

## 不要让所有模块都用 LLM

```text
Planner        小 LLM
Embedding      Embedding model
Reranker       Reranker model
Responder      RP LLM
Style Check    Rules
Memory         Database
```

## 最值得专项训练的是 Responder

Planner / Reranker 可以用通用模型。

Hanser 专门训练价值主要存在于：

- wording；
- timing；
- social reaction；
- scene behavior；
- conversational rhythm。

---

# 43. 第一版推荐本地组合

建议 baseline：

```text
Planner
Qwen3.5-4B

Embedding
Qwen3-Embedding-0.6B

Reranker
Qwen3-Reranker-0.6B

Responder
Qwen3.5-9B
+
Style RAG

之后：
Qwen3.5-9B + Hanser LoRA
```

同时保留两个专业 RP 模型作为 Benchmark 对照，不立即投入 27B/32B。

---

# 44. 近期最合理开发顺序

基于当前项目进度，严格建议：

```text
1. Unified HanserResponder
2. PersonaCompiler
3. ContextBuilder
4. StyleValidator
5. Prometheus → Local Reranker
6. Planner → Local Small LLM
7. Hybrid RAG
8. Style Corpus Builder
9. Style RAG
10. Persistent Memory
11. Relationship / Scene State
12. Local RP Model Benchmark
13. Hanser LoRA
14. Advanced UI
```

---

# 45. 验收标准

## Architecture

- 普通与 RAG 只有一个 Responder；
- Planner / Tool / Context / Responder 分层；
- Model Provider 可替换；
- Conversation / Memory 可持久化。

## Persona

- 强格式规则稳定；
- 两条路由风格一致；
- 长对话无明显 Persona Drift；
- 不明显客服化；
- 口癖不过度。

## RAG

- Hybrid Retrieval 可配置；
- Reranker 本地；
- Evidence 有 source；
- 无证据事实能正确降级。

## Memory

- 跨会话；
- conflict handling；
- 可编辑/删除；
- false memory regression tests。

## Local

日常运行不依赖外部生成式模型：

```text
Planner local
Reranker local
Responder local
```

外部模型主要做离线 Teacher/Judge/fallback。

## Eval

每次 release 自动跑：

```text
router
retrieval
persona
grounding
memory
multi-turn
```

---

# 46. 不推荐的实现

不要：

```text
一个超长 system prompt
+
全部历史
+
全部 Wiki
+
一个 LLM 解决所有事
```

不要：

```text
普通 chat → responder A
RAG chat   → responder B
```

不要每轮自动写长期记忆。

不要把 Synthetic persona dialogue 当真实经历。

不要只靠 embedding 相似度判断 memory importance。

不要一开始构建复杂 multi-agent swarm。

---

# 47. Codex 顶层任务指令

可直接交给 Codex：

```text
以当前 HanserWiki Python Agent Migration 为基线，严格按照
《Hanser Chat Agent 全量工程架构设计规格》逐阶段扩展。

不要一次性重写项目。

第一目标是保持现有 /v1/chat 可用，同时将当前双回答路径统一为
一个 HanserResponder，并建立 PersonaCompiler 与 ContextBuilder。

之后依次完成本地 Reranker、本地 Planner、Hybrid Fact RAG、
Style Corpus / Style RAG、Persistent Memory、Relationship State、
Local RP Responder Benchmark 与 Hanser LoRA。

所有功能必须模块化、可配置、可回滚、带测试。
事实知识、风格数据、长期记忆必须严格分离。
普通 Chat 和 Wiki RAG 只能在检索上下文上不同，不能使用不同 Persona。
只有 HanserResponder 可以生成最终用户可见自然语言。
```

---

# 48. 参考资料

调研日期：2026-09-04。

## Roleplay / Character Frontend

- SillyTavern — https://github.com/SillyTavern/SillyTavern
- SillyTavern Docs — https://docs.sillytavern.app/
- RisuAI — https://github.com/Kyrosarg/RisuAI

## Character / Persona RAG

- ChatHaruhi — https://github.com/LC1332/Chat-Haruhi-Suzumiya
- Zero-Haruhi — https://github.com/LC1332/Zero-Haruhi

## Agent Memory

- Letta — https://github.com/letta-ai/letta
- LangGraph — https://github.com/langchain-ai/langgraph
- MemPalace — https://github.com/ShinRalexis/silly-tavern-mempalace-extension
- MemoryLayers — https://github.com/SAWA-25/SillyTavern-MemoryLayers
- MemoryBooks — https://github.com/snietzl2/SillyTavernMemoryBooks

## Roleplay Training / Evaluation

- CoSER — https://github.com/Neph0s/CoSER
- HER — https://github.com/cydu24/HER
- InCharacter — https://github.com/Neph0s/InCharacter
- RolePlayBench — https://github.com/MiuLab/RolePlayBench
- RP Benchmark — https://github.com/LeviTheWeasel/rp-benchmark
- TwinBench — https://github.com/TwinVoice/TwinBench

## Retrieval Models

- Qwen3 Embedding 0.6B — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- Qwen3 Reranker 0.6B — https://huggingface.co/Qwen/Qwen3-Reranker-0.6B
- BGE-M3 — https://huggingface.co/BAAI/bge-m3
- BGE Reranker v2 M3 — https://huggingface.co/BAAI/bge-reranker-v2-m3

## Local / RP Candidates

- Qwen3.5-4B — https://huggingface.co/Qwen/Qwen3.5-4B
- Qwen3.5-9B — https://huggingface.co/Qwen/Qwen3.5-9B
- Qwen3.5-27B — https://huggingface.co/Qwen/Qwen3.5-27B
- Qwen3-4B Roleplay Chinese — https://huggingface.co/Givenn/Qwen3-4B-Roleplay-Chinese
- Qwen3-4B RPG Roleplay V2 — https://huggingface.co/Chun121/Qwen3-4B-RPG-Roleplay-V2
- HER-32B — https://huggingface.co/ChengyuDu0123/HER-32B

---

# 49. 最终一句话架构

> **Hanser Agent 不应该是“三个 LLM 串起来回答问题”，而应该是一个有 State、Memory、Persona、Fact RAG、Style RAG、Tool System 和统一 Responder 的角色运行时；事实由检索提供，风格由 Persona + Style Corpus 提供，连续性由 Memory 和 Relationship State 提供，最终只有一个 RP Responder 负责把这些内容自然地说出来。**

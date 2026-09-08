# HanserWiki → Python-first Agent migration

这不是“把 C# 每一行翻译成 Python”，而是把核心运行时迁成 Python，同时保留现有 WPF 作为过渡 UI。

## 迁移后的边界

```text
WPF UI (C#)
    |
    | HTTP localhost:8765
    v
FastAPI
    |
    v
ChatAgentService          <- 后续 Agent 的稳定入口
    |
    v
DialoguePlanner (Local Qwen3.5 via ModelGateway)
    |
    +-> StyleSearchTool (real speech examples only)
    |
    +-> optional WikiSearchTool (chunk BM25 + Dense + RRF + Local Reranker)
    |
    +-> ContextBuilder
    |
    +-> HanserResponder (唯一用户可见生成器)
```

后续阶段继续替换 `ChatAgentService` 内部能力，不需要再改 WPF HTTP 协议：

```text
ChatAgentService
  -> ConversationState
  -> DialoguePlanner
  -> Tool Router
  -> WikiSearchTool
  -> ContextBuilder
  -> Unified HanserResponder
  -> Memory
```

## 为什么先保留 WPF

当前 WPF 已有聊天 UI、历史列表、文档查看、设置窗口。如果同时迁 UI，会把大量时间花在与 Agent 无关的界面重写上。

## 与现有数据库兼容

Python backend 保留原来的 `documents`、`doc_tokens`，并在同一个 SQLite 文件中
增加派生的 `document_chunks`、`chunk_tokens`、`vector_embeddings` 和
`style_examples`。原始文档不迁移，事实向量与风格向量使用不同 collection。

BM25 参数保持：`k1=1.5`, `b=0.75`。

Phase 2 已将在线 Prometheus 替换为进程内的
`Qwen/Qwen3-Reranker-0.6B`。普通聊天不会加载 reranker；第一次 Wiki
检索才会加载模型。`models.reranker.provider: bm25` 是无需模型的显式回滚
配置，旧 Prometheus 只保留在离线 A/B 评测脚本中。

Phase 3 已把在线 Planner 默认切到 Ollama 的 `qwen3.5:4b`。当前模型是
Q4_K_M 量化版本；`context_window` 固定为 4096，`think` 关闭。Planner
默认保活 5 分钟以避免普通聊天反复冷启动；当规划结果需要 Wiki 时，会在
进入本地 reranker 前显式卸载。Planner 只依赖名为 `planner` 的 Model
Profile，不知道 Ollama URL 或具体模型名。

外部 Bunny 仅作为可配置的一跳 fallback：

```yaml
models:
  planner:
    external_fallback: true  # false 可完全关闭
```

`provider: openai_compatible` 同时可连接兼容服务；也可以显式填写
`provider: llama_cpp` 或 `provider: vllm`，不需要修改 `DialoguePlanner`。

### Phase 3 本机基准（2026-09-04）

测试环境为 RTX 3060 Laptop 6GB。`qwen3.5:4b` 实际为 4.7B 参数的
Q4_K_M；在 4096 context 下由 Ollama 以 100% GPU 装载，整卡约占用
4.9GB，卸载后回到约 1.0GB 基线，因此没有继续测试 2B。

| Planner | Schema | Intent | Wiki F1 | Rewrite | Mode | P50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 本地 Qwen3.5-4B Q4_K_M | 1.000 | 1.000 | 1.000 | 0.812 | 0.905 | 2.10s |
| 外部 Bunny 对照 | 0.720 | 0.720 | 0.696 | 0.375 | 0.667 | 2.11s |

数据集共 25 例，覆盖闲聊、Wiki Fact、Follow-up Rewrite、User Memory、
Mixed Intent、Relationship 与 Unknown。评测 profile 禁用 fallback，格式或
路由失败不会被其他模型掩盖。

生产资源策略实测：首次闲聊规划 9.54s，同一保活窗口内第二次为 1.81s；
首次 Wiki 规划连同主动卸载为 9.46s，完成后显存立即回落到基线。

### Phase 4：Hybrid Fact RAG

事实检索已从整篇文档 BM25 改为 500–1000 字符量级的分块检索：

```text
standalone query
  -> chunk BM25 ─┐
                  ├-> Reciprocal Rank Fusion -> Qwen3 Reranker -> Wiki Evidence
  -> Qwen3 Dense ┘
```

当前 562 份文档生成 4,197 个分块。Dense 使用
`Qwen/Qwen3-Embedding-0.6B`，向量维度 1024。在线配置固定为 CPU，避免与
Ollama Planner 或本地 Reranker 争抢 6GB 显存；离线索引可显式使用 CUDA
FP16，不包含自动重试或隐式设备回退。

现有 12 条检索回归集结果：

| Profile | Recall@5 | Recall@20 | MRR | NDCG@5 | P50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| BM25 | 1.000 | 1.000 | 1.000 | 1.000 | 0.03s |
| Dense | 0.583 | 0.667 | 0.450 | 0.480 | 0.06s |
| Hybrid | 1.000 | 1.000 | 0.682 | 0.758 | 0.09s |
| Hybrid + Reranker | 1.000 | 1.000 | 1.000 | 1.000 | 4.01s |

这个小数据集偏词面匹配，不能代替后续 100+ 条困难查询集；它用于保证重构
没有破坏已有事实问题，并验证 Dense/RRF/Reranker 的调用链。

### Phase 5：Persona Data Pipeline 与 Style RAG

离线管线扫描 562 份 SQLite/源文件记录，只从显式“弹幕 → 回应”边界提取
高置信度真实发言。当前得到 886 条 Style Example，并保存 scene、tone、
speech_act、energy、teasing、长度、来源和真实性/质量分数。模糊说话人样本
不进入语料，当前没有 Teacher 生成或 Synthetic 数据。

Style 向量存储在独立 `style_examples` collection；每轮检索 3 条，只作为
`ContextBuilder` 的表达示例。Prompt 明确禁止把样例中的事件、经历或判断当作
事实。事实回答与普通聊天仍进入同一个 `HanserResponder`。

离线产物：

- `backend/data/persona/raw_manifest.jsonl`：562 条来源清单
- `backend/data/persona/persona_stats.json`：886 条真实发言的语言统计
- SQLite `style_examples` 与独立 Style 向量 collection

## 运行

1. 将 `backend/` 放到仓库根目录，例如：

```text
HanserWiki/
  HanserWpf/
  Hanser.Core/
  backend/
```

2. Python 3.11+：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yml config.yml
```

3. 修改 `config.yml`。如果 backend 位于仓库根目录下，示例中的路径应按实际目录调整，例如：

```yaml
data:
  db_path: "../source_data/documents.db"
  data_dir: "../source_data/data"
  userdict_path: "../source_data/userdict.txt"
```

填写 `llm.api_key` 与 `llm.model`。

4. 检查数据库：

```powershell
python scripts/check_db.py
```

首次使用本地 embedding/reranker 时需要下载模型。下载完成后可将相应的
`local_files_only` 设为 `true`，确保后续只读取本地缓存。

首次使用本地 Planner 前安装并启动 Ollama，然后下载量化模型：

```powershell
ollama pull qwen3.5:4b
```

若机器无法装载 4B，可只修改 Model Profile 对照较小版本：

```yaml
models:
  planner:
    model: qwen3.5:2b
```

5. 构建 Fact 与 Style 索引：

```powershell
python scripts/build_fact_index.py --device cuda --dtype float16 --batch-size 16
python scripts/build_style_corpus.py --device cuda --dtype float16 --batch-size 16
```

显存不足时直接把 `--device` 改为 `cpu` 并使用 `--dtype float32`；在线配置
无需随之修改。

6. 启动：

```powershell
python run.py
```

7. 浏览器访问：

```text
http://127.0.0.1:8765/health
```

应看到 `ok: true`。

Hybrid 检索评测：

```powershell
python scripts/eval_hybrid_retrieval.py --device cuda --embedding-dtype float16
```

若需要对照旧外部 Prometheus，可显式运行：

```powershell
python scripts/eval_reranker.py --profiles prometheus
```

Planner 的本地/外部 Router Eval：

```powershell
python scripts/eval_planner.py --profiles local,external
```

批量评测和在线配置都默认让本地 Planner 保持加载 5 分钟。在线链路在
`need_wiki=true` 时主动卸载，以便进入本地 reranker；普通闲聊则复用热模型。

8. 手动测试聊天：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/v1/chat `
  -ContentType 'application/json' `
  -Body '{"conversation_id":"test","message":"hanser什么时候退出VirtuaReal"}'
```

## max_tokens bug 已处理

旧 C# 代码按模型名把部分 DeepSeek 模型的 `max_tokens` 设为 1,000,000，可能超过服务端允许值。

Python 版改成按任务固定预算：

- Bunny: 512
- Prometheus: 1024
- Hanser: 8192

`max_tokens` 表示“单次最大输出”，不再拿模型上下文窗口长度直接作为输出上限。

## WPF 接入

将 `wpf_bridge/PythonAgentClient.cs` 放进 `HanserWpf/`。

然后先把 `MainWindow.SendButton_Click()` 中原有 Bunny/Search/Prometheus/Hanser 编排替换为一次 `_pythonAgent.SendAsync(...)`。

第一阶段建议保留原 C# Core 代码，不删除，方便随时回退和 A/B 验证。等 Python backend 输出与旧链路一致后，再删除旧 Agent 编排。

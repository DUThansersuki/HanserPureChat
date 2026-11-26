# QQ 启动、回复控制与召回记录

2026-11-13 对话调整：取消 QQ 层强制问候扩写；强调直接回应本轮内容，避免泛化邀聊、话题安排及照搬直播收场。新增日志字段 `conversation_contract`。服务按用户要求保持停机，改动在下次启动加载；排查证据与 GitHub 参考见 `CONVERSATION_NATURALNESS_20261113.md`。

2026-11-13 语音更新：仍按短句分别合成和 QA，发送前按顺序拼成一条 WAV。片段间静音默认至少 450 ms，已有自然尾部静音计入间隔；只补足缺少的部分，保留 Voice 时间线要求的更长停顿，最后一段不追加空白。配置为 `gateway.voice_reply.min_pause_ms`，范围 0–2000；设为 0 时只保留原运行时时间线停顿。QQ 一轮只发一条语音；下文“每段分别发送”是 10 月 30 日的历史行为。

2026-10-30 用户需求交付。此次只修改代码和脚本，测试使用固定响应与 HTTP MockTransport，未调用本地或外部生成、向量、重排、语音模型，未启动或重启生产服务。

## 桌面启动

双击桌面 `Hanser-QQ.cmd`，或项目根目录 `Start-QQ.cmd`。

- 启动已安装的 NapCat，自动传入配置中的机器人账号，复用 QQ 已有登录态；这是本机随包 `napcat.quick.bat` / `ReadMe.txt` 支持的快速登录方式。
- 再启动原有本机 API 网关、QQ 后端、Gateway 和 Voice；已运行的 Hanser 服务会复用，重复打开本脚本不会创建第二套服务。
- NapCat WebUI 自动从本机 `webui.json` 读取当前 token，打开带自动登录参数的页面，无需手动填写 token。重复双击桌面入口也可以重新打开；新启动 NapCat 时先等待页面服务就绪。
- QQ 登录态失效或触发账号验证时，仍需在自动打开的 NapCat WebUI 完成扫码/验证；脚本不保存密码。
- 保持启动窗口开启；Ctrl+C 停止本次启动器拥有的聊天服务。NapCat 保留登录状态。
- 本次新代码需要关闭旧聊天服务后重新启动才会加载；脚本不会把正在运行的旧服务热替换。此次没有执行启动验收，以免加载/调用模型。
- 交付时会暂存与已验证代码匹配的 `.runtime/pending_reply_controls.json`。下一次服务停止后的启动，先通过既有发布入口核对代码、原发布状态和冻结载荷，再只刷新同一 user_trial 的代码登记；不会把原 `HOLD` 改成通过。代码或发布状态再次变化会拒绝应用，需要重新验证并暂存。

重新安装桌面入口：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_qq_desktop_launcher.ps1`。

2026-11-02 本机启动修复：Windows 对后端原端口 `8782` 返回 `WinError 10013`。本机 `config.yml` 的 `backend.port` 已改为经 TCP bind/listen 验证可用的 `18782`；Gateway 自动从同一配置读取后端地址，NapCat 的反向 WS 仍为 `8781`。没有改系统端口策略。配置备份为 `.runtime/config.before-backend-port-20261102.yml`。

## 语音模式

| 发给机器人的独立消息 | 效果 |
| --- | --- |
| `/voice` | 在纯语音与概率模式之间切换 |
| `/voice on` | 明确开启纯语音 |
| `/voice off` | 明确恢复概率模式，当前配置为 20% |

命令只接受独立的文本指令；引用中的 `/voice`、代码中的 `/voice` 或“请解释 /voice”不会切换。群聊仍需 @/引用机器人且通过白名单。

切换回执是本地固定文字，不走 Planner、Responder 或 TTS。后续聊天在纯语音模式每轮都请求语音，不再抽概率；模式按私聊/群/成员隔离，重启保留。普通消息和命令即使落在同一合并窗口，也按事件顺序分批处理。

纯语音合成失败时不改发文字：批次标记 `backend_failed / voice_required_unavailable`，原回复快照与召回记录保留。服务恢复后沿用现有 `status` 和 `retry --request-id ...` 维护入口重试，会复用已生成文字。概率模式仍保留原有合成失败转文字行为。完全不可朗读的代码等内容，在纯语音模式也保留失败状态，可关闭模式后继续文字对话。

## 文字与语音拆分

严格模式 `gateway.segmented_reply.strict_short_bubbles=true` 默认启用；本机原 `segmented_reply.enabled=true` 继续控制文字分段总开关。

- 可拆分的逗号、句号、问号、分号、换行、中文稀疏空格等优先提供自然边界。
- 合并后的单条最多两个短句，且正文不超过 15 字（不计空白和常见标点）；超过 15 字的完整不可拆单句单独发送。
- 不再为了满足旧的 `max_segments=4` 把剩余内容重新拼成长尾。严格模式优先于旧的长度/段数设置；关闭严格模式可以回到旧算法。
- URL、代码、成对括号/引用、要求原样保留的片段仍保持完整；精确输出任务继续保留原文。
- 例：`好的。知道了。明白了。` → `好的 知道了` / `明白了`。

语音直接消费同轮已校验 `semantic_text`，不用去标点后的 `display_text`。新 QQ 提示明确要求语义原文保留自然标点；旧式中文空格在语音规划中转为逗号，并记录 source map，不改写快照。QQ 专用 SpeechPlanner 先按短句拆分，再执行既有合成长度边界。每个通过 QA 的合成片段单独成为一条 QQ 语音，按序持久发送；不再拼成一条长 WAV。群聊只在第一条单独 @ 一次。

已确认旧链路并非直接把 `display_text` 喂给 TTS；短句在语音规划阶段被合并，以及上游可能生成的稀疏空格，均可能损失听感。新实现的结构与映射已有确定性测试，实际声学效果未试听、未进行模型评估。

## 波浪线

QQ 的用户偏好层要求默认使用普通句尾，只有确需拖音时极偶尔使用 `~ / ～`，并提示不要照搬检索例子的波浪线；最近四轮助手回复出现过时，本轮提示不用。保留用户要求的原文，不改动冻结 Persona 资产，不把这项产品偏好冒称真人频率统计。此次没有模型调用，实际频率改善待后续聊天观察。

## 召回日志

桌面 `Hanser-QQ-Logs` 打开记录目录。新生成回复的记录位于 `qq_gateway/.runtime/retrieval_logs/`：

- `latest.json`：最近一条新生成回复的记录。
- `<reply_id>.json`：逐回复的完整记录；同请求重试不覆盖原始召回结果。
- `user_message / semantic_reply / display_reply`：输入、语义回复、展示回复及对应请求/会话标识。
- `full_corpus.hits`：全量候选检索命中，含原文、BM25 rank、来源、字符坐标、归属依据及原审核状态；仅当该检索实际执行时存在。
- `searches`：Style、Wiki、Memory 实际查询结果；没执行的标为 `not_requested`，失败保留错误类型。
- `selected`：经过选择后交给上下文构建器的素材全文和元数据。
- `injected.style_ids / source_ids / prompt_blocks`：在上下文预算内实际进入提示的 ID 和文字，另有 `budget_drops` 记录预算淘汰。
- `retrieval_trace`：沿用快照中的检索/排序/代际、排除原因和注入 trace。

这些记录只观察已有查询，不额外检索、不新增模型调用。原文仍保留 user_trial/pending 状态，命中或注入不等于模型实际采纳。命令回执不生成模型回复，对应指令与发送结果沿用 Gateway SQLite 事件/批次账本。记录包含私人聊天与记忆，只写入本机原有 Git 忽略的 `.runtime`。

## 回退与验证

相关离线测试 70 项通过，末尾两处小幅清理后另跑聚焦回归 16 项通过（集合有重叠，不累加）。PowerShell 语法检查及改动文件的 `git diff --check` 通过。记录见 `validation/reply-controls.20261030.json`。

本次没有修改冻结 Persona 包、语料、索引或发布指针，也没有刷新后端/Voice 快照。QQ 专用语音入口只在启动 QQ Voice 时加载短句规划规则，维持现有 owner、快照和 QA 边界。可通过 `strict_short_bubbles=false` 恢复旧文字拆分；可发送 `/voice off` 退出纯语音。完整代码回退需同时恢复 QQ Gateway 本次代码和根启动器，保留数据库与日志；新增会话偏好表与旧代码兼容。

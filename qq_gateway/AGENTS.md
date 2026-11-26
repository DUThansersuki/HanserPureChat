# QQ 文字分支

`chat_QQ` 使用当前工作区最新 `chat_voice_desktop/backend` 的文字能力作为基线。

- 产品范围是 QQ 文字接入、全量语料接入和生产链路可用性。
- QQ 改动集中在此目录；后端通过 `prepare --refresh-backend` 显式快照，使用独立环境和数据库。
- 全量 A13 分类只提供候选检索依据。保留来源坐标、排除和去重账本；正式注入继续遵守项目 Persona 规范的载荷、适用性、评估及发布门禁。
- 用户于 2026-10-18 明确要求本轮验证后不论结果直接切全量试用：允许显式 `--user-trial` 使用 pending 原文，保留原评估和未审核状态，不冒称正式批准；后续微调以用户检查为准，不继续自动循环评估。
- 用户于 2026-10-27 明确授权 QQ 语音回复，按每轮约 20% 的概率使用现有角色语音；主动话题先关闭。允许在本目录增加独立 Voice 消费与运行配置，不增加 Live2D、桌面界面或主动群聊功能；生产链路验证区分本地模拟与真实 QQ 收发。
- 用户于 2026-10-27 授权主 Responder 走 C:/AI-Gateway 的 New API；GPT 明确使用 gpt-6.1-sol，DeepSeek 回退明确使用 deepseek-flash，不自动改成 Astra 或 Pro。Planner 等原调用继续 DeepSeek Flash。仅为固定本机网关增加显式 QQ 消费开关，不修改全局 Codex 配置；Flash 修改后只确认一轮 GPT 角色生成并展示原文，不增加备用调用或语料 A/B。
- 用户于 2026-11-13 明确要求主 Responder 切为 gpt-6-astra 并启动服务；本次为显式模型调整，覆盖先前 gpt-6.1-sol 的主模型选择。Planner 与 deepseek-flash 回退沿用原配置，不自动改动其他模型或全局 Codex 设置。
- 用户于 2026-11-16 明确授权新增RAG语义筛选：最近10轮完整对话（20条历史消息，当前消息另传），QA与纯A先本地各取Top10，再由gpt-6.1-terra各选最多3条，只返回序号。用户确认沿用当前New API＋CLIProxyAPI网关（此前误称CC Switch）；独立rag-selector路由，不改变Astra主回复和DeepSeek Planner，不为筛选自动切换其他模型。
- 用户于2026-11-16进一步明确QA必须由来源Q导向召回；A侧行为通道不能把带Q的记录归为QA候选或混入纯A通道。QA使用独立来源Q索引，A正文及回答行为标签不参与QA排名，保留QA真实配对和来源证据。
- 用户于2026-11-20明确要求主回复模型改为gpt-6.2-astra；New API渠道1的auto-code映射已切换，覆盖此前gpt-6-astra主模型选择。RAG筛选gpt-6.1-terra、Planner和DeepSeek回退保持原配置，不修改全局Codex设置。
- 用户于2026-11-20授权试用台词表达质量改良和粗口优化：QQ现有统一Responder可在原稿后追加至多一次同主模型润色；保留原意、硬边界及合适粗口，最终语义统一供文字/语音消费，记录原稿与润色候选。expression_polish_enabled只在user_trial内生效；粗口保持原强度和15%软目标，补充日常表达机会，不按配额强插。不得把本地模拟测试当成人物自然度效果证明。

# 主 Responder 本机 API 网关

2026-11-20 当前主模型按用户明确要求改为 **gpt-6.2-astra**：New API 渠道1的`auto-code`映射已切换，QQ客户端别名不变。一次Chat Completions最小验证返回HTTP 200、`model=gpt-6.2-astra`、正文`OK`；New API日志89记录渠道1、`upstream_model_name=gpt-6.2-astra`，耗时4.0秒、输入307/输出5 tokens。RAG筛选仍为gpt-6.1-terra，Planner及DeepSeek回退保持原配置。备份与验证记录见`C:/AI-Gateway/astra62-switch.20261120.json`。仅重启New API加载映射，QQ和Voice保持运行；下面保留历史记录。

2026-11-13 当前主模型按用户明确要求改为 **gpt-6-astra**，客户端仍使用 `auto-code` 别名；Planner 和 `deepseek-flash` 回退保持原配置。10 月 27 日的 Sol 选择与验证保留为历史记录。

2026-10-27，按项目根目录 windows_gpt_plus_deepseek_gateway_codex_plan.md 部署在 C:/AI-Gateway。

- 主 Responder → http://127.0.0.1:3000/v1，别名 auto-code → gpt-6.1-sol 优先，deepseek-flash 回退。
- Planner 和其他原调用保留原 DeepSeek Flash。
- 用户明确指定 6.1-sol 和 Flash；不自动改用 Astra 或 Pro。初次默认选择及已发生的验证调用保留为历史记录。
- QQ 消费者沿用 Chat Completions；两层网关同时支持已验证的 Responses 路由。没有修改全局 Codex 配置。

本机 config.yml 的 responder_gateway.enabled=true，api_key_file 引用 C:/AI-Gateway/new-api/local-agent.token。令牌、OAuth 和上游 Key 不写入项目 Git。这个显式开关只允许固定的本机 New API 地址；桌面后端的通用 localhost 校验不变。

Start-QQ.cmd 会先调用 C:/AI-Gateway/scripts/start-all.ps1，然后启动 QQ 服务和开启的 Voice。关闭 QQ 服务保留共用的网关进程；手动停用用 C:/AI-Gateway/scripts/stop-all.ps1。本机运维细节见 C:/AI-Gateway/README.local.md。

完整 QQ 本地套件 94 项通过；启动接线整理后聚焦 3 项通过。实际当前 Persona 生成与 New API 渠道日志核对见 [model-gateway.20261027.json](validation/model-gateway.20261027.json)。未发送真实 QQ 测试消息；保留全量 user_trial、原 HOLD 与未正式批准状态，不重新运行语料 A/B。

回滚时把 responder_gateway.enabled 改为 false 后重启，恢复主 Responder 原 DeepSeek 配置，不改聊天数据。配置修改前备份位于 .runtime/model-gateway-20261027/。

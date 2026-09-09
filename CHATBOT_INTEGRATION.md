# Hanser Agent × Vercel Chatbot 本地接入

当前接入基于 Vercel Chatbot 上游提交 `c2f8235e1f3ea903ad8b7f61447c4f74164b5c58`。

## 启动

双击项目根目录的 `Start-Hanser-Chat.cmd`。启动器会：

1. 首次运行时安装 `chatbot/` 的前端依赖；
2. 启动 `backend/run.py`；
3. 启动 Vercel Chatbot；
4. 后端与前端健康检查通过后打开 `http://127.0.0.1:3000`。

运行日志在 `.runtime/`。关闭启动器窗口或按 Ctrl+C，会结束本次由它启动的进程。

## 接口映射

| Vercel Chatbot | Hanser Agent |
| --- | --- |
| chat id | `conversation_id` |
| 固定本地用户 | `user_id=local-user` |
| user message id | `request_id` |
| UI Message Stream | `/v1/chat` 的 JSON 结果经 Next.js 转成 AI SDK 事件流 |
| 历史侧栏 | `GET /v1/conversations` |
| 页面恢复 | `GET /v1/conversations/{conversation_id}` |
| 回复后处理状态 | `post_turn_status` / `post_turn_retry_id` 经 `data-hanser-meta` 传给 UI |

Hanser 后端仍是会话、Persona、Wiki 检索、长期记忆和幂等状态的唯一真源。前端只发送最新用户消息，不重复发送完整历史。

当前文字聊天入口连接统一 `ChatAgentService`，不从浏览器直接选择 Planner/Responder，也不在前端维护第二套 Persona 或历史。`selectedChatModel` 仅保留 AI SDK 请求兼容值 `hanser/agent`；实际模型、Persona package 和 Style generation 均由后端配置与请求冻结逻辑决定。若回复已持久化但 PostTurn 失败，正文仍正常显示，UI 会明确提示记忆更新待重试，不能把该轮误报为完整成功。

## 本地版取舍

不需要 `AI_GATEWAY_API_KEY`、`POSTGRES_URL`、`REDIS_URL`、`BLOB_READ_WRITE_TOKEN` 或 Auth.js 登录。当前仅开放文本对话和历史读取；附件、公开分享、投票、编辑旧消息和删除会话没有接入，因为现有 Hanser 后端尚无与这些操作一致的语义。其中删除操作还会影响记忆来源追溯，不能只删 UI 记录。

后端目前返回完整 JSON，前端会立刻显示等待状态，收到结果后再写入 AI SDK 消息流；这不等于模型 token 级流式输出。若以后需要逐字流式显示，应先在 `ModelGateway → HanserResponder → ChatAgentService` 补齐原生流式和“回复落库完成”边界，再让 Next.js 透传。

## 可配置项

启动脚本默认使用：

- 后端：`http://127.0.0.1:8765`
- 前端：`http://127.0.0.1:3000`
- 用户：`local-user`

前端服务端也识别 `HANSER_API_BASE_URL` 与 `HANSER_USER_ID` 环境变量。模型、检索、Persona 和记忆设置继续由 `backend/config.yml` 管理，避免前端产生第二套配置真源。

### 成年人轻度双关开关

后端 `/v1/chat` 已接受下面的请求级设置，供后续 Chatbot 设置页接入：

```json
{
  "conversation_id": "conversation-id",
  "message": "晚上好",
  "persona_settings": {
    "adult_innuendo_opt_in": true
  }
}
```

`adult_innuendo_opt_in` 默认是 `false`。设置为 `true` 同时表示当前用户明确声明自己是成年人，并允许非露骨、低强度的成人双关。前端接入时应在每次请求中显式传递当前值；后端会把它纳入请求幂等身份与冻结快照，不能在重试同一 `request_id` 时改变。

这个开关只打开年龄与许可门槛，不表示每轮必须出现黄梗。当前轮明确表示未成年、要求停止或拒绝相关表达时仍会硬禁止；非 playful 语境、情绪低落、关系紧张及事实型回答也不会因为开关开启而放宽。当前仅完成文字聊天后端契约，尚未添加 Chatbot UI 控件。

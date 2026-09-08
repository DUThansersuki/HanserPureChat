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

Hanser 后端仍是会话、Persona、Wiki 检索、长期记忆和幂等状态的唯一真源。前端只发送最新用户消息，不重复发送完整历史。

## 本地版取舍

不需要 `AI_GATEWAY_API_KEY`、`POSTGRES_URL`、`REDIS_URL`、`BLOB_READ_WRITE_TOKEN` 或 Auth.js 登录。当前仅开放文本对话和历史读取；附件、公开分享、投票、编辑旧消息和删除会话没有接入，因为现有 Hanser 后端尚无与这些操作一致的语义。其中删除操作还会影响记忆来源追溯，不能只删 UI 记录。

后端目前返回完整 JSON，前端会立刻显示等待状态，收到结果后再写入 AI SDK 消息流；这不等于模型 token 级流式输出。若以后需要逐字流式显示，应先在 `ModelGateway → HanserResponder → ChatAgentService` 补齐原生流式和“回复落库完成”边界，再让 Next.js 透传。

## 可配置项

启动脚本默认使用：

- 后端：`http://127.0.0.1:8765`
- 前端：`http://127.0.0.1:3000`
- 用户：`local-user`

前端服务端也识别 `HANSER_API_BASE_URL` 与 `HANSER_USER_ID` 环境变量。模型、检索、Persona 和记忆设置继续由 `backend/config.yml` 管理，避免前端产生第二套配置真源。

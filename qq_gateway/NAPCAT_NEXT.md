# NapCat 接入

在 [NapCat 官方仓库](https://github.com/NapNeko/NapCatQQ) 安装程序并登录 QQ。在 NapCat 中选择 OneBot v11 反向 WebSocket 客户端，消息格式使用 Array，地址为 `ws://127.0.0.1:8781/onebot/v11/ws`。已修改端口时使用自己的 Gateway 端口。Access Token 为本机 `qq_gateway/config.yml` 内生成的 `gateway.onebot_access_token`。

配置 `gateway.bot_ids`、`gateway.private_users` 和 `gateway.groups` 时，用字符串填写 QQ 号及群号。空的私聊列表允许全部私聊，空的群列表关闭群回复。允许的群仅响应 @ 机器人或引用机器人消息。

启动 Gateway 后，在 `qq_gateway` 运行 `.\.venv\Scripts\python.exe -m hanser_qq status` 查看连接。可用允许的账号发送一条私聊做收发验证。

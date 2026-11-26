# Hanser QQ Gateway

通过 OneBot v11 接入 QQ。支持私聊、白名单群 @ 与引用触发、连续消息合并、独立记忆、分段发送和本地语音。初始配置关闭主动话题，连接凭据由 `prepare` 在本机生成。

安装、反向 WebSocket 配置及语音启动见[仓库首页](../README.md#qq-网关)。

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
..\Start-QQ.cmd
```

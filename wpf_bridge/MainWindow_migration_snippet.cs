// MainWindow 字段：
private readonly PythonAgentClient _pythonAgent = new();

// SendButton_Click 中原 Bunny -> Search -> Prometheus -> Hanser 部分，迁移后替换为：
var conversationId = _currentChatPath ?? "new-chat";
var response = await _pythonAgent.SendAsync(conversationId, question);

_chatItems.Add(ChatMessageVm.Assistant(response.Text, BuildDocument(response.Text)));

if (response.Sources.Count > 0)
{
    // 这里再将 PythonSearchResult 映射到现有 DocList UI；
    // 第一阶段也可只显示回答，第二 commit 再恢复文档折叠列表。
}

SaveCurrentConversation();
ScrollChatToEnd();

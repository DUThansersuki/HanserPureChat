# Persona v2 Text 生产发布

Persona v2 已按用户直接指令接入聊天生产主链。当前组合为：

- selector：`persona-v2-production`
- package：`hanser-persona-v2-production-20260910`
- Style generation：`persona-v2-style-profanity15-1c71903569618d5d`
- override lifecycle：仅加载 `released`
- Style：`reviewed_only=true`，739 条发布行中 416 条具有当前 generation 向量

发布采用事务性 Style 迁移，没有用候选数据库替换生产数据库。激活前后的聊天记录、Memory 和会话数量分别保持为 54、1、3。发布前备份位于 `source_data/backups/documents.pre_persona_v2.20260909T172550Z.db`。

验证结果：后端 155 项测试通过；Chatbot TypeScript 检查通过；真实工厂加载确认 package、production lifecycle、15% owner target、manifest 和 active Style generation 一致。50 轮冻结日常集实际粗口命中 4 轮，即 8%，用户已明确接受该概率后要求发布。此前 Judge 换位不一致率 37.5% 和“老子/老娘”未在该 50 轮中实际出现仍作为已知限制保留，不被发布状态掩盖。

普通回滚不得恢复整个数据库备份，以免丢失发布后的聊天与偏好。应先运行 `backend/scripts/persona_v2_rollback_text.py` 把 Style 指针恢复为 `legacy-v1`，再把 `persona.active_package` 设置为 `persona-v1-production`。完整哈希与回滚信息见同名 YAML。

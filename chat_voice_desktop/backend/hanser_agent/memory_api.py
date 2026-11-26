from __future__ import annotations

import json
from contextlib import nullcontext

from fastapi import FastAPI, HTTPException
from pydantic import model_validator

from . import db
from .models import MemoryItem, MemoryPatch


class MemoryEdit(MemoryPatch):
    object_value: str | None = None

    @model_validator(mode="after")
    def require_change(self):
        return self if self.object_value is not None else super().require_change()


def attach_memory_routes(app: FastAPI, *, store, retriever=None, agent=None):
    def owned(memory_id: str, user_id: str) -> MemoryItem:
        items = store.active_memories_by_ids(user_id=user_id, memory_ids=[memory_id])
        if not items:
            raise HTTPException(status_code=404, detail="memory not found")
        return items[0]

    def user_lock(user_id: str):
        return agent._user_lock(user_id) if hasattr(agent, "_user_lock") else nullcontext()

    def clear_global_permission(memory: MemoryItem):
        if memory.predicate != "expression_permission" or memory.memory_scope != "global":
            return
        feature, _, target = memory.memory_key.removeprefix("preference:expression:").partition(":")
        with store._connection() as conn:
            rows = conn.execute("""SELECT source_message_id,event_index,event_json FROM persona_permission_events
                WHERE user_id=? AND scope='user' AND json_extract(event_json,'$.feature')=?""",
                (memory.user_id, feature)).fetchall()
            for row in rows:
                event_target = json.loads(row["event_json"]).get("target") or "*"
                if ''.join(event_target.split()).casefold() == (target or "*"):
                    conn.execute("DELETE FROM persona_permission_events WHERE source_message_id=? AND event_index=?",
                                 (row["source_message_id"], row["event_index"]))

    @app.get("/v1/memories", response_model=list[MemoryItem])
    async def list_memories(user_id: str = "local-user", conversation_id: str | None = None):
        return store.list_memories(user_id=user_id, conversation_id=conversation_id)

    @app.patch("/v1/memories/{memory_id}", response_model=MemoryItem)
    async def update_memory(memory_id: str, patch: MemoryEdit, user_id: str = "local-user"):
        async with user_lock(user_id):
            with store.transaction():
                original = owned(memory_id, user_id)
                content = patch.content
                if patch.object_value is not None:
                    value = patch.object_value.strip()
                    if original.predicate == "answer_length" and value in {"用户偏好简短回答", "用户偏好详细回答"}:
                        content = value
                    elif original.predicate == "expression_permission" and value in {"allow", "deny", "unknown"}:
                        feature, _, target = original.memory_key.removeprefix("preference:expression:").partition(":")
                        content = f"用户长期表达偏好：{feature}={value}" + (f"，对象={target}" if target != "*" else "")
                    elif original.predicate == "preferred_address" and value:
                        content = f"用户名字是{value}" if original.address_kind == "personal_name" else f"用户可被称为{value}"
                    else:
                        raise HTTPException(status_code=422, detail="该记忆不支持此结构化值")
                memory = store.update_memory(memory_id, MemoryPatch(
                    content=content, importance=patch.importance, confidence=patch.confidence))
                if patch.object_value is not None:
                    key = original.memory_key
                    if original.predicate == "preferred_address":
                        key = f"address:{original.address_kind}:{''.join(value.split()).casefold()}"
                    with store._connection() as conn:
                        conn.execute("""UPDATE memories SET memory_key=?,predicate=?,subject=?,object_value=?,
                            address_kind=?,context_tags_json=?,address_priority=?,polarity=? WHERE id=?""",
                            (key, original.predicate, original.subject, value, original.address_kind,
                             json.dumps(original.context_tags), original.address_priority,
                             "negative" if original.predicate == "expression_permission" and value == "deny" else "positive", memory.id))
                    memory = owned(memory.id, user_id)
                if patch.content is not None or patch.object_value is not None:
                    clear_global_permission(original)
            if retriever is not None:
                await retriever.index(memory)
                retriever.delete_index(memory_id)
            return memory

    @app.delete("/v1/memories/{memory_id}")
    async def delete_memory(memory_id: str, user_id: str = "local-user"):
        async with user_lock(user_id):
            with store.transaction():
                original = owned(memory_id, user_id)
                store.delete_memory(memory_id)
                clear_global_permission(original)
            if retriever is not None:
                retriever.delete_index(memory_id)
            return {"deleted": memory_id}

    @app.get("/v1/replies/{reply_id}/evidence")
    async def evidence(reply_id: str, user_id: str = "local-user"):
        with db.connect(store.db_path) as conn:
            row = conn.execute("""SELECT s.response_json,s.post_turn_status,f.id AS retry_id
                FROM reply_snapshots s LEFT JOIN post_turn_failures f
                ON f.request_id=s.request_id AND f.status='pending'
                WHERE s.reply_id=? AND s.user_id=?""", (reply_id, user_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="reply not found")
        response = json.loads(row["response_json"])
        return {"sources": response.get("sources", []), "degraded_reasons": response.get("degraded_reasons", []),
                "post_turn_status": "completed" if row["post_turn_status"] == "completed" else "pending_retry",
                "post_turn_retry_id": row["retry_id"]}

    @app.post("/v1/replies/{reply_id}/recover-memory")
    async def recover_memory(reply_id: str, user_id: str = "local-user"):
        state = await evidence(reply_id, user_id)
        if state["post_turn_retry_id"] and hasattr(agent, "retry_post_turn"):
            await agent.retry_post_turn(state["post_turn_retry_id"])
        return await evidence(reply_id, user_id)

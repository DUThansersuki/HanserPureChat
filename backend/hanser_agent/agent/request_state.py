from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .. import db
from ..failures import IdempotencyConflict, RequestInProgress
from ..models import ChatResponse


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RequestClaim:
    trace_id: str
    cached_response: ChatResponse | None = None
    effective_request: dict[str, object] | None = None


class RequestStateStore:
    def __init__(self, db_path: str | Path, *, lease_seconds: float = 300.0):
        self.db_path = Path(db_path)
        self.lease_seconds = lease_seconds

    def claim(
        self,
        *,
        request_id: str,
        user_id: str,
        conversation_id: str,
        request_hash: str,
        trace_id: str,
        effective_request: dict[str, object] | None = None,
    ) -> RequestClaim:
        with db.connect(self.db_path) as conn:
            db.init_db(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM request_executions WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                now = _now()
                conn.execute(
                    """
                    INSERT INTO request_executions
                        (request_id,user_id,conversation_id,request_hash,trace_id,
                         status,effective_request_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,'in_progress',?,?,?)
                    """,
                    (
                        request_id,
                        user_id,
                        conversation_id,
                        request_hash,
                        trace_id,
                        json.dumps(effective_request or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                conn.commit()
                return RequestClaim(
                    trace_id=trace_id,
                    effective_request=effective_request or {},
                )
            if (
                str(row["user_id"]) != user_id
                or str(row["conversation_id"]) != conversation_id
                or str(row["request_hash"]) != request_hash
            ):
                raise IdempotencyConflict()
            status = str(row["status"])
            frozen_request = json.loads(str(row["effective_request_json"] or "{}"))
            if status == "completed" and row["response_json"]:
                return RequestClaim(
                    trace_id=str(row["trace_id"]),
                    cached_response=ChatResponse.model_validate_json(
                        str(row["response_json"])
                    ),
                    effective_request=frozen_request,
                )
            if status == "in_progress":
                committed = conn.execute(
                    """
                    SELECT response_json,post_turn_status
                    FROM reply_snapshots WHERE request_id=? AND request_hash=?
                    """,
                    (request_id, request_hash),
                ).fetchone()
                if committed is None:
                    updated_at = datetime.fromisoformat(str(row["updated_at"]))
                    if datetime.now(timezone.utc) - updated_at < timedelta(
                        seconds=self.lease_seconds
                    ):
                        raise RequestInProgress()
                    conn.execute(
                        """
                        UPDATE request_executions
                        SET trace_id=?,attempts=attempts+1,updated_at=?
                        WHERE request_id=?
                        """,
                        (trace_id, _now(), request_id),
                    )
                    conn.commit()
                    return RequestClaim(
                        trace_id=trace_id,
                        effective_request=frozen_request,
                    )
                response = ChatResponse.model_validate_json(
                    str(committed["response_json"])
                )
                if str(committed["post_turn_status"]) != "completed":
                    response.status = "degraded"
                    response.post_turn_status = "pending_retry"
                    response.degraded_reasons = list(
                        dict.fromkeys(
                            [*response.degraded_reasons, "post_turn_pending_retry"]
                        )
                    )
                return RequestClaim(
                    trace_id=str(row["trace_id"]),
                    cached_response=response,
                    effective_request=frozen_request,
                )
            conn.execute(
                """
                UPDATE request_executions
                SET status='in_progress',trace_id=?,error_code=NULL,
                    attempts=attempts+1,updated_at=?
                WHERE request_id=?
                """,
                (trace_id, _now(), request_id),
            )
            conn.commit()
            return RequestClaim(
                trace_id=trace_id,
                effective_request=frozen_request,
            )

    def complete(
        self,
        request_id: str,
        response: ChatResponse,
        stage_timings: dict[str, float],
    ) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE request_executions
                SET status='completed',response_json=?,error_code=NULL,
                    stage_timings_json=?,updated_at=?
                WHERE request_id=?
                """,
                (
                    response.model_dump_json(),
                    json.dumps(stage_timings, ensure_ascii=False),
                    _now(),
                    request_id,
                ),
            )
            conn.commit()

    def fail(
        self,
        request_id: str,
        error_code: str,
        stage_timings: dict[str, float],
    ) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE request_executions
                SET status='failed',error_code=?,stage_timings_json=?,updated_at=?
                WHERE request_id=?
                """,
                (
                    error_code,
                    json.dumps(stage_timings, ensure_ascii=False),
                    _now(),
                    request_id,
                ),
            )
            conn.commit()

    def record_post_turn_failure(
        self,
        *,
        request_id: str,
        trace_id: str,
        stage: str,
        payload: dict[str, object],
        error: Exception,
    ) -> str:
        failure_id = str(uuid4())
        now = _now()
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO post_turn_failures
                    (id,request_id,trace_id,stage,status,attempts,payload_json,
                     last_error_type,last_error_message,created_at,updated_at)
                VALUES (?,?,?,?,'pending',1,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET
                    stage=excluded.stage,status='pending',
                    attempts=post_turn_failures.attempts+1,
                    payload_json=excluded.payload_json,
                    last_error_type=excluded.last_error_type,
                    last_error_message=excluded.last_error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    failure_id,
                    request_id,
                    trace_id,
                    stage,
                    json.dumps(payload, ensure_ascii=False),
                    type(error).__name__,
                    str(error)[:1000],
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM post_turn_failures WHERE request_id=?", (request_id,)
            ).fetchone()
            return str(row["id"])

    def get_post_turn_failure(self, failure_id: str):
        with db.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT * FROM post_turn_failures WHERE id=?", (failure_id,)
            ).fetchone()

    def list_post_turn_failures(self) -> list[dict[str, object]]:
        with db.connect(self.db_path) as conn:
            now = _now()
            pending = conn.execute(
                """
                SELECT s.request_id,s.post_turn_payload_json,
                       COALESCE(r.trace_id, s.request_id) AS trace_id
                FROM reply_snapshots s
                LEFT JOIN request_executions r ON r.request_id=s.request_id
                LEFT JOIN post_turn_failures f ON f.request_id=s.request_id
                WHERE s.post_turn_status='pending' AND f.request_id IS NULL
                """
            ).fetchall()
            conn.executemany(
                """
                INSERT INTO post_turn_failures
                    (id,request_id,trace_id,stage,status,attempts,payload_json,
                     last_error_type,last_error_message,created_at,updated_at)
                VALUES (?,?,?,'post_turn','pending',1,?,
                        'RecoveredPendingSnapshot',
                        'reply persisted before post-turn completion was recorded',?,?)
                """,
                [
                    (
                        str(uuid4()),
                        str(row["request_id"]),
                        str(row["trace_id"]),
                        str(row["post_turn_payload_json"]),
                        now,
                        now,
                    )
                    for row in pending
                ],
            )
            conn.commit()
            rows = conn.execute(
                """
                SELECT id,request_id,trace_id,stage,status,attempts,
                       last_error_type,last_error_message,created_at,updated_at
                FROM post_turn_failures WHERE status='pending' ORDER BY created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_post_turn_completed(self, failure_id: str) -> None:
        with db.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT request_id FROM post_turn_failures WHERE id=?", (failure_id,)
            ).fetchone()
            conn.execute(
                "UPDATE post_turn_failures SET status='completed',completed_at=?,updated_at=? WHERE id=?",
                (_now(), _now(), failure_id),
            )
            if row is not None:
                request = conn.execute(
                    "SELECT response_json FROM request_executions WHERE request_id=?",
                    (str(row["request_id"]),),
                ).fetchone()
                if request is not None and request["response_json"]:
                    response = ChatResponse.model_validate_json(
                        str(request["response_json"])
                    )
                    response.post_turn_status = "completed"
                    response.post_turn_retry_id = None
                    response.degraded_reasons = [
                        value
                        for value in response.degraded_reasons
                        if value != "post_turn_pending_retry"
                    ]
                    response.status = (
                        "degraded" if response.degraded_reasons else "ok"
                    )
                    conn.execute(
                        "UPDATE request_executions SET response_json=?,updated_at=? WHERE request_id=?",
                        (response.model_dump_json(), _now(), str(row["request_id"])),
                    )
            conn.commit()

    def update_post_turn_error(self, failure_id: str, error: Exception) -> None:
        with db.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE post_turn_failures SET attempts=attempts+1,
                    last_error_type=?,last_error_message=?,updated_at=? WHERE id=?
                """,
                (type(error).__name__, str(error)[:1000], _now(), failure_id),
            )
            conn.commit()

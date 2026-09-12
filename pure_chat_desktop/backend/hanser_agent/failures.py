from __future__ import annotations


class ServiceFailure(RuntimeError):
    """Structured operational failure safe to expose through the API."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int = 503,
        trace_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.status_code = status_code
        self.trace_id = trace_id

    def with_trace(self, trace_id: str) -> "ServiceFailure":
        self.trace_id = trace_id
        return self


class IdempotencyConflict(ServiceFailure):
    def __init__(self, message: str = "request_id conflicts with another request"):
        super().__init__(
            "idempotency_conflict", message, retryable=False, status_code=409
        )


class RequestInProgress(ServiceFailure):
    def __init__(self):
        super().__init__(
            "request_in_progress",
            "the same request is already being processed",
            retryable=True,
            status_code=409,
        )

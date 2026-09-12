from .performance import (
    AllowedPerformance,
    Delivery,
    OutputPreferences,
    PERFORMANCE_SCHEMA_VERSION,
    PerformanceIntent,
    ReplySnapshot,
    SpeechTicket,
)
from .validator import StyleValidator, ValidationResult

__all__ = [
    "AllowedPerformance",
    "Delivery",
    "DisplayAdapter",
    "GeneratedResponse",
    "HanserResponder",
    "OutputPreferences",
    "PERFORMANCE_SCHEMA_VERSION",
    "PerformanceIntent",
    "PerformanceConstraintAdapter",
    "PerformancePolicyResolver",
    "ReplySnapshot",
    "SpeechTicket",
    "StyleValidator",
    "ValidationResult",
]


def __getattr__(name: str):
    """Keep model contracts importable without pulling in the agent service graph."""

    if name in {"GeneratedResponse", "HanserResponder"}:
        from .service import GeneratedResponse, HanserResponder

        return {"GeneratedResponse": GeneratedResponse, "HanserResponder": HanserResponder}[name]
    if name in {"PerformanceConstraintAdapter", "PerformancePolicyResolver"}:
        from .performance_policy import PerformanceConstraintAdapter, PerformancePolicyResolver

        return {
            "PerformanceConstraintAdapter": PerformanceConstraintAdapter,
            "PerformancePolicyResolver": PerformancePolicyResolver,
        }[name]
    if name == "DisplayAdapter":
        from .presentation import DisplayAdapter

        return DisplayAdapter
    raise AttributeError(name)

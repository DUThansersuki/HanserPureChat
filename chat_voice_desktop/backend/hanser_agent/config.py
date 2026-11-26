from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from .local_models import OLLAMA_MODEL, OLLAMA_URL, ollama_origin


@dataclass(slots=True)
class LLMTaskConfig:
    model: str
    max_tokens: int
    temperature: float
    top_p: float


@dataclass(slots=True)
class HybridRetrievalConfig:
    mode: str = "bm25"
    bm25_top_k: int = 40
    dense_top_k: int = 40
    fusion_k: int = 60
    candidate_pool_size: int = 24
    top_k: int = 6
    chunk_target_chars: int = 800
    chunk_max_chars: int = 1000
    chunk_overlap_chars: int = 120


@dataclass(slots=True)
class StyleConfig:
    enabled: bool = True
    reviewed_only: bool = True
    output_dir: Path = Path("data/persona")
    top_k: int = 3
    candidate_pool_size: int = 48
    min_quality: float = 0.68


@dataclass(slots=True)
class MemoryConfig:
    recent_messages: int = 12
    summary_trigger_messages: int = 20
    summary_interval_messages: int = 12
    summary_max_chars: int = 800
    retrieval_top_k: int = 5
    candidate_pool_size: int = 20
    min_importance: float = 0.6
    min_confidence: float = 0.75


@dataclass(slots=True)
class ContextConfig:
    input_token_budget: int = 24576
    output_reserve_tokens: int = 8192
    estimator: str = "utf8_bytes_div_3"
    message_overhead_tokens: int = 4


@dataclass(slots=True)
class PerformanceConfig:
    structured_performance_enabled: bool = False
    speech_runtime_enabled: bool = False
    dynamic_live2d_enabled: bool = False
    offline_export_enabled: bool = False
    render_profile_revision: str = "hanser-render-candidate-1"
    voice_runtime_base_url: str = "http://127.0.0.1:8770"
    internal_token: str = field(default="", repr=False)
    request_timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class PersonaRuntimeConfig:
    active_package: Literal[
        "persona-v1-production",
        "persona-v2-production",
        "hanser-persona-v2-candidate",
    ] = "persona-v2-production"
    candidate_package: Literal["hanser-persona-v2-candidate"] = "hanser-persona-v2-candidate"
    candidate_style_generation: str = "persona-v2-style-profanity15-1c71903569618d5d"
    detector_version: str = "persona_lexical_v4"
    schema_version: int = 2


@dataclass(slots=True)
class ModelProfileConfig:
    provider: str = "openai_compatible"
    endpoint: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str = field(default="", repr=False)
    max_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.9
    context_window: int = 4096
    input_token_budget: int = 3072
    think: bool = False
    request_timeout_seconds: float = 180.0


@dataclass(slots=True)
class Settings:
    root: Path
    db_path: Path
    data_dir: Path
    userdict_path: Path
    base_url: str
    api_key: str
    default_model: str
    bunny: LLMTaskConfig
    prometheus: LLMTaskConfig
    hanser: LLMTaskConfig
    retrieval: HybridRetrievalConfig = field(
        default_factory=HybridRetrievalConfig
    )
    style: StyleConfig = field(default_factory=StyleConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    persona: PersonaRuntimeConfig = field(default_factory=PersonaRuntimeConfig)
    planner: ModelProfileConfig = field(default_factory=ModelProfileConfig)
    responder: ModelProfileConfig | None = None
    host: str = "127.0.0.1"
    port: int = 8765


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _task(raw: dict[str, Any], default_model: str, *, default_max: int, default_temp: float) -> LLMTaskConfig:
    return LLMTaskConfig(
        model=str(raw.get("model") or default_model),
        max_tokens=max(1, int(raw.get("max_tokens") or default_max)),
        temperature=float(raw.get("temperature", default_temp)),
        top_p=float(raw.get("top_p", 0.9)),
    )


def _retrieval(raw: dict[str, Any]) -> HybridRetrievalConfig:
    return HybridRetrievalConfig(
        mode="bm25",
        bm25_top_k=max(1, int(raw.get("bm25_top_k", 40))),
        dense_top_k=max(1, int(raw.get("dense_top_k", 40))),
        fusion_k=max(1, int(raw.get("fusion_k", 60))),
        candidate_pool_size=max(
            1,
            int(raw.get("candidate_pool_size", 24)),
        ),
        top_k=max(1, int(raw.get("top_k", 6))),
        chunk_target_chars=max(
            100,
            int(raw.get("chunk_target_chars", 800)),
        ),
        chunk_max_chars=max(
            100,
            int(raw.get("chunk_max_chars", 1000)),
        ),
        chunk_overlap_chars=max(
            0,
            int(raw.get("chunk_overlap_chars", 120)),
        ),
    )


def _style(raw: dict[str, Any], root: Path) -> StyleConfig:
    return StyleConfig(
        enabled=bool(raw.get("enabled", True)),
        reviewed_only=bool(raw.get("reviewed_only", True)),
        output_dir=_resolve(
            root,
            str(raw.get("output_dir", "data/persona")),
        ),
        top_k=max(1, int(raw.get("top_k", 3))),
        candidate_pool_size=max(
            1,
            int(raw.get("candidate_pool_size", 48)),
        ),
        min_quality=min(
            1.0,
            max(0.0, float(raw.get("min_quality", 0.68))),
        ),
    )


def _memory(raw: dict[str, Any]) -> MemoryConfig:
    return MemoryConfig(
        recent_messages=max(2, int(raw.get("recent_messages", 12))),
        summary_trigger_messages=max(
            4,
            int(raw.get("summary_trigger_messages", 20)),
        ),
        summary_interval_messages=max(
            2,
            int(raw.get("summary_interval_messages", 12)),
        ),
        summary_max_chars=max(200, int(raw.get("summary_max_chars", 800))),
        retrieval_top_k=max(1, int(raw.get("retrieval_top_k", 5))),
        candidate_pool_size=max(
            1,
            int(raw.get("candidate_pool_size", 20)),
        ),
        min_importance=min(
            1.0,
            max(0.0, float(raw.get("min_importance", 0.6))),
        ),
        min_confidence=min(
            1.0,
            max(0.0, float(raw.get("min_confidence", 0.75))),
        ),
    )


def _context(raw: dict[str, Any]) -> ContextConfig:
    estimator = str(raw.get("estimator", "utf8_bytes_div_3"))
    if estimator != "utf8_bytes_div_3":
        raise ValueError(f"不支持的 context token estimator: {estimator}")
    return ContextConfig(
        input_token_budget=max(256, int(raw.get("input_token_budget", 24576))),
        output_reserve_tokens=max(1, int(raw.get("output_reserve_tokens", 8192))),
        estimator=estimator,
        message_overhead_tokens=max(
            0,
            int(raw.get("message_overhead_tokens", 4)),
        ),
    )


def _performance(raw: dict[str, Any]) -> PerformanceConfig:
    return PerformanceConfig(
        structured_performance_enabled=bool(
            raw.get("structured_performance_enabled", False)
        ),
        speech_runtime_enabled=bool(raw.get("speech_runtime_enabled", False)),
        dynamic_live2d_enabled=bool(raw.get("dynamic_live2d_enabled", False)),
        offline_export_enabled=bool(raw.get("offline_export_enabled", False)),
        render_profile_revision=str(
            raw.get("render_profile_revision", "hanser-render-candidate-1")
        ),
        voice_runtime_base_url=str(
            raw.get("voice_runtime_base_url", "http://127.0.0.1:8770")
        ).rstrip("/"),
        internal_token=str(
            raw.get("internal_token")
            or os.getenv("HANSER_VOICE_INTERNAL_TOKEN")
            or ""
        ),
        request_timeout_seconds=max(
            1.0, float(raw.get("request_timeout_seconds", 10.0))
        ),
    )


def _persona(raw: dict[str, Any]) -> PersonaRuntimeConfig:
    active = str(raw.get("active_package", "persona-v2-production"))
    candidate = str(raw.get("candidate_package", "hanser-persona-v2-candidate"))
    allowed_active = {
        "persona-v1-production",
        "persona-v2-production",
        "hanser-persona-v2-candidate",
    }
    if active not in allowed_active:
        raise ValueError(f"未知 persona.active_package: {active}")
    if candidate != "hanser-persona-v2-candidate":
        raise ValueError(f"未知 persona.candidate_package: {candidate}")
    return PersonaRuntimeConfig(
        active_package=active,  # type: ignore[arg-type]
        candidate_package=candidate,  # type: ignore[arg-type]
        candidate_style_generation=str(
            raw.get(
                "candidate_style_generation",
                "persona-v2-style-profanity15-1c71903569618d5d",
            )
        ),
        detector_version=str(raw.get("detector_version", "persona_lexical_v4")),
        schema_version=int(raw.get("schema_version", 2)),
    )


def _model_profile(
    raw: dict[str, Any],
    *,
    provider: str,
    endpoint: str,
    model: str,
    api_key: str = "",
) -> ModelProfileConfig:
    configured_provider = str(raw.get("provider", provider))
    if configured_provider != "openai_compatible":
        raise ValueError(
            "Pure Chat 仅支持 openai_compatible 外部模型 provider"
        )
    configured_endpoint = str(raw.get("endpoint", endpoint)).rstrip("/")
    parsed_endpoint = urlsplit(configured_endpoint)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.hostname:
        raise ValueError("Pure Chat 外部模型 endpoint 必须是有效的 HTTP(S) URL")
    if parsed_endpoint.hostname.casefold() in {"localhost", "127.0.0.1", "::1"} and not ollama_origin(configured_endpoint):
        raise ValueError("本机 loopback 模型仅支持 Ollama 的 11434/v1 端点")
    return ModelProfileConfig(
        provider=configured_provider,
        endpoint=configured_endpoint,
        model=str(raw.get("model") or model),
        api_key=str(raw.get("api_key") or api_key),
        max_tokens=max(1, int(raw.get("max_tokens", 512))),
        temperature=float(raw.get("temperature", 0.1)),
        top_p=float(raw.get("top_p", 0.9)),
        context_window=max(512, int(raw.get("context_window", 4096))),
        input_token_budget=max(256, int(raw.get("input_token_budget", 3072))),
        think=bool(raw.get("think", False)),
        request_timeout_seconds=max(
            1.0, float(raw.get("request_timeout_seconds", 180.0))
        ),
    )


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("HANSER_CONFIG", "config.yml")).resolve()
    if not config_path.exists():
        raise FileNotFoundError(
            f"找不到配置文件 {config_path}；请复制 config.example.yml 为 config.yml 后填写 API。"
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = config_path.parent
    data = raw.get("data", {})
    llm = raw.get("llm", {})
    models = raw.get("models", {})
    server = raw.get("server", {})
    performance_raw = raw.get("performance", {})
    persona_raw = raw.get("persona", {})

    base_url = str(llm.get("base_url") or os.getenv("OPENAI_BASE_URL") or OLLAMA_URL)
    local = ollama_origin(base_url) is not None
    api_key = "" if local else str(llm.get("api_key") or os.getenv("OPENAI_API_KEY") or "")
    model = str(llm.get("model") or os.getenv("OPENAI_MODEL") or (OLLAMA_MODEL if local else ""))
    if not api_key and not local:
        raise ValueError("未配置 llm.api_key / OPENAI_API_KEY")
    if not model:
        raise ValueError("未配置 llm.model / OPENAI_MODEL")

    bunny = _task(
        llm.get("bunny", {}),
        model,
        default_max=512,
        default_temp=0.1,
    )
    planner_raw = models.get("planner", {})
    planner = _model_profile(
        planner_raw,
        provider="openai_compatible",
        endpoint=base_url,
        model=bunny.model,
        api_key=api_key,
    )
    hanser = _task(
        llm.get("hanser", {}),
        model,
        default_max=8192,
        default_temp=0.3,
    )
    responder_raw = {
        "max_tokens": hanser.max_tokens,
        "temperature": hanser.temperature,
        "top_p": hanser.top_p,
        **models.get("responder", {}),
    }
    responder = _model_profile(
        responder_raw,
        provider="openai_compatible",
        endpoint=base_url,
        model=hanser.model,
        api_key=api_key,
    )
    for profile in (planner, responder):
        if ollama_origin(profile.endpoint):
            profile.api_key = ""
        elif not profile.api_key:
            raise ValueError("外部模型需要配置 API Key")

    return Settings(
        root=root,
        db_path=_resolve(root, str(data.get("db_path", "documents.db"))),
        data_dir=_resolve(root, str(data.get("data_dir", "data"))),
        userdict_path=_resolve(root, str(data.get("userdict_path", "userdict.txt"))),
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        default_model=model,
        bunny=bunny,
        prometheus=_task(llm.get("prometheus", {}), model, default_max=1024, default_temp=0.1),
        hanser=hanser,
        retrieval=_retrieval(raw.get("retrieval", {})),
        style=_style(raw.get("style", {}), root),
        memory=_memory(raw.get("memory", {})),
        context=_context(raw.get("context", {})),
        performance=_performance(performance_raw),
        persona=_persona(persona_raw),
        planner=planner,
        responder=responder,
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8765)),
    )

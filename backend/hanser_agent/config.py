from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class LLMTaskConfig:
    model: str
    max_tokens: int
    temperature: float
    top_p: float


@dataclass(slots=True)
class RerankerConfig:
    provider: str = "local"
    model: str = "Qwen/Qwen3-Reranker-0.6B"
    device: str = "auto"
    dtype: str = "auto"
    max_length: int = 768
    batch_size: int = 8
    candidate_pool_size: int = 20
    top_k: int = 5
    local_files_only: bool = False
    instruction: str = (
        "Given a Chinese Hanser Wiki question, retrieve passages that "
        "contain evidence needed to answer the question"
    )
    release_after_request: bool = False


@dataclass(slots=True)
class EmbeddingConfig:
    provider: str = "local"
    model: str = "Qwen/Qwen3-Embedding-0.6B"
    device: str = "cpu"
    dtype: str = "float32"
    max_length: int = 1024
    batch_size: int = 8
    dimension: int = 1024
    local_files_only: bool = False
    instruction: str = (
        "Given a Chinese Hanser Wiki question, retrieve passages that "
        "contain evidence needed to answer the question"
    )


@dataclass(slots=True)
class HybridRetrievalConfig:
    mode: str = "hybrid"
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
    reviewed_only: bool = False
    output_dir: Path = Path("data/persona")
    top_k: int = 3
    candidate_pool_size: int = 16
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
class ModelProfileConfig:
    provider: str = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:4b"
    api_key: str = field(default="", repr=False)
    max_tokens: int = 512
    temperature: float = 0.1
    top_p: float = 0.9
    context_window: int = 4096
    keep_alive: str = "5m"
    think: bool = False
    fallback_profile: str | None = None
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
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: HybridRetrievalConfig = field(
        default_factory=HybridRetrievalConfig
    )
    style: StyleConfig = field(default_factory=StyleConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    planner: ModelProfileConfig = field(default_factory=ModelProfileConfig)
    planner_external: ModelProfileConfig | None = None
    responder: ModelProfileConfig | None = None
    responder_benchmarks: dict[str, ModelProfileConfig] = field(
        default_factory=dict
    )
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


def _reranker(raw: dict[str, Any]) -> RerankerConfig:
    return RerankerConfig(
        provider=str(raw.get("provider", "local")),
        model=str(raw.get("model", "Qwen/Qwen3-Reranker-0.6B")),
        device=str(raw.get("device", "auto")),
        dtype=str(raw.get("dtype", "auto")),
        max_length=max(256, int(raw.get("max_length", 768))),
        batch_size=max(1, int(raw.get("batch_size", 8))),
        candidate_pool_size=max(1, int(raw.get("candidate_pool_size", 20))),
        top_k=max(1, int(raw.get("top_k", 5))),
        local_files_only=bool(raw.get("local_files_only", False)),
        instruction=str(
            raw.get("instruction")
            or RerankerConfig().instruction
        ),
        release_after_request=bool(raw.get("release_after_request", False)),
    )


def _embedding(raw: dict[str, Any]) -> EmbeddingConfig:
    return EmbeddingConfig(
        provider=str(raw.get("provider", "local")),
        model=str(raw.get("model", "Qwen/Qwen3-Embedding-0.6B")),
        device=str(raw.get("device", "cpu")),
        dtype=str(raw.get("dtype", "float32")),
        max_length=max(128, int(raw.get("max_length", 1024))),
        batch_size=max(1, int(raw.get("batch_size", 8))),
        dimension=max(32, int(raw.get("dimension", 1024))),
        local_files_only=bool(raw.get("local_files_only", False)),
        instruction=str(
            raw.get("instruction")
            or EmbeddingConfig().instruction
        ),
    )


def _retrieval(raw: dict[str, Any]) -> HybridRetrievalConfig:
    return HybridRetrievalConfig(
        mode=str(raw.get("mode", "hybrid")),
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
        reviewed_only=bool(raw.get("reviewed_only", False)),
        output_dir=_resolve(
            root,
            str(raw.get("output_dir", "data/persona")),
        ),
        top_k=max(1, int(raw.get("top_k", 3))),
        candidate_pool_size=max(
            1,
            int(raw.get("candidate_pool_size", 16)),
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


def _model_profile(
    raw: dict[str, Any],
    *,
    provider: str,
    endpoint: str,
    model: str,
    api_key: str = "",
    fallback_profile: str | None = None,
) -> ModelProfileConfig:
    return ModelProfileConfig(
        provider=str(raw.get("provider", provider)),
        endpoint=str(raw.get("endpoint", endpoint)).rstrip("/"),
        model=str(raw.get("model") or model),
        api_key=str(raw.get("api_key") or api_key),
        max_tokens=max(1, int(raw.get("max_tokens", 512))),
        temperature=float(raw.get("temperature", 0.1)),
        top_p=float(raw.get("top_p", 0.9)),
        context_window=max(512, int(raw.get("context_window", 4096))),
        keep_alive=str(raw.get("keep_alive", "5m")),
        think=bool(raw.get("think", False)),
        fallback_profile=fallback_profile,
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

    base_url = str(llm.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    api_key = str(llm.get("api_key") or os.getenv("OPENAI_API_KEY") or "")
    model = str(llm.get("model") or os.getenv("OPENAI_MODEL") or "")
    if not api_key:
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
    external_fallback = bool(planner_raw.get("external_fallback", True))
    planner = _model_profile(
        planner_raw,
        provider="ollama",
        endpoint="http://127.0.0.1:11434",
        model="qwen3.5:4b",
        fallback_profile=(
            "planner_external"
            if external_fallback
            else None
        ),
    )
    planner_external = (
        ModelProfileConfig(
            provider="openai_compatible",
            endpoint=base_url.rstrip("/"),
            model=bunny.model,
            api_key=api_key,
            max_tokens=bunny.max_tokens,
            temperature=bunny.temperature,
            top_p=bunny.top_p,
            context_window=4096,
        )
        if external_fallback
        else None
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
    responder_benchmarks = {
        str(name): _model_profile(
            dict(profile),
            provider="ollama",
            endpoint="http://127.0.0.1:11434",
            model=str(profile.get("model") or name),
        )
        for name, profile in models.get("responder_benchmarks", {}).items()
    }

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
        reranker=_reranker(models.get("reranker", {})),
        embedding=_embedding(models.get("embedding", {})),
        retrieval=_retrieval(raw.get("retrieval", {})),
        style=_style(raw.get("style", {}), root),
        memory=_memory(raw.get("memory", {})),
        context=_context(raw.get("context", {})),
        planner=planner,
        planner_external=planner_external,
        responder=responder,
        responder_benchmarks=responder_benchmarks,
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8765)),
    )

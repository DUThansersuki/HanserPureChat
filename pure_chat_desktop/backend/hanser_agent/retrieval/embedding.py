from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import EmbeddingConfig


class Embedder(Protocol):
    model_name: str

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(frozen=True, slots=True)
class _EmbeddingRuntime:
    torch: Any
    tokenizer: Any
    model: Any


class LocalQwenEmbedder:
    """Qwen3 embedding model with instruction-aware query encoding."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.model_name = config.model
        self._runtime: _EmbeddingRuntime | None = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_limit = 512

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        instructed = [
            f"Instruct: {self.config.instruction}\nQuery: {text}"
            for text in texts
        ]
        return await self._cached_encode("query", instructed)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._cached_encode("document", texts)

    async def warmup(self) -> None:
        await self.embed_queries(["Hanser 对话检索预热"])

    async def _cached_encode(self, namespace: str, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing_texts: list[str] = []
        missing_indexes: list[int] = []
        with self._inference_lock:
            for index, value in enumerate(texts):
                key = (namespace, value)
                cached = self._cache.get(key)
                if cached is None:
                    missing_texts.append(value)
                    missing_indexes.append(index)
                else:
                    self._cache.move_to_end(key)
                    results[index] = cached
        if missing_texts:
            vectors = await asyncio.to_thread(self._encode, missing_texts)
            with self._inference_lock:
                for index, value, vector in zip(missing_indexes, missing_texts, vectors, strict=True):
                    self._cache[(namespace, value)] = vector
                    results[index] = vector
                while len(self._cache) > self._cache_limit:
                    self._cache.popitem(last=False)
        return [vector for vector in results if vector is not None]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        runtime = self._get_runtime()
        vectors: list[list[float]] = []
        with self._inference_lock:
            for start in range(0, len(texts), self.config.batch_size):
                vectors.extend(
                    self._encode_batch(
                        texts[start : start + self.config.batch_size],
                        runtime,
                    )
                )
        return vectors

    def _get_runtime(self) -> _EmbeddingRuntime:
        if self._runtime is not None:
            return self._runtime
        with self._load_lock:
            if self._runtime is not None:
                return self._runtime

            import torch
            from transformers import AutoModel, AutoTokenizer

            device_name = self.config.device
            if device_name == "auto":
                device_name = "cuda" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)
            dtype = getattr(torch, self.config.dtype)
            tokenizer = AutoTokenizer.from_pretrained(
                self.config.model,
                padding_side="left",
                local_files_only=self.config.local_files_only,
            )
            model = AutoModel.from_pretrained(
                self.config.model,
                dtype=dtype,
                local_files_only=self.config.local_files_only,
            ).to(device).eval()
            self._runtime = _EmbeddingRuntime(
                torch=torch,
                tokenizer=tokenizer,
                model=model,
            )
            return self._runtime

    def _encode_batch(
        self,
        texts: list[str],
        runtime: _EmbeddingRuntime,
    ) -> list[list[float]]:
        torch = runtime.torch
        inputs = runtime.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.config.max_length,
            return_tensors="pt",
        )
        inputs = {
            key: value.to(runtime.model.device)
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            outputs = runtime.model(**inputs)
            hidden = outputs.last_hidden_state
            mask = inputs["attention_mask"]
            if bool((mask[:, -1].sum() == mask.shape[0]).item()):
                pooled = hidden[:, -1]
            else:
                sequence_lengths = mask.sum(dim=1) - 1
                pooled = hidden[
                    torch.arange(hidden.shape[0], device=hidden.device),
                    sequence_lengths,
                ]
            pooled = pooled[:, : self.config.dimension]
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        return pooled.float().cpu().tolist()


def build_embedder(config: EmbeddingConfig) -> Embedder:
    if config.provider == "local":
        return LocalQwenEmbedder(config)
    raise ValueError(
        f"不支持的 embedding provider: {config.provider}; 当前支持 local"
    )

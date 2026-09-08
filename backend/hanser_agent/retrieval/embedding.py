from __future__ import annotations

import asyncio
import threading
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

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        instructed = [
            f"Instruct: {self.config.instruction}\nQuery: {text}"
            for text in texts
        ]
        return await asyncio.to_thread(self._encode, instructed)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode, texts)

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

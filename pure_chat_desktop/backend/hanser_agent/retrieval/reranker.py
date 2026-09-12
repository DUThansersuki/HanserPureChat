from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import RerankerConfig


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    document_id: int
    filename: str
    text: str
    retrieval_score: float
    chunk_id: int | None = None


@dataclass(frozen=True, slots=True)
class RerankHit:
    document_id: int
    filename: str
    score: float
    chunk_id: int | None = None


@dataclass(frozen=True, slots=True)
class _QwenRuntime:
    torch: Any
    tokenizer: Any
    model: Any
    token_false_id: int
    token_true_id: int
    prefix_tokens: list[int]
    suffix_tokens: list[int]


class Reranker(Protocol):
    name: str

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        ...

    async def unload(self) -> None:
        ...


class BM25Reranker:
    """Deterministic no-model profile used for comparison and rollback."""

    name = "bm25"

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        del query
        ranked = sorted(
            candidates,
            key=lambda item: item.retrieval_score,
            reverse=True,
        )
        return [
            RerankHit(
                document_id=item.document_id,
                filename=item.filename,
                score=item.retrieval_score,
                chunk_id=item.chunk_id,
            )
            for item in ranked[:top_k]
        ]

    async def unload(self) -> None:
        return None


class LocalQwenReranker:
    """Qwen3 reranker running in-process through Transformers.

    The model is loaded on the first factual request so health checks and casual
    chat do not reserve GPU memory. Inference is serialized because the model is
    shared by all requests in this process.
    """

    name = "qwen3_local"

    _PREFIX = (
        '<|im_start|>system\nJudge whether the Document meets the requirements '
        'based on the Query and the Instruct provided. Note that the answer can '
        'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    )
    _SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def __init__(self, config: RerankerConfig):
        self.config = config
        self._runtime: _QwenRuntime | None = None
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        top_k: int,
    ) -> list[RerankHit]:
        if not candidates:
            return []
        return await asyncio.to_thread(
            self._rerank_sync,
            query,
            candidates,
            top_k,
        )

    async def unload(self) -> None:
        await asyncio.to_thread(self._unload_sync)

    def _unload_sync(self) -> None:
        with self._inference_lock:
            runtime = self._runtime
            self._runtime = None
            if runtime is None:
                return
            import gc

            del runtime
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    def _rerank_sync(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int,
    ) -> list[RerankHit]:
        with self._inference_lock:
            runtime = self._get_runtime()
            scores: list[float] = []
            for start in range(0, len(candidates), self.config.batch_size):
                batch = candidates[start : start + self.config.batch_size]
                scores.extend(
                    self._score_batch(
                        query,
                        batch,
                        runtime,
                    )
                )

        ranked = sorted(
            zip(candidates, scores, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            RerankHit(
                document_id=item.document_id,
                filename=item.filename,
                score=round(score, 6),
                chunk_id=item.chunk_id,
            )
            for item, score in ranked[:top_k]
        ]

    def _get_runtime(
        self,
    ) -> _QwenRuntime:
        if self._runtime is not None:
            return self._runtime

        with self._load_lock:
            if self._runtime is not None:
                return self._runtime

            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            device_name = self.config.device
            if device_name == "auto":
                device_name = "cuda" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_name)

            dtype_name = self.config.dtype
            if dtype_name == "auto":
                dtype = torch.float16 if device.type == "cuda" else torch.float32
            else:
                dtype = getattr(torch, dtype_name)

            tokenizer = AutoTokenizer.from_pretrained(
                self.config.model,
                padding_side="left",
                local_files_only=self.config.local_files_only,
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.config.model,
                dtype=dtype,
                local_files_only=self.config.local_files_only,
            ).to(device).eval()

            token_false_id = tokenizer.convert_tokens_to_ids("no")
            token_true_id = tokenizer.convert_tokens_to_ids("yes")
            prefix_tokens = tokenizer.encode(
                self._PREFIX,
                add_special_tokens=False,
            )
            suffix_tokens = tokenizer.encode(
                self._SUFFIX,
                add_special_tokens=False,
            )
            self._runtime = _QwenRuntime(
                torch=torch,
                tokenizer=tokenizer,
                model=model,
                token_false_id=token_false_id,
                token_true_id=token_true_id,
                prefix_tokens=prefix_tokens,
                suffix_tokens=suffix_tokens,
            )
            return self._runtime

    def _score_batch(
        self,
        query: str,
        candidates: list[RerankCandidate],
        runtime: _QwenRuntime,
    ) -> list[float]:
        torch = runtime.torch
        tokenizer = runtime.tokenizer
        model = runtime.model
        pairs = [
            self._format_instruction(query, candidate.text)
            for candidate in candidates
        ]
        inputs = tokenizer(
            pairs,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=(
                self.config.max_length
                - len(runtime.prefix_tokens)
                - len(runtime.suffix_tokens)
            ),
        )
        for index, token_ids in enumerate(inputs["input_ids"]):
            inputs["input_ids"][index] = (
                runtime.prefix_tokens + token_ids + runtime.suffix_tokens
            )
        inputs = tokenizer.pad(
            inputs,
            padding=True,
            return_tensors="pt",
        )
        inputs = {
            key: value.to(model.device)
            for key, value in inputs.items()
        }

        with torch.inference_mode():
            logits = model(**inputs).logits[:, -1, :]
            yes_no = torch.stack(
                [
                    logits[:, runtime.token_false_id],
                    logits[:, runtime.token_true_id],
                ],
                dim=1,
            )
            scores = torch.nn.functional.log_softmax(
                yes_no,
                dim=1,
            )[:, 1].exp()
        return [float(score) for score in scores.cpu().tolist()]

    def _format_instruction(self, query: str, document: str) -> str:
        return (
            f"<Instruct>: {self.config.instruction}\n"
            f"<Query>: {query}\n"
            f"<Document>: {document}"
        )


def build_reranker(config: RerankerConfig) -> Reranker:
    if config.provider == "local":
        return LocalQwenReranker(config)
    if config.provider == "bm25":
        return BM25Reranker()
    raise ValueError(
        f"不支持的 reranker provider: {config.provider}; 可选 local / bm25"
    )

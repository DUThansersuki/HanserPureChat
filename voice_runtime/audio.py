from __future__ import annotations

import os
import secrets
from pathlib import Path

from .contracts import AudioArtifact
from .qa import basic_audio_qa


class AudioArtifactStore:
    """Owner-isolated, QA-gated PCM artifact cache."""

    def __init__(self, root: str | Path, *, package_sample_rate: int = 48000):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.package_sample_rate = package_sample_rate

    def find(self, owner: str, cache_key: str) -> AudioArtifact | None:
        manifest = self._owner_dir(owner) / "keys" / f"{cache_key}.json"
        if not manifest.is_file():
            return None
        artifact = AudioArtifact.model_validate_json(manifest.read_text(encoding="utf-8"))
        path = self.root / artifact.relative_path
        return artifact if path.is_file() and path.stat().st_size > 44 else None

    def publish(
        self,
        *,
        owner: str,
        cache_key: str,
        pcm,
        native_sample_rate: int,
    ) -> AudioArtifact:
        import numpy as np
        import soundfile as sf

        if native_sample_rate != self.package_sample_rate:
            raise ValueError(
                "native sample rate differs from package rate; configure one explicit resampler"
            )
        qa = basic_audio_qa(pcm, native_sample_rate)
        if qa.status != "passed":
            raise ValueError("audio QA failed: " + ",".join(qa.reasons))
        samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
        owner_dir = self._owner_dir(owner)
        audio_dir = owner_dir / "audio"
        key_dir = owner_dir / "keys"
        audio_dir.mkdir(parents=True, exist_ok=True)
        key_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = secrets.token_urlsafe(24)
        final_path = audio_dir / f"{artifact_id}.wav"
        temporary = audio_dir / f".{artifact_id}.tmp.wav"
        sf.write(temporary, samples, self.package_sample_rate, subtype="PCM_16")
        os.replace(temporary, final_path)
        artifact = AudioArtifact(
            artifact_id=artifact_id,
            relative_path=final_path.relative_to(self.root).as_posix(),
            sample_rate=self.package_sample_rate,
            channels=1,
            sample_count=int(samples.size),
            native_sample_rate=native_sample_rate,
            qa_status="passed",
        )
        key_tmp = key_dir / f".{cache_key}.tmp.json"
        key_final = key_dir / f"{cache_key}.json"
        key_tmp.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        os.replace(key_tmp, key_final)
        return artifact

    def resolve(self, owner: str, artifact_id: str) -> Path | None:
        audio_dir = self._owner_dir(owner) / "audio"
        path = (audio_dir / f"{artifact_id}.wav").resolve()
        if path.parent != audio_dir.resolve() or not path.is_file():
            return None
        return path

    def _owner_dir(self, owner: str) -> Path:
        import hashlib

        owner_key = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:24]
        return self.root / "owners" / owner_key

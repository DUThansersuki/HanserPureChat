from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .alignment import amplitude_envelope, trailing_silence_samples
from .contracts import AudioArtifact, SegmentPackage, Timeline
from .performance import VisualResolution


class TimelineCompiler:
    revision = "timeline_2"

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def compile(
        self,
        *,
        job_id: str,
        reply_id: str,
        rendition_id: str,
        segment_id: str,
        index: int,
        audio: AudioArtifact,
        pcm,
        target_gap_ms: int,
        visual: VisualResolution,
        degraded_reasons: list[str] | None = None,
    ) -> SegmentPackage:
        target_gap_samples = round(audio.sample_rate * target_gap_ms / 1000)
        natural_tail = trailing_silence_samples(pcm)
        pause_after = max(0, target_gap_samples - natural_tail)
        artifact_id = secrets.token_urlsafe(24)
        timeline = Timeline(
            artifact_id=artifact_id,
            revision=self.revision,
            sample_rate=audio.sample_rate,
            pause_after_samples=pause_after,
            mouth_precision="amplitude",
            subtitle_precision="segment",
            mouth=amplitude_envelope(pcm, audio.sample_rate),
            expression=visual.model_dump(mode="json"),
        )
        self._publish(timeline)
        return SegmentPackage(
            job_id=job_id,
            reply_id=reply_id,
            rendition_id=rendition_id,
            segment_id=segment_id,
            index=index,
            audio=audio,
            segment_duration_samples=audio.sample_count + pause_after,
            timeline=timeline,
            qa={"basic": "passed", "content": "not_run"},
            degraded_reasons=degraded_reasons or [],
        )

    def _publish(self, timeline: Timeline) -> None:
        final = self.root / f"{timeline.artifact_id}.json"
        temporary = self.root / f".{timeline.artifact_id}.tmp.json"
        temporary.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, final)

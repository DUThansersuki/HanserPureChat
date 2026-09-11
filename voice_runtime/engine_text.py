from __future__ import annotations

from .contracts import EngineMapEntry, EngineTextPlan, VoicePlan


class EngineTextAdapter:
    """Compile the exact VoxCPM2 target text and its projection."""

    revision = "voxcpm2_text_1"

    def compile(self, speech_text: str, voice_plan: VoicePlan) -> EngineTextPlan:
        if voice_plan.style_prompt is not None and voice_plan.control_text is not None:
            raise ValueError("prompt audio/transcript and control text are mutually exclusive")
        if voice_plan.control_text is None:
            return EngineTextPlan(
                speech_text=speech_text,
                engine_text=speech_text,
                speech_to_engine=[
                    EngineMapEntry(
                        speech_span=(0, len(speech_text)),
                        engine_span=(0, len(speech_text)),
                    )
                ],
                spoken_engine_spans=[(0, len(speech_text))],
                adapter_revision=self.revision,
            )

        control_prefix = f"({voice_plan.control_text.strip()})"
        engine_text = control_prefix + speech_text
        return EngineTextPlan(
            speech_text=speech_text,
            engine_text=engine_text,
            speech_to_engine=[
                EngineMapEntry(
                    speech_span=(0, len(speech_text)),
                    engine_span=(len(control_prefix), len(engine_text)),
                )
            ],
            control_spans=[(0, len(control_prefix))],
            spoken_engine_spans=[(len(control_prefix), len(engine_text))],
            adapter_revision=self.revision,
        )

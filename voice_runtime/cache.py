from __future__ import annotations

import hashlib
import json

from .contracts import EngineTextPlan, VoicePlan
from .profiles import VoiceProfile


def generation_identity(
    *,
    profile: VoiceProfile,
    voice_plan: VoicePlan,
    engine_text: EngineTextPlan,
    variant_salt: str | None,
    postprocess_revision: str = "pcm48k_mono_1",
) -> tuple[str, int]:
    prompt = voice_plan.style_prompt
    payload = {
        "backend": profile.backend,
        "model_id": profile.model.id,
        "model_revision": profile.model.revision,
        "package_version": profile.model.package_version,
        "profile_revision": profile.profile_revision,
        "clone_mode": voice_plan.clone_mode,
        "identity_reference": {
            "id": voice_plan.identity_reference.asset_id,
            "revision": voice_plan.identity_reference.asset_revision,
        },
        "style_prompt": (
            {
                "id": prompt.asset_id,
                "revision": prompt.asset_revision,
                "transcript": prompt.transcript,
            }
            if prompt is not None
            else None
        ),
        "engine_text": engine_text.engine_text,
        "adapter_revision": engine_text.adapter_revision,
        "control_text": voice_plan.control_text,
        "cfg_value": profile.inference.cfg_value,
        "inference_timesteps": profile.inference.inference_timesteps,
        "normalize": False,
        "postprocess_revision": postprocess_revision,
        "variant_salt": variant_salt,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    return digest.hex(), int.from_bytes(digest[:4], "big") & 0x7FFFFFFF

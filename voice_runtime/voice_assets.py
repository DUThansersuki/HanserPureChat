from __future__ import annotations

from .contracts import SpeechSegment, VoiceAsset, VoicePlan
from .performance import VoiceCapability
from .profiles import VoiceProfile


class VoiceAssetSelector:
    revision = "voice_asset_selector_1"

    def select(
        self,
        segment: SpeechSegment,
        capability: VoiceCapability,
        profile: VoiceProfile,
        assets: dict[str, VoiceAsset],
        previous_prompt_id: str | None = None,
        forbidden_features: tuple[str, ...] = (),
    ) -> VoicePlan:
        identity = assets.get(profile.clone.identity_reference_id)
        if identity is None or identity.review_status != "approved":
            raise ValueError("approved identity reference is unavailable")
        if identity.speaker_id != profile.speaker_id:
            raise ValueError("identity reference speaker does not match profile")

        candidates = [
            asset
            for asset in assets.values()
            if asset.kind == "style_prompt"
            and asset.review_status == "approved"
            and asset.speaker_id == profile.speaker_id
            and asset.family == capability.family
            and asset.intensity_band == capability.intensity_band
            and not set(asset.feature_tags).intersection(forbidden_features)
            and self._language_matches(segment.language, asset.approved_languages)
        ]
        candidates.sort(
            key=lambda asset: (
                0 if asset.asset_id == previous_prompt_id else 1,
                *self._rank(asset, segment),
            )
        )

        prompt = candidates[0] if candidates else None
        reasons: list[str] = []
        if prompt is None and profile.clone.default_style_prompt_id:
            neutral = assets.get(profile.clone.default_style_prompt_id)
            if neutral is not None and neutral.review_status == "approved":
                prompt = neutral
                reasons.append("style_asset_fallback_neutral")
        if profile.clone.mode == "ref_continuation" and prompt is None:
            raise ValueError("ref_continuation profile requires an approved style prompt")
        if prompt is not None and not prompt.transcript:
            raise ValueError("style prompt requires an exact transcript")

        return VoicePlan(
            clone_mode=profile.clone.mode,
            identity_reference=identity,
            style_prompt=prompt,
            family=prompt.family if prompt else "neutral",
            intensity_band=prompt.intensity_band if prompt else "low",
            voice_intensity_mode=capability.voice_intensity_mode,
            degraded_reasons=[*capability.degraded_reasons, *reasons],
        )

    @staticmethod
    def _language_matches(language: str, approved: list[str]) -> bool:
        return not approved or language in approved or "mixed" in approved

    @staticmethod
    def _rank(asset: VoiceAsset, segment: SpeechSegment) -> tuple[int, int, str]:
        utterance_penalty = int(
            bool(asset.utterance_types)
            and segment.features.utterance_type not in asset.utterance_types
        )
        length_penalty = 0
        if asset.preferred_length_range is not None:
            low, high = asset.preferred_length_range
            length = segment.features.codepoint_length
            length_penalty = 0 if low <= length <= high else min(abs(length - low), abs(length - high))
        return utterance_penalty, length_penalty, asset.asset_id

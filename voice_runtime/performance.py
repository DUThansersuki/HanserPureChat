from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .contracts import AllowedPerformance, Delivery
from .profiles import RigProfile, VoiceProfile


_VOICE_FAMILY = {
    Delivery.NEUTRAL: "neutral",
    Delivery.GENTLE: "soft",
    Delivery.CONCERNED: "soft",
    Delivery.SERIOUS: "firm",
    Delivery.SOFT_SURPRISED: "light_reactive",
    Delivery.EXCITED: "light_reactive",
    Delivery.AMUSED: "light_playful",
    Delivery.TEASING: "light_playful",
    Delivery.DEADPAN: "dry",
    Delivery.ANNOYED_SOFT: "firm",
    Delivery.ANNOYED_PLAYFUL: "light_playful",
    Delivery.EMBARRASSED: "soft",
    Delivery.HESITANT: "soft",
}


class VoiceCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    intensity_band: str
    voice_intensity_mode: str
    degraded_reasons: list[str] = Field(default_factory=list)


class VisualResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str
    weight: float = Field(ge=0.0, le=1.0)
    motion: str = "none"
    feature_tags: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)


class VoiceCapabilityMapper:
    revision = "voice_mapper_1"

    def resolve(
        self, allowed: AllowedPerformance, profile: VoiceProfile
    ) -> VoiceCapability:
        if allowed.delivery not in profile.capabilities.approved_delivery:
            return VoiceCapability(
                family="neutral",
                intensity_band="low",
                voice_intensity_mode=profile.capabilities.voice_intensity_mode,
                degraded_reasons=[f"delivery_not_voice_approved:{allowed.delivery.value}"],
            )
        family = _VOICE_FAMILY[allowed.delivery]
        band = "medium" if allowed.intensity > 0.45 else "low"
        if band not in profile.capabilities.intensity_bands:
            band = profile.capabilities.intensity_bands[0]
        return VoiceCapability(
            family=family,
            intensity_band=band,
            voice_intensity_mode=profile.capabilities.voice_intensity_mode,
        )


class VisualCapabilityMapper:
    revision = "visual_mapper_1"

    def resolve(
        self, allowed: AllowedPerformance, rig: RigProfile
    ) -> VisualResolution:
        preset = rig.presets.get(allowed.delivery) or rig.presets.get(Delivery.NEUTRAL)
        if preset is None:
            return VisualResolution(
                preset="neutral",
                weight=0.0,
                degraded_reasons=["rig_preset_unavailable"],
            )
        forbidden = set(allowed.forbidden_features)
        if forbidden.intersection(preset.feature_tags):
            neutral = rig.presets.get(Delivery.NEUTRAL)
            return VisualResolution(
                preset=neutral.preset if neutral else "neutral",
                weight=0.0,
                motion="none",
                degraded_reasons=["visual_features_forbidden"],
            )
        return VisualResolution(
            preset=preset.preset,
            weight=min(allowed.intensity, preset.max_weight),
            motion=preset.motion,
            feature_tags=preset.feature_tags,
        )


class CrossModalCompatibility:
    revision = "cross_modal_compatibility_1"

    def filter(
        self,
        voice: VoiceCapability,
        visual: VisualResolution,
    ) -> VisualResolution:
        if voice.family != "neutral" or visual.weight <= 0.45:
            return visual
        if not set(visual.feature_tags).intersection({"strong_anger", "ecstatic", "large_motion"}):
            return visual
        filtered = visual.model_copy(deep=True)
        filtered.weight = min(filtered.weight, 0.25)
        filtered.motion = "none"
        filtered.degraded_reasons.append("neutral_voice_strong_visual_filtered")
        return filtered

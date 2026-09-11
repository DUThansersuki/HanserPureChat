from __future__ import annotations

import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Delivery, VoiceAsset


class ModelRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    revision: str | None = None
    package_version: str | None = None
    local_path: str | None = None


class CloneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["reference", "ref_continuation"]
    identity_reference_id: str
    default_style_prompt_id: str | None = None
    recursive_generated_prompt: Literal[False] = False


class VoiceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_delivery: list[Delivery] = Field(default_factory=lambda: [Delivery.NEUTRAL])
    voice_intensity_mode: Literal["discrete", "not_controllable"] = "discrete"
    intensity_bands: list[str] = Field(default_factory=lambda: ["low"])
    text_control: bool = False
    style_switches_per_reply: int = Field(default=0, ge=0)


class RuntimeMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_mode: Literal["public"] = "public"
    explicit_prompt_cache: Literal[False] = False


class GPTSoVITSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str = "http://127.0.0.1:9880"
    request_timeout_seconds: float = Field(default=90.0, gt=0.0, le=900.0)
    text_language: Literal["zh", "all_zh"] = "zh"
    prompt_language: Literal["zh", "all_zh"] = "zh"
    text_split_method: Literal["cut5"] = "cut5"
    top_k: int = Field(default=5, ge=1)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    temperature: float = Field(default=1.0, gt=0.0)
    batch_size: Literal[1] = 1
    split_bucket: Literal[True] = True
    speed_factor: Literal[1.0] = 1.0
    parallel_infer: Literal[False] = False
    repetition_penalty: float = Field(default=1.35, gt=0.0)

    @model_validator(mode="after")
    def endpoint_is_loopback_http(self) -> "GPTSoVITSConfig":
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("GPT-SoVITS endpoint must be a loopback HTTP origin")
        self.endpoint = self.endpoint.rstrip("/")
        return self

    def generation_parameters(self) -> dict[str, object]:
        return self.model_dump(exclude={"endpoint", "request_timeout_seconds"})


class InferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cfg_value: float = 2.0
    inference_timesteps: int = Field(default=10, gt=0)
    normalize: Literal[False] = False
    retry_badcase: Literal[False] = False
    load_denoiser: bool = False


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_sample_rate: Literal[48000] = 48000
    channels: Literal[1] = 1


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    profile_id: str
    profile_revision: str
    status: Literal["candidate", "validated", "released"] = "candidate"
    enabled: bool = False
    backend: Literal["gpt_sovits_v2pro"] = "gpt_sovits_v2pro"
    speaker_id: str = "hanser"
    model: ModelRevision
    clone: CloneConfig
    capabilities: VoiceCapabilities = Field(default_factory=VoiceCapabilities)
    runtime: RuntimeMode = Field(default_factory=RuntimeMode)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    gpt_sovits: GPTSoVITSConfig | None = None
    output: OutputConfig = Field(default_factory=OutputConfig)
    assets_manifest: str = "../../assets/voices/hanser/manifest.jsonl"

    @model_validator(mode="after")
    def enabled_profile_is_frozen(self) -> "VoiceProfile":
        if self.enabled and self.status not in {"validated", "released"}:
            raise ValueError("candidate voice profile cannot be enabled")
        if self.enabled and (not self.model.revision or not self.model.package_version):
            raise ValueError("enabled voice profile requires frozen model and package revisions")
        if self.gpt_sovits is None:
            raise ValueError("GPT-SoVITS profile requires gpt_sovits settings")
        return self


class RigPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str
    max_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    motion: str = "none"
    feature_tags: list[str] = Field(default_factory=list)


class RigProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    profile_id: str
    revision: str
    status: Literal["candidate", "validated", "released"] = "candidate"
    enabled: bool = False
    model_path: str | None = None
    presets: dict[Delivery, RigPreset] = Field(default_factory=dict)
    attack_ms: int = Field(default=160, ge=0)
    hold_ms: int = Field(default=900, ge=0)
    release_ms: int = Field(default=240, ge=0)


def load_voice_profile(path: str | Path) -> VoiceProfile:
    profile_path = Path(path).resolve()
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    profile = VoiceProfile.model_validate(raw)
    if profile.model.local_path:
        model_path = Path(profile.model.local_path)
        if not model_path.is_absolute():
            model_path = profile_path.parents[1] / model_path
        profile.model.local_path = str(model_path.resolve())
    if profile.enabled:
        load_voice_assets(profile_path, profile)
    return profile


def load_voice_assets(profile_path: Path, profile: VoiceProfile) -> dict[str, VoiceAsset]:
    manifest_path = (profile_path.parent / profile.assets_manifest).resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"voice assets manifest not found: {manifest_path}")
    assets: dict[str, VoiceAsset] = {}
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        asset = VoiceAsset.model_validate(json.loads(line))
        if asset.asset_id in assets:
            raise ValueError(f"duplicate voice asset id at line {line_number}: {asset.asset_id}")
        asset_path = (manifest_path.parent / asset.path).resolve()
        if asset.review_status == "approved" and not asset_path.is_file():
            raise FileNotFoundError(f"approved voice asset missing: {asset_path}")
        asset.path = str(asset_path)
        assets[asset.asset_id] = asset
    return assets


def load_rig_profile(path: str | Path) -> RigProfile:
    profile_path = Path(path).resolve()
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    profile = RigProfile.model_validate(raw)
    if profile.enabled:
        if profile.status not in {"validated", "released"}:
            raise ValueError("candidate rig profile cannot be enabled")
        if not profile.model_path:
            raise ValueError("enabled rig profile requires model_path")
        model_path = (profile_path.parent / profile.model_path).resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"Live2D model not found: {model_path}")
        profile.model_path = str(model_path)
    return profile

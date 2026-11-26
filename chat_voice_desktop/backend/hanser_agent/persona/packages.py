from __future__ import annotations

import json
from pathlib import Path
import yaml


def resolve_persona_package(root: Path, selector: str) -> Path:
    if selector == "persona-v1-production":
        return root
    if selector == "hanser-persona-v2-candidate":
        return root / "candidates" / "hanser-persona-v2-candidate"
    if selector != "persona-v2-production":
        raise ValueError(f"unknown Persona package: {selector}")
    pointer = json.loads((root / "releases" / "production.json").read_text(encoding="utf-8"))
    package = (root / "releases" / pointer["directory"]).resolve()
    if package.parent != (root / "releases").resolve():
        raise ValueError("Persona release directory must be a direct child of releases")
    manifest = yaml.safe_load((package / "manifest.yaml").read_text(encoding="utf-8"))
    if (manifest["package_id"], manifest["style_generation"]) != (pointer["package_id"], pointer["style_generation"]):
        raise ValueError("Persona release pointer disagrees with package/Style generation")
    return package


def loaded_persona_status(agent) -> dict:
    builder = getattr(agent, "context_builder", None)
    compiler = getattr(builder, "persona_compiler", None)
    if compiler is None:
        return {}
    manifest = compiler.manifest
    return {
        "loaded_persona_package_id": compiler.package_id,
        "loaded_persona_path": str(compiler.prompt_dir.resolve()),
        "loaded_persona_source_sha256": compiler.source_sha256,
        "loaded_style_generation": manifest.style_generation if manifest else None,
    }

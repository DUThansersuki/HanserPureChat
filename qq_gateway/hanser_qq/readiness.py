"""Inspect the actual QQ production combination without constructing a model."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import json

from .config import Settings
from .corpus import corpus_status


def check_prepared(config: Settings) -> dict:
    from .headless import install_backend
    install_backend(config.backend.package_path)
    from hanser_agent.config import load_settings
    from hanser_agent.persona.compiler import PersonaCompiler
    from hanser_agent.persona.packages import resolve_persona_package

    settings = load_settings(config.backend.config_path)
    from .model_routes import apply_responder_gateway
    apply_responder_gateway(settings, config)
    if not settings.db_path.resolve().is_relative_to(config.storage_path.parent.resolve()):
        raise ValueError("QQ database must be inside the independent runtime data directory")
    root = config.backend.package_path / "hanser_agent/prompts/persona"
    package = resolve_persona_package(root, settings.persona.active_package)
    compiler = PersonaCompiler(package, settings_lifecycle=(
        "production" if settings.persona.active_package == "persona-v2-production" else "preview"
    ))
    loaded = {"loaded_style_generation": compiler.manifest.style_generation if compiler.manifest else None,
              "loaded_persona_package_id": compiler.package_id, "loaded_persona_path": str(package),
              "loaded_persona_source_sha256": compiler.source_sha256}
    selector = config.storage_path.parent / "active_release.json"
    active = json.loads(selector.read_text(encoding="utf-8")) if selector.exists() else None
    if active:
        from .releases import validate_engine
        validate_engine(active)
        frozen_corpus = corpus_status(config.storage_path.parent / "releases" / active["release_id"] / "corpus.sqlite3")
        if not frozen_corpus["indexed"] or active["style_generation"] != loaded["loaded_style_generation"]:
            raise ValueError("active QQ corpus release is incomplete or incompatible")
    return {"mode": "qq-offline-check", "production": production_status(settings, loaded), **loaded,
            "runtime_corpus": {"enabled_for_next_start": active is not None, "release": active},
            "corpus": corpus_status(config.storage_path.parent / "full_corpus.sqlite3"),
            "model_configuration_valid": True, "model_network_verified": False, "qq_delivery_verified": False}


def production_status(settings, loaded: dict) -> dict:
    from hanser_agent.persona.data_pipeline import style_row_to_model
    from hanser_agent.persona.style_eligibility import style_exclusion_reason

    # Read-only: status must also work while the chat process is stopped.
    import sqlite3
    with closing(sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        active = conn.execute("SELECT generation,revision FROM active_index_generations WHERE collection='style_examples'").fetchone()
        generation, revision = (active["generation"], active["revision"]) if active else (None, None)
        approved = conn.execute("SELECT * FROM style_examples WHERE review_status='approved' AND index_generation=?",
                                (generation,)).fetchall()
        exclusions = Counter()
        for row in approved:
            reason = style_exclusion_reason(style_row_to_model(row), active_generation=generation,
                                            min_quality=settings.style.min_quality)
            if reason:
                exclusions[reason] += 1
        document_count = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    expected = loaded.get("loaded_style_generation")
    matched = expected is not None and generation == expected
    frozen = settings.persona.active_package == "persona-v2-production"
    return {
        "chat_ready": matched and frozen,
        "persona_selector": settings.persona.active_package,
        "frozen_production": frozen, "generation_matches_persona": matched,
        "style_generation": generation, "style_revision": revision,
        "documents": document_count, "style_outer_approved": len(approved),
        "style_static_eligible": len(approved) - sum(exclusions.values()),
        "style_static_exclusions": dict(exclusions),
        "per_turn_applicability_required": True,
    }

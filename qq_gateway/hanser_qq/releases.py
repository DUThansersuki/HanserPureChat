"""QQ-local immutable release, Style-only migration and matched rollback."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .admission import REVIEW_VERSION, admit
from .corpus import corpus_status
from .locking import service_lock


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def runtime_engine_hashes() -> dict:
    # Freeze/activation boundary only, never in a message or health loop.
    directory = Path(__file__).resolve().parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("headless.py", "runtime_style.py", "text_core.py", "admission.py",
                         "segmentation.py", "engine.py", "store.py", "config.py", "client.py", "plugin.py",
                         "speech_habits.py", "speech_habits.v1.json", "proactive.py", "proactive_backend.py", "voice.py", "voice_runtime.py", "model_routes.py", "__main__.py",
                         "retrieval_audit.py", "voice_entry.py", "runtime_update.py", "releases.py",
                         "corpus.py", "rag_retrieval.py", "rag_selector.py", "expression_trial.py")}


def validate_engine(selected: dict):
    if selected.get("engine_sha256") and selected["engine_sha256"] != runtime_engine_hashes():
        raise ValueError("QQ release engine changed after evaluation; revalidate before activation")


def backup_database(source: Path, destination: Path):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def active_release(root: Path) -> dict | None:
    path = root / ".runtime/active_release.json"
    return read_json(path) if path.exists() else None


def release_path(root: Path, release_id: str) -> Path:
    path = (root / "releases" / release_id).resolve()
    if path.parent != (root / "releases").resolve():
        raise ValueError("release must be a direct child of qq_gateway/releases")
    return path


def style_rows(database: Path, generation: str) -> list[dict]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM style_examples WHERE index_generation=?", (generation,))]


def migrate_style(database: Path, rows: list[dict], manifest: dict):
    generation, now = manifest["style_generation"], datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(database)) as conn, conn:
        present = conn.execute("SELECT COUNT(*) FROM style_examples WHERE index_generation=?", (generation,)).fetchone()[0]
        if present and present != len(rows):
            raise ValueError("partially installed Style generation")
        if not present:
            columns = [name for name in rows[0] if name != "id"]
            conn.executemany(f"INSERT INTO style_examples ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                             (tuple(row[name] for name in columns) for row in rows))
        conn.execute("""INSERT INTO index_generations(collection,generation,status,model,dimensions,
                       source_revision,config_hash,item_count,created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(collection,generation) DO NOTHING""",
                     ("style_examples", generation, "published", "local-bm25", 0, manifest["corpus_generation"],
                      manifest["payload_sha256"], len(rows), now, now))
        conn.execute("""INSERT INTO active_index_generations(collection,generation,revision,updated_at)
                       VALUES('style_examples',?,1,?) ON CONFLICT(collection) DO UPDATE SET
                       generation=excluded.generation,revision=excluded.revision,updated_at=excluded.updated_at""",
                     (generation, now))


def build_release(root: Path, release_id: str) -> dict:
    from .headless import install_backend
    backend = root / ".runtime/backend"
    install_backend(backend)
    from hanser_agent.persona.data_pipeline import style_row_to_model
    from hanser_agent.persona.style_eligibility import style_exclusion_reason

    target = release_path(root, release_id)
    if target.exists():
        raise ValueError("release directory already exists; immutable releases cannot be overwritten")
    corpus = root / ".runtime/full_corpus.sqlite3"
    status = corpus_status(corpus)
    if not status["indexed"]:
        raise ValueError("full corpus must be built first")
    pointer = read_json(backend / "hanser_agent/prompts/persona/releases/production.json")
    generation = release_id + "-style"
    with closing(sqlite3.connect(corpus.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        units = [json.loads(row[0]) for row in conn.execute("SELECT record_json FROM units WHERE bucket='classified' ORDER BY unit_id")]
    admitted, decisions, counts = [], [], Counter()
    for unit in units:
        payload, reason = admit(unit)
        counts[reason] += 1
        decisions.append({"unit_id": unit["unit_id"], "reason": reason, "payload": payload})
        if payload:
            admitted.append(payload)
    if not admitted:
        raise ValueError("no independently admitted runtime payloads")
    rows = style_rows(backend / "documents.seed.db", pointer["style_generation"])
    for row in rows:
        row["index_generation"] = generation
    template = rows[0]
    for item in admitted:
        meta = {
            "style_record_version": 3, "unit_id": item["unit_id"] + f":fragment:{item['start']}-{item['end']}",
            "source_episode_id": item["unit_id"], "trigger_basis": "unknown", "trigger_evidence_refs": [],
            "schema_review_status": "approved", "runtime_payload_reviewed": True,
            "provenance_kind": "verbatim", "payload_class": "reaction_only",
            "behavior_tags": [item["behavior"]], "expression_tags": item["expressions"],
            "runtime_scope": ["style_runtime"], "speaker_status": "transcript_verified",
            "audience": "individual", "group_id": item["source_id"], "hidden_eval": False,
            "transfer_scope": "requires_context" if item["requires"] else "general",
            "applicability": {
                "requires_all": [{"field": field, "one_of": [value]} for field, value in item["requires"]],
                "forbids_any": [{"field": name, "one_of": [True]} for name in
                                ("explicit_stop", "quoted_or_hypothetical")]
                                + ([{"field": "distress", "one_of": [True]},
                                    {"field": "disable_humor", "one_of": [True]},
                                    {"field": "tension", "one_of": [True]}]
                                   if item["rule_id"] in {"playful_reaction", "low_profanity"} else []),
            },
            "runtime_payload_kind": "reviewed_fragment", "runtime_reaction_fragment": item["fragment"],
            "projection_schema_version": "style-qa-v1", "projection_version": REVIEW_VERSION,
            "source_revision": item["source_revision"], "attribution_status": "verified",
            "attribution_basis": item["attribution_basis"], "speaker_candidate": "hanser",
            "attribution_evidence_refs": ["hanser-source-prior-accepted-by-user-20261013"
                                          if item["attribution_basis"] == "source_prior" else "explicit_hanser_source_marker"],
            "q_projection": {"review_status": "approved", "scene_description": item["scene"],
                             "evidence_refs": [REVIEW_VERSION, item["unit_id"]]},
            "a_projection": {"review_status": "approved", "behavior_ids": [item["behavior"]],
                             "evidence_refs": [REVIEW_VERSION, item["unit_id"]]},
            "qq_literal_review": item,
        }
        row = {name: None for name in template}
        row.update({"prompt": "", "response": item["fragment"], "context_before": "", "context_after": "",
                    "scene": item["rule_id"], "speech_act": item["behavior"], "tone_json": "[]",
                    "answer_length": "short", "response_mode": "playful" if item["expressions"] else "casual",
                    "source_type": "real", "source_ref": f"{item['unit_id']}#chars={item['start']}-{item['end']}",
                    "authenticity_score": 0.9, "quality_score": 0.85, "metadata_json": json.dumps(meta, ensure_ascii=False),
                    "review_status": "approved", "source_tier": "primary", "source_document_id": item["document_id"],
                    "source_start_char": item["start"], "source_end_char": item["end"], "source_speaker": "hanser",
                    "source_response_turn": item["fragment"], "source_raw_text": item["fragment"],
                    "cleaning_operations_json": "[]", "reviewer_id": REVIEW_VERSION,
                    "review_notes": "Bounded literal fragment only; A13 classification is not approval; original A retained in corpus",
                    "reviewed_at": datetime.now(timezone.utc).isoformat(), "index_generation": generation})
        rows.append(row)
    payload_bytes = (json.dumps(rows, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    manifest = {"schema_version": 1, "release_id": release_id, "style_generation": generation,
                "parent_persona": pointer, "parent_style_generation": pointer["style_generation"],
                "corpus_generation": status["generation"], "review_version": REVIEW_VERSION,
                "source_documents": status["source_documents"], "classified": status["classified"],
                "admitted_fragments": len(admitted), "admitted_documents": len({p["source_id"] for p in admitted}),
                "admission_counts": dict(counts), "rules": dict(Counter(p["rule_id"] for p in admitted)),
                "style_rows": len(rows), "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(), "evaluation_status": "pending"}
    manifest["runtime_engine"] = "qq-text-v3"
    target.mkdir(parents=True)
    (target / "style_rows.json").write_bytes(payload_bytes)
    with (target / "admission_decisions.jsonl").open("w", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
    write_json(target / "release.json", manifest)
    persona = target / "persona"
    shutil.copytree(backend / "hanser_agent/prompts/persona/releases" / pointer["directory"], persona)
    persona_manifest = yaml.safe_load((persona / "manifest.yaml").read_text(encoding="utf-8"))
    persona_manifest.update(package_id=release_id, style_generation=generation, parent_package_id=pointer["package_id"],
                            examples_version=status["generation"], build_status="candidate")
    (persona / "manifest.yaml").write_text(yaml.safe_dump(persona_manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    runtime_release = root / ".runtime/releases" / release_id
    runtime_release.mkdir(parents=True)
    backup_database(corpus, runtime_release / "corpus.sqlite3")
    backup_database(backend / "documents.seed.db", runtime_release / "candidate.db")
    migrate_style(runtime_release / "candidate.db", rows, manifest)
    with closing(sqlite3.connect(runtime_release / "candidate.db")) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM style_examples WHERE index_generation=?", (generation,)):
            example = style_row_to_model(row)
            if example.reviewer_id == REVIEW_VERSION:
                reason = style_exclusion_reason(example, active_generation=generation, min_quality=0.68)
                if reason:
                    raise ValueError(f"admitted fragment failed shared runtime gate: {example.id}: {reason}")
    return manifest


def install_pointer(backend: Path, root: Path, manifest: dict):
    target = backend / "hanser_agent/prompts/persona/releases"
    package = target / manifest["release_id"]
    if not package.exists():
        shutil.copytree(release_path(root, manifest["release_id"]) / "persona", package)
    write_json(target / "production.next.json", {"schema_version": 1, "directory": manifest["release_id"],
               "package_id": manifest["release_id"], "style_generation": manifest["style_generation"]})
    (target / "production.next.json").replace(target / "production.json")


def publish_release(root: Path, release_id: str, evaluation: Path, *, user_trial: bool = False,
                    expected_engine: dict | None = None, expected_active: dict | None = None) -> dict:
    target = release_path(root, release_id)
    manifest, evidence = read_json(target / "release.json"), read_json(evaluation)
    if evidence.get("release_id") != release_id or (not user_trial and evidence.get("decision") != "PASS"):
        raise ValueError("release requires matching completed passing evaluation")
    if not user_trial and (evidence.get("hard_failures") or evidence.get("missing_outputs")):
        raise ValueError("release has unresolved hard-gate failures or missing outputs")
    engine = runtime_engine_hashes()
    if expected_engine is not None and engine != expected_engine:
        raise ValueError("staged QQ runtime code changed; prepare a new checked update")
    if not user_trial and manifest.get("runtime_engine") == "qq-text-v3" and evidence.get("engine_sha256") != engine:
        raise ValueError("release evaluation does not match the current QQ runtime engine")
    if evidence.get("payload_sha256") != manifest["payload_sha256"]:
        raise ValueError("release evaluation does not match the frozen Style payload")
    payload = (target / "style_rows.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest["payload_sha256"]:
        raise ValueError("frozen release payload changed")
    runtime = root / ".runtime"
    with service_lock(runtime / "run.lock"), service_lock(runtime / "backend.lock"), service_lock(runtime / "gateway.lock"):
        previous = active_release(root)
        if expected_active is not None and previous != expected_active:
            raise ValueError("active QQ release changed after the runtime update was staged")
        mode = "user_trial" if user_trial else "evaluated"
        if previous and previous["release_id"] == release_id and previous.get("mode", "evaluated") == mode:
            if user_trial and previous.get("engine_sha256") != engine:
                # Refresh explicitly authorized trial code without replacing
                # its original rollback target or relabeling old A/B evidence.
                previous.update(engine_sha256=engine, runtime_updated_at=datetime.now(timezone.utc).isoformat())
                write_json(runtime / "active_release.next.json", previous)
                (runtime / "active_release.next.json").replace(runtime / "active_release.json")
                return {"published": release_id, "already_active": True, "runtime_refreshed": True,
                        "mode": mode, "evaluation_decision": evidence.get("decision"), "user_data_preserved": True}
            return {"published": release_id, "already_active": True, "user_data_preserved": True}
        rollback = {"active_release": previous, "persona": read_json(runtime / "backend/hanser_agent/prompts/persona/releases/production.json")}
        write_json(runtime / f"rollback-{release_id}.json", rollback)
        migrate_style(runtime / "hanser.db", json.loads(payload), manifest)
        migrate_style(runtime / "backend/documents.seed.db", json.loads(payload), manifest)
        install_pointer(runtime / "backend", root, manifest)
        write_json(runtime / "active_release.next.json", {"release_id": release_id,
                   "style_generation": manifest["style_generation"], "evaluation": str(evaluation.resolve()),
                   "engine_sha256": engine, "mode": mode,
                   "evaluation_decision": evidence.get("decision"), "formal_approval": not user_trial,
                   "trial_authorization": "user-2026-10-18-full-corpus-regardless-evaluation" if user_trial else None,
                   "full_corpus_originals": user_trial})
        (runtime / "active_release.next.json").replace(runtime / "active_release.json")
    return {"published": release_id, "style_generation": manifest["style_generation"], "mode": mode,
            "evaluation_decision": evidence.get("decision"), "user_data_preserved": True}


def rollback_release(root: Path) -> dict:
    active = active_release(root)
    if active is None:
        raise ValueError("no QQ full-corpus release is active")
    runtime = root / ".runtime"
    rollback = read_json(runtime / f"rollback-{active['release_id']}.json")
    pointer = rollback["persona"]
    with service_lock(runtime / "run.lock"), service_lock(runtime / "backend.lock"), service_lock(runtime / "gateway.lock"):
        with closing(sqlite3.connect(runtime / "hanser.db")) as conn, conn:
            if not conn.execute("SELECT 1 FROM style_examples WHERE index_generation=? LIMIT 1", (pointer["style_generation"],)).fetchone():
                raise ValueError("rollback Style generation is unavailable")
            conn.execute("UPDATE active_index_generations SET generation=?,revision=revision+1 WHERE collection='style_examples'",
                         (pointer["style_generation"],))
        with closing(sqlite3.connect(runtime / "backend/documents.seed.db")) as conn, conn:
            conn.execute("UPDATE active_index_generations SET generation=?,revision=revision+1 WHERE collection='style_examples'",
                         (pointer["style_generation"],))
        write_json(runtime / "backend/hanser_agent/prompts/persona/releases/production.json", pointer)
        if rollback["active_release"]:
            write_json(runtime / "active_release.json", rollback["active_release"])
        else:
            (runtime / "active_release.json").unlink()
    return {"rolled_back": active["release_id"], "style_generation": pointer["style_generation"], "user_data_preserved": True}

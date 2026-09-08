# Slice 4 Implementation Report — 2026-09-07

## Verdict

**PASS** for `NEXT_STAGE_ENGINEERING_GUIDE.md` Slice 4, subject to the unchanged scope and deferred work stated below.

The machine-readable acceptance report passed 10/10 gates:

- `audit_artifacts/slice4_runtime_2026-09-07_003/slice4_acceptance_report.json`

## Implemented boundaries

- Empty responder content is retried once through the same Responder profile. A second empty result raises the structured `empty_provider_output` failure; it is never replaced by the legacy hard-coded “憨憨不知道哦” answer.
- Model calls use per-profile timeouts and stable failure codes for timeout, transport, HTTP, and malformed-response failures.
- Planner primary-provider fallback, deterministic fallback, and unload failure are surfaced through `status=degraded` and `degraded_reasons`; these results can no longer be counted as normal quality samples.
- Dense retrieval failure uses the verified BM25 candidate path and reports `dense_unavailable_bm25_fallback`.
- Reranker failure returns the already recalled evidence through deterministic BM25 ordering, preserves sources, and reports `reranker_unavailable_retrieval_fallback`.
- A reply is persisted before post-turn processing. Post-turn failure returns the saved reply with `post_turn_status=pending_retry`, persists a retry record, and exposes maintenance list/retry endpoints.
- Request IDs provide idempotent replay. Repeating the same request returns the cached response without duplicate messages; reusing the ID with a different payload is rejected.
- A memory whose embedding failed remains eligible for indexing on a later post-turn retry.

No second Responder, new framework, queue system, Persona Prompt change, model switch, LoRA, or DPO was introduced.

## Migration

- Dry-run preview: no blockers, 0 planned deletes, 16 existing messages retained.
- Production migration version: 5.
- Added `request_executions` and `post_turn_failures`.
- Pre-migration backup: `source_data/documents.pre_slice4.20260907T084200Z.db`.
- Postcheck at migration time: SQLite integrity `ok`, migration 5 present, message count still 16.
- Failure records are retained; rollback of the resource policy remains a configuration change.

## Verification

### Unit and fault coverage

Final full suite: **68 passed, 4 subtests passed**.

Slice 4-specific tests cover:

- provider 200 with empty content, same-Responder retry, and repeated-empty structured failure;
- provider timeout and structured API error;
- reranker exception with preserved evidence sources;
- dense embedding/retrieval exception with BM25 fallback;
- post-turn failure after reply persistence and successful maintenance retry;
- request replay idempotency and request-ID conflict;
- embedding failure followed by indexing retry.

Persisted fault probes: **7/7 passed** in the final runtime directory.

### Two ordered 20-turn scenarios

Final healthy run: `audit_artifacts/slice4_runtime_2026-09-07_003`.

| Scenario | HTTP 200 | Non-empty | Degraded | p50 | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| continuity | 20/20 | 20/20 | 0 | 4.63s | 17.98s | 18.45s |
| mixed | 20/20 | 20/20 | 0 | 5.90s | 19.96s | 20.64s |
| total | 40/40 | 40/40 | 0 | 5.60s | 18.45s | 20.64s |

The fixed ordering matches `phase6_runtime.py:SCENARIOS`. False shared events were not persisted. Corrections remained active and the old positive coffee preference did not return.

### Cross-session

After closing and rebuilding the gateway, stores, agent, and ASGI application, the new conversation returned:

> 小林嘛 你爱喝茶不爱咖啡 我记得呢

The persisted memory set contained the personal name `小林`, positive tea preference, negative coffee preference, and fan identity `毛怪` as separate compatible address/memory facts.

### Failure observability diagnostic

`audit_artifacts/slice4_runtime_2026-09-07_002` intentionally remains as evidence from a run where Ollama was unavailable. All 40 requests still returned usable responses through fallback planning, but all 40 were explicitly marked degraded:

- `planner_primary_provider_fallback`: 6
- `planner_deterministic_fallback`: 34
- `planner_unload_failed`: 8 (overlapping)

This run is excluded from normal persona-quality acceptance and demonstrates that failure results are no longer silently counted as healthy.

## Resource experiment and selected default

Experiments used the 6 GB RTX 3060 exclusively, alternated 10 Wiki and 10 casual turns, separated cold/warm behavior, sampled VRAM continuously, and retained stage timings.

Selected profile: **retain reranker** (`release_after_request=False`, the effective default).

Latest healthy retain run:

- 20/20 HTTP success;
- 1/20 explicitly degraded due a temporary primary Planner fallback; no retrieval fallback;
- total p50 27.64s, p95 41.20s, max 48.85s;
- Wiki p50 33.53s; casual p50 21.84s;
- GPU range 1581–5977 MiB.

The release-reranker comparison reduced median latency and idle VRAM but produced 7/20 `reranker_unavailable_retrieval_fallback` responses. It was therefore not selected as the default. The switch remains available for controlled experiments and rollback.

The final 40-turn healthy regression was materially faster than the alternating cold/warm resource experiment and reduced the earlier 102.84s observed maximum to 20.64s. This is evidence for the target machine/run, not a universal latency guarantee.

## Regression-contract decision

- Non-relaxable grounding and memory gates: passed.
- Empty-provider hard gate: passed by deterministic injection tests.
- Two 20-turn scenarios and cross-session replay: passed.
- Persona Prompt hashes match the archived Phase 6 baseline.
- Independent blind persona scoring was not rerun because Persona Prompt, responder model, and style data did not change. Naturally generated contextual uses of “不知道” remain valid; only the technical empty-content substitution is forbidden.
- The deferred 30–50-turn stress set remains outside Slice 4, as stated by `ARCHITECTURE_SPEC_DELTA.md`.

## Files added or materially changed

- `backend/hanser_agent/failures.py`
- `backend/hanser_agent/models.py`
- `backend/hanser_agent/config.py`
- `backend/hanser_agent/model_gateway.py`
- `backend/hanser_agent/responder/service.py`
- `backend/hanser_agent/retrieval/hybrid.py`
- `backend/hanser_agent/retrieval/reranker.py`
- `backend/hanser_agent/agent/planner.py`
- `backend/hanser_agent/agent/service.py`
- `backend/hanser_agent/agent/request_state.py`
- `backend/hanser_agent/agent/tools/wiki_search.py`
- `backend/hanser_agent/memory/pipeline.py`
- `backend/hanser_agent/memory/store.py`
- `backend/hanser_agent/db.py`
- `backend/hanser_agent/api.py`
- `backend/tests/test_slice4_failures.py`
- `backend/scripts/migrate_slice4_failure_state.py`
- `backend/scripts/slice4_resource_experiment.py`
- `backend/scripts/summarize_slice4_resources.py`
- `backend/scripts/slice4_verify.py`


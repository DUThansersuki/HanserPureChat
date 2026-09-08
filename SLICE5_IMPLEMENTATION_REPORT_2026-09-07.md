# Slice 5 — Ownership and index lifecycle implementation report

## Verdict

**PASS for the deterministic Slice 5 engineering contract.** No local embedding,
reranker, responder, or other GPU-backed model was loaded. Slice 4 and the Persona
Prompt were not changed.

## Implemented

- Conversation history is owner-checked before any message, summary, relationship,
  or scene data is read by the chat path.
- An existing conversation owner cannot be changed by `append_turn`; creation and
  append are serialized with `BEGIN IMMEDIATE`.
- Owner conflicts raise a structured error and `/v1/chat` maps it to HTTP 409.
- Summary reads (`message_count` and `messages_for_summary`) require the same owner.
- Vector indexes now use immutable staging generations and an atomic active pointer.
- Generation metadata records source revision, embedding model, dimensions, config
  hash, item count, status, and publication time.
- Publication validates the physical row count and every row's model/dimensions.
- Failed or interrupted builds never replace the previous active generation.
- In-process matrices are keyed by the database generation revision; publication or
  incremental mutation from another process invalidates stale cache on next read.
- Fact and Style rebuild paths stage every batch before publication.
- Document chunk deletion removes associated `chunk_tokens` in the same transaction.
- Memory vector deletion updates the active generation count and revision.
- Legacy vectors remain in the old table as the rollback generation source.

## Migration evidence

- Preview: `audit_artifacts/slice5_2026-09-07_001/migration_preview.json`
- Candidate migration: `audit_artifacts/slice5_2026-09-07_001/candidate_migration.json`
- Production migration: `audit_artifacts/slice5_2026-09-07_001/production_migration.json`
- Production post-check: `audit_artifacts/slice5_2026-09-07_001/production_postcheck.json`
- Pre-migration production SHA-256:
  `7835142A9687C0CFD5F035DF8A6D6DD8AF8BFFE2B9CC2FD7D57EF222980BE6D9`
- Backup: `source_data/documents.pre_slice5.20260906T192552Z.db`
- Backup SHA-256 exactly matches the pre-migration hash.
- SQLite integrity: `ok`.
- Missing conversation owners: 0.
- Orphan chunk tokens: 0.
- Legacy vectors: 5084; generation vectors: 5084.
- Active generations: fact 4197, memory 1, style 886.
- A read-only production smoke query successfully read the active migrated memory
  generation.

## Test evidence

`pytest -q backend/tests`: **60 passed, 1 dependency deprecation warning, 4 subtests passed**.

Slice 5-specific probes cover:

- Alice owns a conversation; Bob cannot read it or append to it.
- Alice can continue the same conversation normally.
- New, modified, and deleted index items follow only the published generation.
- A failed partial build leaves the old complete generation active.
- A model/dimension change requires and succeeds through a new generation.
- A separate Python process publishes an index while the parent retains an old
  matrix cache; the parent observes the new result through revision invalidation.
- Deleting document chunks removes all corresponding BM25 token rows.

## Scope notes

- No destructive fault injection used the production database; all failure and
  mutation probes used temporary databases or the isolated candidate copy.
- No new framework, service boundary, model switch, LoRA, DPO, or large-scale
  architecture change was introduced.
- The only warning is the existing `jieba`/`pkg_resources` deprecation warning; it
  is unrelated to Slice 5 correctness.

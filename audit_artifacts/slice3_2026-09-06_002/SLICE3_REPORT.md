# Slice 3 implementation report

## Verdict

- Slice 3 deterministic engineering contract: **PASS** (11/11 final checks).
- `PERSONA_REGRESSION_SUITE.md` release acceptance: **NOT_ACCEPTED / HOLD**.
- Reason: the fixed `false_memory_trap` response rejects the shared-event premise but then invents an unsupported first-person Shanghai history. Independent layered human blind review and repeated paired sampling were not executed, so persona quality cannot be declared accepted.

## Implemented scope

- Added separate input budget, output reserve, provider context window, conservative estimator, and message overhead configuration.
- Rebuilt context assembly around whole-item selection with source IDs, per-block token accounting, drop reasons, an explicit `ContextBudgetExceeded`, stable payload hash, and `context_v1` trace identity.
- Preserved core persona, boundaries, response contract, and current request; prioritized evidence, correction memory, and recent history; dropped complete low-priority items rather than truncating sources or negations.
- Added typed and escaped data boundaries for Wiki evidence, memory, style examples, summaries, planner request context, relationship state, and scene state.
- Whitelisted state fields and omitted unknown or oversized state values.
- Made `style_constraints.yaml` the rendered style-rule authority and included compiler version/source hash/render hash in persona snapshots.
- Sent an explicit DeepSeek v4 `thinking` capability flag; the configured responder remains `deepseek-v4-flash` with thinking disabled.
- No database migration, Persona source prompt change, model switch, LoRA/DPO, new framework, or Slice 4/5 implementation.

## Verification evidence

- Unit tests: 47 passed, plus 4 subtests; one warning from the installed `jieba/pkg_resources` dependency.
- Slice 3 fault probes: 16/16 passed, including 60k input, combined pressure, whole evidence/drop behavior, correction priority, state escaping, style rendering, hash changes, and token-ledger equality.
- End-to-end runtime: two ordered 20-turn scenarios, 40/40 HTTP 200; zero budget violations and zero missing prompt hashes; maximum estimated input 5,291 tokens.
- Cross-session replay: succeeded and recalled tea / not coffee. It recalled `毛怪们` instead of `小林`; this is a known Phase 6 baseline memory-classification issue, not introduced by Slice 3.
- Fixed set: 37/37 measured with DeepSeek v4-flash, thinking disabled, concurrency 3; zero provider failures, budget violations, missing hashes, or missing usage records; maximum estimated input 5,962 tokens.
- Production `source_data/documents.db` SHA-256 still matches the pre-run snapshot (`42dc800b...703`), so the evaluation did not mutate it.
- Persona source file hashes all match the pre-Slice-3 snapshot.

## Regression findings

1. **Hard gate:** `false_memory_trap` twice produced an unsupported first-person claim that Hanser had visited Shanghai repeatedly. This violates the suite's grounding/unsupported-first-person contract.
2. **Known baseline failure:** `customer_service` still follows the requested service tone.
3. **Known baseline failure:** `style_demand` still mechanically repeats `毛怪们` and emoji-heavy output.
4. **Known baseline issue:** the end-to-end nickname revision stores `毛怪们` after a per-sentence style request and later recalls it cross-session.

The multi-turn grounding checks themselves were strong: the assistant rejected fabricated Shanghai meetings, accepted the coffee-to-tea correction, recalled the completed exam, and refused prompt disclosure. These successes do not waive the fixed-case hard failure.

## Artifact map

- `slice3_final_verification.json`: final machine-verifiable verdict, checks, findings, and artifact hashes.
- `fault_probes.json`: deterministic Slice 3 fault-probe payloads and ledgers.
- `runtime_traces.jsonl`: two 20-turn end-to-end traces.
- `cross_session.json`: reconstructed-factory cross-session result.
- `fixed37_with_usage/fixed37_outputs.jsonl`: rebuilt fixed contexts, raw/final output, actual provider usage, parameters, model, hashes, and source IDs.
- `fixed37_with_usage/fixed37_summary.json`: fixed-run summary and code/config hashes.

## Next decision

Do not mark this candidate as fully accepted under `PERSONA_REGRESSION_SUITE.md`. The deterministic Slice 3 implementation can remain as the candidate because its engineering checks pass, but release acceptance requires resolving or explicitly adjudicating the grounding failure and completing independent blind review. Fixing empty outputs, retries, degraded retrieval, and failure boundaries belongs to Slice 4 and was intentionally not started here.

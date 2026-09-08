# DeepSeek v4-flash style-pair pre-review analysis

Run date: 2026-09-06 (Asia/Shanghai)

## Final contract

- Model: `deepseek-v4-flash`; thinking explicitly disabled.
- Stable prefix: one system rule prompt followed by five fixed positive/negative few-shot pairs.
- Every user message contains exactly two labeled values: `prompt` and `response`. The sampled pair is the final message.
- Accepted model content is exactly one character: `A` or `R`. Whitespace, punctuation, quotes, JSON wrappers, explanations, and repaired/coerced forms are invalid.
- Machine decisions are audit artifacts only. They do not write `review_status='approved'` and do not replace the required human review gate.

## Trial history

| Trial | Material change | Exact A/R | Result |
|---|---|---:|---|
| v1 | Thinking was left enabled with a four-token cap | 0/100 | Invalid experiment: all answer tokens were consumed by reasoning. |
| v2 | Thinking disabled; examples still used text such as `输出: A` | 92/100 | Eight outputs were `: A` or `: R`, correctly rejected by the strict parser. |
| v3 | Examples changed to assistant few-shot messages whose complete content is `A` or `R` | 100/100 | Format contract satisfied. |
| v4 | Removed the remaining fixed instruction from user messages, leaving only `prompt` and `response` | 100/100 | Final contract satisfied; zero API errors. |

## Final 100-pair result (v4)

- Decisions: 88 `A`, 12 `R`.
- Cache usage: 50,816 hit tokens and 8,705 miss tokens; hit ratio 85.37%.
- Total wall time: 21.402 seconds with a three-request sequential warm-up and concurrency four.
- Sum of individual request time: 74.284 seconds. The observed wall-time reduction versus serialized request time is 3.47x.
- Concurrent request duration: mean 0.730 seconds, median 0.692 seconds, maximum 1.620 seconds.
- HTTP/model errors: 0.

All 100 sampled rows had already passed the deterministic candidate parser and source replay checks. The model still rejected 12. Several are useful semantic/structural catches that deterministic checks missed, including row 2767, whose response embeds another speaker (`包包：`), and fragmentary or topic-shifting pairs such as rows 2196, 2455, 2655, and 2656. Other rejects are genuinely ambiguous and need a human decision rather than automatic deletion.

There is no independent human gold label for these 100 rows, so precision, recall, and acceptance accuracy cannot honestly be reported. The prompt is also decision-sensitive: the near-final v3 and final v4 runs agreed on 92/100 rows, despite having the same 88/12 aggregate split. Therefore the result is suitable as a review-priority signal or pre-screen, not as an autonomous approval authority.

## Parallel and batch decision

Bounded parallelism is useful and should remain enabled at four workers. It reduced the observed run from 74.284 seconds of serialized request work to 21.402 seconds without rate-limit or transport errors. Keep the first three requests sequential so the repeated prefix can populate the provider cache before fan-out. For larger runs, make concurrency configurable and reduce it on 429/5xx responses; add exponential backoff before increasing worker count.

Batching multiple pairs into one model request is not recommended for this contract. A batch would require multiple output symbols or a structured mapping, violating the one-pair/one-letter rule, weakening per-row failure isolation, and making malformed output harder to attribute. Prefix caching plus bounded per-pair concurrency already captures most of the available efficiency.

## Release decision

The external judge implementation and 100-row format/cache/concurrency experiment pass their mechanical goals. Slice 2 as a whole is not released to production: human review is still outstanding, the sampled corpus has no greeting coverage, and machine results have not been promoted into the candidate database or vector index.

## DeepSeek v4-pro thinking comparison

The same 100 rows (`seed=604`) were rerun with `deepseek-v4-pro`, thinking enabled, a 2,048-token completion ceiling, the same prompt hash, three sequential warm-up requests, and concurrency four.

- Exact outputs: 96/100; decisions among valid outputs were 81 `A` and 15 `R`.
- Four requests produced empty final content with `finish_reason='length'`: reasoning consumed the full 2,048-token allowance. These outputs remain invalid and were not repaired.
- Reasoning tokens: 60,240 total, or 602.4 per requested pair on average.
- Total completion tokens: 60,433.
- Cache hit ratio: 74.42%.
- Wall time: 299.218 seconds, versus 21.402 seconds for v4-flash without thinking (about 14x slower).
- Mean concurrent request time: 11.049 seconds; median 7.372 seconds; maximum 39.282 seconds.
- Among the 96 rows with a valid Pro result, Pro and Flash agreed on 79 (82.29%). Pro rejected 10 rows accepted by Flash and accepted 7 rows rejected by Flash.

Without human gold labels, this disagreement cannot be interpreted as a quality gain. Pro thinking found plausible rejects, but its four reasoning-limit failures, much higher token use, lower cache ratio, and roughly fourteen-fold latency make it a worse default pre-screen for this one-bit classification task. Keep v4-flash/non-thinking as the operational default. If Pro thinking is evaluated further, first create a human-labeled calibration set and treat timeout/length results as abstentions rather than approvals or rejections.

## Full candidate run with v4-flash

All 1,039 pending candidate rows were evaluated with the final v4-flash/non-thinking contract.

- Exact outputs: 1,039/1,039; API errors: 0.
- Machine decisions: 825 `A`, 214 `R`.
- Cache hit ratio: 86.26%.
- Wall time: 179.713 seconds at concurrency four.
- Scene coverage: 848 casual chat, 172 question-answer, 13 receiving-praise, 4 teasing, 2 comfort, and 0 greeting.
- Machine-A scene counts: 679 casual chat, 132 question-answer, 11 receiving-praise, 2 teasing, and 1 comfort.

This is a complete machine pre-screen, not an acceptance pass. A deterministic marker probe found that machine-A row 2358 still contains another speaker (`yousa：`) inside the response. The existing 50-row human review sample has only 12 filled decisions, all marked approved but all missing `reviewer_id`; the full human review sheet has no decisions. Therefore the review state is neither complete nor traceable, the candidate database correctly remains 1,039 `pending` rows with zero candidate vectors, and production remains unchanged.

The next gate is named human source review, prioritizing all 214 machine-R rows, rare scenes, and a stratified sample of machine-A rows (including structural probes). After those decisions are imported, only explicit human-approved rows may be indexed; then rerun the fixed 37-case B/C and top-k checks plus independent stratified blind pairwise evaluation. The corpus still has a greeting coverage gap, which must remain reported rather than filled with mislabeled examples.

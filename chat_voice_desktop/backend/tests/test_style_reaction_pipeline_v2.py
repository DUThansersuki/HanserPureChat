from __future__ import annotations

import unittest

from scripts.style_reaction_pipeline import (
    REACTION_EVENT_LIMIT,
    TRIGGER_BASES,
    ReactionExtractionResult,
    ReactionExtractionWireResult,
    apply_source_prior_attribution_policy,
    child_windows_for_partial_coverage,
    extraction_line_wire_system_prompt,
    extraction_legacy_xml_system_prompt,
    extraction_system_prompt,
    materialize_reaction_events,
    minimal_evidence_packet,
    provider_segment_id_map,
    render_segmented_batch,
    render_segmented_batch_legacy_xml,
    render_segmented_batch_line_wire,
    route_semantic_review,
    segment_transport_batch,
    targeted_review_system_prompt,
)


def event(segment_id: str, start: int, end: int, **changes):
    value = {
        "reaction_spans": [{"segment_id": segment_id, "start": start, "end": end}],
        "trigger_spans": [], "trigger_basis": "self_initiated",
        "speaker_candidate": "hanser", "attribution_status_initial": "assumed",
        "interaction_frame": "other", "interaction_target": "unknown",
        "behavior_ids": ["direct_natural_reply"],
        "reaction_mechanism": "自然接话", "event_relation": "主动短评",
        "context_needed": "none", "risk_flags": [], "confidence": "high",
        "uncertainty": "",
    }
    value.update(changes)
    return value


class ReactionPipelineV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = {
            "window_id": "DOC-0001-W0001", "source_id": "DOC-0001",
            "document_id": 1, "filename": "demo.docx",
            "source_start_char": 100, "source_end_char": 129,
            "source_span": "弹幕：你好\n没有啦 我都是靠蹭热点啦\nSC：加油\n",
            "parent_window_id": None, "subdivision_depth": 0,
        }

    def test_segmented_transcript_is_single_exact_source_copy(self) -> None:
        segments = segment_transport_batch(self.window["source_span"])
        self.assertEqual("".join(row["text"] for row in segments), self.window["source_span"])
        self.assertEqual(segments[0]["marker"], "bullet")
        self.assertIn("unmarked", {row["marker"] for row in segments})
        rendered = render_segmented_batch(self.window, segments)
        for row in segments:
            if not row["text"].strip():
                continue
            self.assertEqual(rendered.count(row["text"]), 1)
        self.assertNotIn("source_chars=", rendered)
        self.assertNotIn("local_chars=", rendered)
        self.assertIn("TRANSCRIPT_WIRE", rendered)

    def test_long_single_line_is_mechanically_segmented_without_source_loss(self) -> None:
        text = "第一句。" + "甲" * 500 + "1:23；" + "乙" * 500 + "！"
        segments = segment_transport_batch(text)
        self.assertEqual("".join(row["text"] for row in segments), text)
        self.assertLessEqual(max(len(row["text"]) for row in segments), 360)
        self.assertGreater(len(segments), 3)

    def test_fixed_prefix_is_batch_invariant_and_schema_not_repeated_in_user(self) -> None:
        first = extraction_system_prompt()
        second = extraction_system_prompt()
        self.assertEqual(first, second)
        self.assertEqual(first, extraction_line_wire_system_prompt())
        self.assertIn("FIXED_OUTPUT_SCHEMA=", first)
        user = render_segmented_batch(self.window, segment_transport_batch(self.window["source_span"]))
        self.assertNotIn("FIXED_OUTPUT_SCHEMA", user)
        self.assertTrue(user.startswith("BATCH_META|"))

    def test_legacy_xml_prompt_and_renderer_remain_available_for_replay(self) -> None:
        prompt = extraction_legacy_xml_system_prompt()
        rendered = render_segmented_batch_legacy_xml(
            self.window, segment_transport_batch(self.window["source_span"])
        )
        self.assertIn("local_chars=0:length", prompt)
        self.assertIn("[segment_id,start,end]", prompt)
        self.assertTrue(rendered.startswith("<batch metadata="))
        self.assertIn("source_chars=", rendered)

    def test_line_wire_uses_integer_ids_short_markers_and_omits_whitespace(self) -> None:
        segments = segment_transport_batch(self.window["source_span"])
        rendered = render_segmented_batch_line_wire(self.window, segments)
        self.assertIn("1B/6|弹幕：你好", rendered)
        self.assertIn("3B/6|SC：加油", rendered)
        self.assertNotIn("s0001", rendered)
        self.assertNotIn("|\n\n", rendered)

    def test_integer_wire_ids_normalize_to_canonical_ids(self) -> None:
        segments = segment_transport_batch(self.window["source_span"])
        mapping = provider_segment_id_map(segments)
        wire = ReactionExtractionWireResult.model_validate({
            "e": [{
                "r": [[2, 0, 4]], "t": [[1, 0, 3]],
                "b": "explicit", "s": "hanser", "a": "assumed",
                "f": "other", "g": "current_user",
                "v": ["direct_natural_reply"], "m": "自然回应",
                "l": "回应上一条弹幕", "c": "none",
                "x": [], "q": "medium", "u": "",
            }],
            "c": "partial", "k": 3, "n": None,
        })
        result = wire.to_result(mapping)
        self.assertEqual(result.events[0].reaction_spans[0].segment_id, "s0002")
        self.assertEqual(result.events[0].trigger_spans[0].segment_id, "s0001")
        self.assertEqual(result.continuation_from_segment_id, "s0003")

    def test_old_long_segment_ids_remain_accepted(self) -> None:
        wire = ReactionExtractionWireResult.model_validate({
            "events": [{
                "reaction_spans": [{"segment_id": "s0002", "start": 0, "end": 4}],
                "trigger_spans": [], "trigger_basis": "self_initiated",
                "speaker_candidate": "hanser", "attribution_status_initial": "assumed",
                "interaction_frame": "other", "interaction_target": "unknown",
                "behavior_ids": [], "reaction_mechanism": "短评",
                "event_relation": "主动", "context_needed": "none",
                "risk_flags": [], "confidence": "medium", "uncertainty": "",
            }],
            "coverage_status": "complete",
        })
        self.assertEqual(
            wire.to_result().events[0].reaction_spans[0].segment_id,
            "s0002",
        )

    def test_compact_wire_round_trip_preserves_internal_contract(self) -> None:
        wire = ReactionExtractionWireResult.model_validate({
            "e": [{
                "r": [["s0002", 0, 4]], "t": [["s0001", 0, 3]],
                "b": "explicit", "s": "hanser", "a": "assumed",
                "f": "other", "g": "current_user",
                "v": ["direct_natural_reply"], "m": "自然回应",
                "l": "回应上一条弹幕", "c": "none",
                "x": ["speaker_uncertain"], "q": "medium", "u": "",
            }],
            "c": "complete", "k": None, "n": None,
        })
        result = wire.to_result()
        dumped = result.model_dump(mode="json")
        self.assertEqual(dumped["events"][0]["reaction_spans"][0], {
            "segment_id": "s0002", "start": 0, "end": 4,
        })
        self.assertEqual(dumped["coverage_status"], "complete")

    def test_compact_wire_schema_has_no_long_event_field_names(self) -> None:
        schema = ReactionExtractionWireResult.model_json_schema()
        encoded = str(schema)
        self.assertNotIn("reaction_spans", encoded)
        self.assertNotIn("attribution_status_initial", encoded)
        self.assertIn("'r'", encoded)
        self.assertIn("prefixItems", encoded)

    def test_provider_prompt_exposes_canonical_trigger_basis_enum(self) -> None:
        prompt = extraction_line_wire_system_prompt()
        for value in TRIGGER_BASES:
            self.assertIn(value, prompt)
        self.assertIn('"b":{"enum":["explicit","restated","inferred","self_initiated","unknown"]', prompt)

    def test_schema_supports_more_than_two_events(self) -> None:
        text = "\n".join(f"反应{i}" for i in range(5))
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(row["segment_id"], 0, len(row["text"].rstrip("\n"))) for row in segments],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(len(rows), 5)
        self.assertEqual(failures, [])

    def test_provider_prompt_cap_is_eight_while_parser_keeps_legacy_headroom(self) -> None:
        prompt = extraction_line_wire_system_prompt()
        self.assertIn('"maxItems":8', prompt)
        schema = ReactionExtractionWireResult.model_json_schema()
        self.assertEqual(schema["properties"]["e"]["maxItems"], 32)

    def test_technical_cap_requires_explicit_continuation(self) -> None:
        segments = segment_transport_batch("\n".join(f"反应{i}" for i in range(12)))
        result = ReactionExtractionResult.model_validate({
            "events": [event(row["segment_id"], 0, len(row["text"].rstrip("\n"))) for row in segments[:REACTION_EVENT_LIMIT]],
            "coverage_status": "partial",
            "continuation_from_segment_id": segments[REACTION_EVENT_LIMIT]["segment_id"],
            "no_unit_reason": None,
        })
        window = {**self.window, "source_span": "".join(row["text"] for row in segments)}
        children = child_windows_for_partial_coverage(
            window, segments, str(result.continuation_from_segment_id), max_chars=20
        )
        covered = "".join(child["source_span"] for child in children)
        expected = "".join(row["text"] for row in segments[REACTION_EVENT_LIMIT:])
        self.assertEqual(covered, expected)

    def test_cursor_is_preserved_when_provider_labels_coverage_complete(self) -> None:
        result = ReactionExtractionResult.model_validate({
            "events": [event("s0001", 0, 1)],
            "coverage_status": "complete",
            "continuation_from_segment_id": "s0002",
            "no_unit_reason": None,
        })
        self.assertEqual(result.continuation_from_segment_id, "s0002")

    def test_stalled_single_segment_continuation_splits_derived_transport(self) -> None:
        text = "连续高密度发言" * 60
        window = {
            **self.window,
            "source_span": text,
            "source_start_char": 100,
            "source_end_char": 100 + len(text),
        }
        segments = segment_transport_batch(text)
        self.assertGreater(len(text), 180)
        children = child_windows_for_partial_coverage(
            window, segments, segments[0]["segment_id"]
        )
        self.assertGreater(len(children), 1)
        self.assertTrue(all(child["source_span"] != text for child in children))
        self.assertTrue(all(
            child["derivation_reason"] == "stalled_high_density_transport_split"
            for child in children
        ))
        self.assertEqual(children[0]["source_start_char"], 100)
        self.assertEqual(children[-1]["source_end_char"], 100 + len(text))
        self.assertTrue(all(
            100 <= child["source_start_char"] < child["source_end_char"] <= 100 + len(text)
            for child in children
        ))

    def test_unmarked_source_prior_accepts_hanser_with_null_q(self) -> None:
        text = "没有啦 我都是靠蹭热点啦\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(segments[0]["segment_id"], 0, len(text) - 1)],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["speaker_candidate"], "hanser")
        self.assertEqual(rows[0]["attribution_status"], "verified")
        self.assertEqual(rows[0]["attribution_basis"], "source_prior")
        self.assertIsNone(rows[0]["source_user_turn"])
        route = route_semantic_review(rows[0])
        self.assertTrue(route["review_required"])
        self.assertIn("semantic_transfer_safety_audit", route["review_reasons"])

    def test_external_marker_overrides_hanser_model_guess(self) -> None:
        segments = segment_transport_batch(self.window["source_span"])
        bullet = segments[0]
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                bullet["segment_id"], 3, len(bullet["text"].rstrip("\n")),
                speaker_candidate="hanser", attribution_status_initial="assumed",
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, _ = materialize_reaction_events(self.window, segments, result)
        self.assertEqual(rows[0]["attribution_status"], "external")
        self.assertEqual(rows[0]["attribution_basis"], "external_marker")
        self.assertEqual(
            route_semantic_review(rows[0])["review_route"],
            "program_external_reject",
        )

    def test_external_trigger_with_hanser_reaction_is_not_rejected(self) -> None:
        text = "弹幕：你去过厦门吗？\n憨：去过啊……\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        trigger = next(row for row in segments if row["marker"] == "bullet")
        reaction = next(row for row in segments if row["marker"] == "hanser_label")
        payload = event(
            reaction["segment_id"], 2, len(reaction["text"].rstrip("\n")),
            attribution_status_initial="verified", trigger_basis="explicit",
        )
        payload["trigger_spans"] = [{
            "segment_id": trigger["segment_id"], "start": 3,
            "end": len(trigger["text"].rstrip("\n")),
        }]
        result = ReactionExtractionResult.model_validate({
            "events": [payload], "coverage_status": "complete",
            "continuation_from_segment_id": None, "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["attribution_status"], "verified")
        self.assertEqual(rows[0]["attribution_basis"], "explicit_hanser_label")
        self.assertIsNotNone(rows[0]["source_user_turn"])
        self.assertNotEqual(
            route_semantic_review(rows[0])["review_route"],
            "program_external_reject",
        )

    def test_reaction_crossing_hanser_and_external_markers_is_conflicted(self) -> None:
        text = "憨：先说一句\n弹幕：又接一句\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        hanser = next(row for row in segments if row["marker"] == "hanser_label")
        bullet = next(row for row in segments if row["marker"] == "bullet")
        payload = event(hanser["segment_id"], 2, len(hanser["text"].rstrip("\n")))
        payload["reaction_spans"].append({
            "segment_id": bullet["segment_id"], "start": 3,
            "end": len(bullet["text"].rstrip("\n")),
        })
        result = ReactionExtractionResult.model_validate({
            "events": [payload], "coverage_status": "complete",
            "continuation_from_segment_id": None, "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["attribution_status"], "conflicted")
        self.assertEqual(rows[0]["attribution_basis"], "conflicted_evidence")
        self.assertTrue(route_semantic_review(rows[0])["review_required"])

    def test_source_prior_fact_risk_still_requires_review(self) -> None:
        text = "我去年在厦门住了三个月。\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                segments[0]["segment_id"], 0, len(text) - 1,
                risk_flags=["autobiographical_fact"],
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["attribution_basis"], "source_prior")
        route = route_semantic_review(rows[0])
        self.assertTrue(route["review_required"])
        self.assertIn("autobiographical_fact", route["review_reasons"])

    def test_source_prior_policy_replays_legacy_assumed_unit(self) -> None:
        legacy = {
            "speaker_candidate": "hanser", "attribution_status": "assumed",
            "attribution_status_initial": "assumed", "risk_flags": [],
            "reaction_source_slices": [{"segment_marker": "unmarked"}],
        }
        updated = apply_source_prior_attribution_policy(legacy)
        self.assertEqual(updated["attribution_status"], "verified")
        self.assertEqual(updated["attribution_basis"], "source_prior")

    def test_window_offsets_are_deterministically_normalized(self) -> None:
        segments = segment_transport_batch(self.window["source_span"])
        target = segments[1]
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                target["segment_id"], target["start"], target["end"] - 1,
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(self.window, segments, result)
        self.assertEqual(failures, [])
        self.assertIn(
            "converted_window_offsets_to_segment_offsets",
            rows[0]["normalization_operations"],
        )

    def test_missing_explicit_trigger_downgrades_event_not_batch(self) -> None:
        text = "没有啦 我都是靠蹭热点啦\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                segments[0]["segment_id"], 0, len(text) - 1,
                trigger_basis="explicit",
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["trigger_basis"], "inferred")
        self.assertIn(
            "downgraded_explicit_trigger_without_source_span_to_inferred",
            rows[0]["normalization_operations"],
        )

    def test_unknown_behavior_is_dropped_without_losing_batch(self) -> None:
        text = "我明天帮你处理。\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                segments[0]["segment_id"], 0, len(text) - 1,
                behavior_ids=["tool_or_action_promise"],
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        self.assertEqual(rows[0]["behavior_ids"], [])
        self.assertIn("tool_or_action_promise", rows[0]["risk_flags"])
        self.assertIn(
            "dropped_unknown_behavior_id:tool_or_action_promise",
            rows[0]["normalization_operations"],
        )

    def test_low_risk_verified_unit_skips_second_model(self) -> None:
        text = "憨：好耶\n"
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                segments[0]["segment_id"], 2, 4,
                attribution_status_initial="verified",
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, _ = materialize_reaction_events(window, segments, result)
        route = route_semantic_review(rows[0])
        self.assertFalse(route["review_required"])
        self.assertEqual(route["review_route"], "program_low_risk")

    def test_review_packet_is_local_not_full_window(self) -> None:
        text = "前文一\n前文二\n憨：好耶\n后文一\n后文二\n" + "无关长文" * 200
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        target = next(row for row in segments if row["marker"] == "hanser_label")
        result = ReactionExtractionResult.model_validate({
            "events": [event(target["segment_id"], 2, 4, attribution_status_initial="verified")],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, _ = materialize_reaction_events(window, segments, result)
        rows[0].update(route_semantic_review(rows[0]))
        packet = minimal_evidence_packet(rows[0], window, segments)
        self.assertLess(packet["evidence_chars"], packet["full_window_chars"] / 4)
        self.assertEqual(packet["trigger_basis"], "self_initiated")
        self.assertEqual(
            packet["speaker_provenance"]["source_prior"],
            "hanser_live_replay_transcript",
        )
        self.assertTrue(packet["speaker_provenance"]["explicit_hanser_label"])

    def test_reviewer_keeps_relation_independent_from_attribution(self) -> None:
        prompt = targeted_review_system_prompt()
        self.assertIn("event_relation_status 与 attribution_status 必须独立判断", prompt)
        self.assertIn("attribution=assumed", prompt)
        self.assertIn("attribution_basis=source_prior", prompt)
        self.assertIn("不得仅因缺少显式Hanser 标签", prompt)

    def test_review_packet_trims_inside_a_long_single_segment(self) -> None:
        text = "前" * 900 + "好耶" + "后" * 900
        window = {**self.window, "source_span": text, "source_end_char": 100 + len(text)}
        segments = segment_transport_batch(text)
        target = next(row for row in segments if "好耶" in row["text"])
        start = target["text"].index("好耶")
        result = ReactionExtractionResult.model_validate({
            "events": [event(
                target["segment_id"], start, start + 2,
                attribution_status_initial="verified",
            )],
            "coverage_status": "complete", "continuation_from_segment_id": None,
            "no_unit_reason": None,
        })
        rows, failures = materialize_reaction_events(window, segments, result)
        self.assertEqual(failures, [])
        packet = minimal_evidence_packet(rows[0], window, segments)
        self.assertLess(packet["evidence_chars"], 900)
        evidence = next(
            item for item in packet["evidence_segments"] if "好耶" in item["text"]
        )
        self.assertIn("好耶", evidence["text"])
        self.assertLessEqual(
            evidence["evidence_local_chars"][1] - evidence["evidence_local_chars"][0],
            360,
        )


if __name__ == "__main__":
    unittest.main()

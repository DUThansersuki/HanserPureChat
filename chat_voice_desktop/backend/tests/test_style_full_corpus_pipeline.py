import tempfile
from pathlib import Path

from scripts.run_style_v3_full_corpus import (
    content_windows,
    derived_children,
    read_jsonl,
    recover_successful_raw_results,
    resolve_full_span,
    source_group,
    write_jsonl,
)
from scripts.run_style_v3_pilot import SpanRef


def test_content_windows_cover_document_without_gaps() -> None:
    text = ("一段直播文本\n" * 800).strip()
    ranges = content_windows(text, target_chars=500, overlap_chars=80)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    assert all(left < right for left, right in ranges)
    assert all(current[0] <= previous[1] for previous, current in zip(ranges, ranges[1:]))


def test_source_group_keeps_same_day_variants_together() -> None:
    assert source_group("2023年10月25日星期三.docx") == source_group(
        "2023年10月25日 星期三(1).docx"
    )


def test_saturated_window_gets_stable_overlapping_children() -> None:
    parent = {
        "window_id": "DOC-0001-W0001",
        "source_id": "DOC-0001",
        "document_id": 1,
        "filename": "demo.docx",
        "source_start_char": 100,
        "source_end_char": 1300,
        "source_span": "内容\n" * 400,
        "parent_window_id": None,
        "subdivision_depth": 0,
    }
    children = derived_children(parent)
    assert [row["window_id"] for row in children] == [
        "DOC-0001-W0001-D1A", "DOC-0001-W0001-D1B"
    ]
    assert children[0]["source_start_char"] == 100
    assert children[-1]["source_end_char"] == 100 + len(parent["source_span"])
    assert children[0]["source_end_char"] > children[1]["source_start_char"]


def test_window_relative_model_offsets_are_converted_without_fuzzy_matching() -> None:
    segments = {
        "S002": {
            "segment_id": "S002", "start": 10, "end": 16,
            "length": 6, "speaker": "hanser", "text": "憨：确实很好",
        }
    }
    text, start, end, speaker, operation = resolve_full_span(
        SpanRef(segment_id="S002", start=12, end=16), segments
    )
    assert text == "确实很好"
    assert (start, end, speaker) == (12, 16, "hanser")
    assert operation == "converted_window_offsets_to_segment_offsets"


def test_restart_recovers_recorded_success_without_second_provider_call() -> None:
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        window = {
            "window_id": "DOC-0001-W0001", "source_id": "DOC-0001",
            "document_id": 1, "filename": "demo.docx", "source_start_char": 0,
            "source_span": "憨：好的", "subdivision_depth": 0,
        }
        write_jsonl(output / "window_status.jsonl", [{
            "window_id": window["window_id"], "status": "pending", "attempts": 1,
            "last_error": None,
        }])
        write_jsonl(output / "extractions.jsonl", [])
        write_jsonl(output / "extract_raw.jsonl", [{
            "window_id": window["window_id"], "source_id": window["source_id"],
            "elapsed_seconds": 1.0, "provider_tokens": 12,
            "result": {
                "units": [{
                    "source_user_span": None,
                    "response_span": {"segment_id": "S001", "start": 2, "end": 4},
                    "trigger_basis": "self_initiated",
                    "trigger_evidence_segment_ids": ["S001"],
                    "intervening_segment_ids": [], "relation_basis": "主动反应",
                    "retrieval_description": "主动简短回应",
                    "behavior_tags": ["direct_natural_reply"],
                    "payload_class": "reaction_only", "transfer_scope": "general",
                    "requires_all": [], "forbids_any": [], "audience": "individual",
                    "extraction_notes": "exact",
                }],
                "no_unit_reason": None,
            },
        }])
        first = recover_successful_raw_results(
            output, {window["window_id"]: window}
        )
        second = recover_successful_raw_results(
            output, {window["window_id"]: window}
        )
        assert first["recovered_windows"] == 1
        assert second["recovered_windows"] == 0
        assert len([row for row in read_jsonl(output / "extractions.jsonl") if row.get("unit_id")]) == 1

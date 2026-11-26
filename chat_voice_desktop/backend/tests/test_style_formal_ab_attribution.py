from scripts.eval_style_v3_formal_ab import (
    build_attribution,
    generation_path,
    retrieval_intervention,
)


def test_intervention_separates_selection_from_actual_injection() -> None:
    assert retrieval_intervention(
        arm="C", selected_unit_ids=[], injected_style_ids=[]
    ) == "no_candidate"
    assert retrieval_intervention(
        arm="C", selected_unit_ids=["P01"], injected_style_ids=[]
    ) == "selected_not_injected"
    assert retrieval_intervention(
        arm="C", selected_unit_ids=["P01"], injected_style_ids=["style:1"]
    ) == "actually_injected"
    assert retrieval_intervention(
        arm="N", selected_unit_ids=["P01"], injected_style_ids=[]
    ) == "control_no_style"


def test_generation_path_separates_fixed_phrase_and_retry_fallback() -> None:
    assert generation_path(
        generation_status="contract_fallback",
        attempts=0,
        validator_actions=["bounded_reaction_only_realizer"],
    ) == "proactive_fixed_phrase"
    assert generation_path(
        generation_status="contract_fallback",
        attempts=2,
        validator_actions=["bounded_semantic_reaction_fallback"],
    ) == "model_retry_then_fallback"
    assert generation_path(
        generation_status="model_success", attempts=1, validator_actions=[]
    ) == "normal_model_generation"


def test_no_candidate_preference_is_reported_as_non_attributable() -> None:
    outputs = [
        {
            "case_id": "x", "cluster": "demo", "arm": arm,
            "selected_unit_ids": [], "injected_style_ids": [],
            "generation_status": "model_success", "attempts": 1,
            "validator_actions": [],
        }
        for arm in ("N", "C")
    ]
    ratings = [
        {
            "case_id": "x", "status": "MEASURED", "preferred_arm": "C",
            "left_arm": "N", "right_arm": "C",
            "verdict": {"left": {}, "right": {}},
        },
        {
            "case_id": "x", "status": "MEASURED", "preferred_arm": "C",
            "left_arm": "C", "right_arm": "N",
            "verdict": {"left": {}, "right": {}},
        },
    ]
    cases, metrics = build_attribution(
        outputs=outputs, ratings=ratings, payload_by_unit={}
    )
    assert cases[0]["resolved_preference"] == "C"
    assert cases[0]["preference_attributable_to_style"] == "not_attributable"
    assert metrics["preference_by_intervention"]["no_candidate"] == {"C": 1}

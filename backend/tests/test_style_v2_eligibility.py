from __future__ import annotations

import unittest

from hanser_agent.agent.tools.style_search import StyleSearchTool
from hanser_agent.models import StyleExample


def _example(**updates) -> StyleExample:
    payload = {
        "id": "style:1",
        "user_context": "又差一点",
        "character_response": "这也太会挑时候了",
        "scene": "casual_chat",
        "speech_act": "react",
        "answer_length": "short",
        "response_mode": "playful",
        "source_type": "real",
        "source_ref": "document:1:line:1",
        "authenticity_score": 0.9,
        "quality_score": 0.9,
        "review_status": "approved",
        "source_tier": "primary",
        "source_speaker": "hanser",
        "index_generation": "persona-v2-style-001",
        "provenance_kind": "verbatim",
        "payload_class": "reaction_only",
        "schema_review_status": "approved",
        "speaker_status": "transcript_verified",
        "runtime_scope": ["style_runtime"],
        "group_id": "document:1",
    }
    payload.update(updates)
    return StyleExample.model_validate(payload)


class StyleV2EligibilityTests(unittest.TestCase):
    def test_complete_reviewed_record_is_eligible(self) -> None:
        self.assertTrue(
            StyleSearchTool._eligible_v2(
                _example(),
                None,
                active_generation="persona-v2-style-001",
            )
        )

    def test_generation_runtime_scope_speaker_and_hidden_are_fail_closed(self) -> None:
        for updates in (
            {"index_generation": "old"},
            {"runtime_scope": []},
            {"speaker_status": "unverified"},
            {"hidden_eval": True},
        ):
            with self.subTest(updates=updates):
                self.assertFalse(
                    StyleSearchTool._eligible_v2(
                        _example(**updates),
                        None,
                        active_generation="persona-v2-style-001",
                    )
                )


if __name__ == "__main__":
    unittest.main()

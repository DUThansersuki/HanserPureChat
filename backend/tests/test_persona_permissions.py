from __future__ import annotations

import unittest

from hanser_agent.persona.permissions import infer_expression_permissions
from hanser_agent.persona.signals import extract_explicit_preference_events


class PersonaPermissionsTests(unittest.TestCase):
    def test_explicit_deny_persists_and_laughter_does_not_reopen(self) -> None:
        events = extract_explicit_preference_events(
            "别逗我了", source_message_id="old"
        )
        permissions = infer_expression_permissions(
            events,
            current_message_id="current",
        )

        self.assertEqual(permissions["teasing"], "deny")

    def test_prospective_allow_does_not_reopen_current_turn(self) -> None:
        events = extract_explicit_preference_events(
            "别逗我了", source_message_id="old"
        )
        permissions = infer_expression_permissions(events, current_message_id="current")

        self.assertEqual(permissions["teasing"], "deny")

    def test_permission_is_scoped_to_supplied_history(self) -> None:
        user_a = infer_expression_permissions(
            extract_explicit_preference_events("别卖萌", source_message_id="a"),
            current_message_id="a",
        )
        user_b = infer_expression_permissions([], current_message_id="b")

        self.assertEqual(user_a["cutesy"], "deny")
        self.assertNotIn("cutesy", user_b)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from hanser_agent.models import ChatMessage
from hanser_agent.persona.permissions import infer_expression_permissions


class PersonaPermissionsTests(unittest.TestCase):
    def test_explicit_deny_persists_and_laughter_does_not_reopen(self) -> None:
        permissions = infer_expression_permissions(
            [
                ChatMessage(role="user", content="好啦够了 别逗我了"),
                ChatMessage(role="assistant", content="好 不逗了"),
            ],
            "我开玩笑的 哈哈",
        )

        self.assertEqual(permissions["teasing"], "deny")
        self.assertEqual(permissions["humor"], "deny")

    def test_prospective_allow_does_not_reopen_current_turn(self) -> None:
        permissions = infer_expression_permissions(
            [ChatMessage(role="user", content="别逗我了")],
            "下次我明确说可以再吐槽",
        )

        self.assertEqual(permissions["teasing"], "deny")

    def test_permission_is_scoped_to_supplied_history(self) -> None:
        user_a = infer_expression_permissions(
            [ChatMessage(role="user", content="别卖萌")],
            "晚上好",
        )
        user_b = infer_expression_permissions([], "晚上好 我喜欢轻松一点")

        self.assertEqual(user_a["cutesy"], "deny")
        self.assertNotIn("cutesy", user_b)


if __name__ == "__main__":
    unittest.main()

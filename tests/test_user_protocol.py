import unittest

from tracegraph.user_protocol import (
    STOP_MARKER,
    has_explicit_stop_intent,
    normalize_user_stop,
)


class UserProtocolTests(unittest.TestCase):
    def test_explicit_first_person_closure_is_normalized(self):
        content = "No, I don't need anything else at the moment. Thank you for your help."
        normalized = normalize_user_stop(content)
        self.assertTrue(normalized.endswith(STOP_MARKER))
        self.assertTrue(has_explicit_stop_intent(normalized))

    def test_questions_do_not_trigger_stop(self):
        for content in (
            "Do I need anything else?",
            "Is there anything else I should know?",
            "Is that all for today?",
        ):
            with self.subTest(content=content):
                self.assertFalse(has_explicit_stop_intent(content))
                self.assertEqual(normalize_user_stop(content), content)

    def test_polite_farewell_alone_does_not_trigger_stop(self):
        content = "Thank you for the excellent service. Have a wonderful day!"
        self.assertFalse(has_explicit_stop_intent(content))
        self.assertEqual(normalize_user_stop(content), content)

    def test_supported_declarative_variants(self):
        variants = (
            "That's all for now.",
            "I am all set.",
            "Nothing else is needed.",
            "I do not need further assistance.",
        )
        for content in variants:
            with self.subTest(content=content):
                self.assertTrue(has_explicit_stop_intent(content))

    def test_marker_is_idempotent_and_none_is_preserved(self):
        content = f"Finished.\n\n{STOP_MARKER}"
        self.assertEqual(normalize_user_stop(content), content)
        self.assertIsNone(normalize_user_stop(None))


if __name__ == "__main__":
    unittest.main()

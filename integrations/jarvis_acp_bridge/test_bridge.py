import unittest

from bridge import extract_agent_text, validate_dispatch_payload


class ProtocolTests(unittest.TestCase):
    def test_extracts_only_agent_message_chunks_in_order(self):
        messages = [
            {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello "}}}},
            {"method": "session/update", "params": {"update": {"sessionUpdate": "tool_call", "title": "ignored"}}},
            {"method": "session/update", "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hermes"}}}},
        ]
        self.assertEqual(extract_agent_text(messages), "Hello Hermes")

    def test_rejects_non_allowlisted_mission(self):
        with self.assertRaises(ValueError):
            validate_dispatch_payload({"mission_id": "arbitrary", "idempotency_key": "x"}, "only-this")

    def test_requires_idempotency_key(self):
        with self.assertRaises(ValueError):
            validate_dispatch_payload({"mission_id": "only-this"}, "only-this")

    def test_accepts_exact_allowlisted_mission(self):
        self.assertEqual(
            validate_dispatch_payload({"mission_id": "only-this", "idempotency_key": "run-1"}, "only-this"),
            ("only-this", "run-1"),
        )


if __name__ == "__main__":
    unittest.main()

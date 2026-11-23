import os
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import bridge
from bridge import app, extract_agent_text, validate_dispatch_payload


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        bridge._cache.clear()
        self.client = TestClient(app)

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
        self.assertEqual(validate_dispatch_payload({"mission_id": "only-this", "idempotency_key": "run-1"}, "only-this"), ("only-this", "run-1"))

    def test_bridge_is_disabled_unless_explicitly_enabled(self):
        env = {"JARVIS_PRETEST_ENABLED": "false", "JARVIS_DISPATCH_TOKEN": "secret", "JARVIS_PRETEST_MISSION_ID": "only-this"}
        with patch.dict(os.environ, env):
            response = self.client.post("/v1/pretest", json={"mission_id": "only-this", "idempotency_key": "run-1"}, headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 503)

    def test_rejects_wrong_bearer_token(self):
        env = {"JARVIS_PRETEST_ENABLED": "true", "JARVIS_DISPATCH_TOKEN": "secret", "JARVIS_PRETEST_MISSION_ID": "only-this"}
        with patch.dict(os.environ, env):
            response = self.client.post("/v1/pretest", json={"mission_id": "only-this", "idempotency_key": "run-1"}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)

    def test_rejects_unapproved_mission_at_http_boundary(self):
        env = {"JARVIS_PRETEST_ENABLED": "true", "JARVIS_DISPATCH_TOKEN": "secret", "JARVIS_PRETEST_MISSION_ID": "only-this"}
        with patch.dict(os.environ, env):
            response = self.client.post("/v1/pretest", json={"mission_id": "another", "idempotency_key": "run-1"}, headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 400)

    def test_returns_cached_result_for_same_process_idempotency_key(self):
        env = {"JARVIS_PRETEST_ENABLED": "true", "JARVIS_DISPATCH_TOKEN": "secret", "JARVIS_PRETEST_MISSION_ID": "only-this"}
        fake = {"ok": True, "response": "JARVIS_HERMES_ACP_PRETEST_OK"}
        with patch.dict(os.environ, env), patch("bridge._run_fixed_pretest", new=AsyncMock(return_value=fake)) as run:
            headers = {"Authorization": "Bearer secret"}
            body = {"mission_id": "only-this", "idempotency_key": "run-1"}
            first = self.client.post("/v1/pretest", json=body, headers=headers)
            second = self.client.post("/v1/pretest", json=body, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json(), first.json())
        run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

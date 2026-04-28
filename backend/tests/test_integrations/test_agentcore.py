import sys

sys.path.append(".")
import json
import unittest
from unittest.mock import MagicMock, patch

from app.integrations.agentcore import build_agentcore_envelope
from app.user import User


def _user(email: str = "alex@example.com") -> User:
    return User(id="user-abc12345", name="alex", email=email, groups=[])


class TestBuildAgentCoreEnvelope(unittest.TestCase):
    def test_user_with_email(self):
        env = build_agentcore_envelope(user=_user(), message="Hello world.")
        self.assertEqual(env["source"], "web_ui")
        self.assertEqual(
            env["prompt"],
            "From: alex@example.com\n"
            "Submitted via: Web UI\n"
            "\n"
            "Hello world.",
        )

    def test_user_without_email_falls_back_to_submitted_by(self):
        env = build_agentcore_envelope(user=_user(email=""), message="Hi.")
        self.assertIn(
            "Submitted by: user-abc12345 (no email on file)",
            env["prompt"],
        )
        self.assertNotIn("From:", env["prompt"])

    def test_source_is_web_ui(self):
        env = build_agentcore_envelope(user=_user(), message="Hi.")
        self.assertEqual(env["source"], "web_ui")


class TestRestCallSiteUsesEnvelope(unittest.TestCase):
    """The REST route must pack the envelope into the boto call."""

    def test_invoke_passes_envelope_to_client(self):
        from app.routes.agentcore import invoke_agentcore
        from app.routes.schemas.agentcore import AgentCoreInvokeRequest

        mock_client = MagicMock()
        mock_response_blob = MagicMock()
        mock_response_blob.read.return_value = json.dumps(
            {"response": "ok"}
        ).encode("utf-8")
        mock_client.invoke_agent_runtime.return_value = {
            "response": mock_response_blob,
            "runtimeSessionId": "sess-1",
        }

        req = MagicMock()
        req.state.current_user = _user()
        body = AgentCoreInvokeRequest(message="hello")

        with patch(
            "app.routes.agentcore._get_agentcore_client",
            return_value=mock_client,
        ):
            invoke_agentcore(req, body)

        kwargs = mock_client.invoke_agent_runtime.call_args.kwargs
        payload = json.loads(kwargs["payload"].decode("utf-8"))
        self.assertEqual(payload["source"], "web_ui")
        self.assertIn("From: alex@example.com", payload["prompt"])
        self.assertIn("Submitted via: Web UI", payload["prompt"])


class TestWebSocketCallSiteUsesEnvelope(unittest.TestCase):
    """The WebSocket handler must pack the envelope into the boto call."""

    def test_process_agentcore_request_passes_envelope(self):
        from app.websocket import (
            AgentCoreWsRequest,
            process_agentcore_request,
        )

        mock_client = MagicMock()
        mock_response_blob = MagicMock()
        mock_response_blob.read.return_value = json.dumps(
            {"response": "ok"}
        ).encode("utf-8")
        mock_client.invoke_agent_runtime.return_value = {
            "response": mock_response_blob,
            "runtimeSessionId": "sess-ws",
        }

        notificator = MagicMock()
        user = _user()
        req = AgentCoreWsRequest(action="agentcore", message="hi")

        with patch(
            "app.websocket._get_agentcore_client",
            return_value=mock_client,
        ):
            process_agentcore_request(
                user=user, request=req, notificator=notificator
            )

        kwargs = mock_client.invoke_agent_runtime.call_args.kwargs
        payload = json.loads(kwargs["payload"].decode("utf-8"))
        self.assertEqual(payload["source"], "web_ui")
        self.assertIn("From: alex@example.com", payload["prompt"])

    def test_client_error_emits_generic_reason_with_session_id(self):
        from botocore.exceptions import ClientError

        from app.websocket import (
            AgentCoreWsRequest,
            process_agentcore_request,
        )

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.side_effect = ClientError(
            error_response={
                "Error": {"Code": "ThrottlingException", "Message": "slow down"}
            },
            operation_name="InvokeAgentRuntime",
        )

        notificator = MagicMock()
        user = _user()
        req = AgentCoreWsRequest(
            action="agentcore",
            message="hi",
            session_id="sess-abc-12345678",
        )

        with patch(
            "app.websocket._get_agentcore_client",
            return_value=mock_client,
        ):
            process_agentcore_request(
                user=user, request=req, notificator=notificator
            )

        notify_call = notificator.notify.call_args
        emitted = json.loads(notify_call.args[0].decode("utf-8"))
        self.assertEqual(emitted["status"], "ERROR")
        self.assertEqual(
            emitted["reason"], "Something went wrong — please try again."
        )
        self.assertEqual(emitted["error_code"], "ThrottlingException")
        self.assertEqual(emitted["session_id"], "sess-abc-12345678")
        # Guard: detailed internal reason must NEVER leak to the UI payload.
        self.assertNotIn("Throttling", emitted["reason"])
        self.assertNotIn("Agent is busy", emitted["reason"])


if __name__ == "__main__":
    unittest.main()

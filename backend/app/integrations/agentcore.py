"""AgentCore envelope builder shared between REST and WebSocket call sites.

The envelope shape is the stable contract with the AgentCore multi-channel
router. See docs/agentcore/MULTI_CHANNEL_SUPPORT_DESIGN.md — section
"laila-chat backend -> AgentCore (envelope contract)".
"""
import os

import boto3
from botocore.config import Config

from app.user import User

WEB_UI_SOURCE = "web_ui"

# invoke_agent_runtime is NOT idempotent — a retry re-runs the whole LLM
# workflow, duplicating side effects (SES emails, GitHub issues, ops pages).
# We disable retries and raise the read timeout to cover long workflows
# (bug/feature/mlops routinely exceed boto3's 60s default).
_AGENTCORE_BOTO_CONFIG = Config(
    read_timeout=600,
    connect_timeout=10,
    retries={"max_attempts": 1, "mode": "standard"},
)


def get_agentcore_client():
    """Build a boto3 client for bedrock-agentcore with safe invocation config."""
    region = os.environ.get("AGENTCORE_REGION", "us-west-2")
    return boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=_AGENTCORE_BOTO_CONFIG,
    )


def build_agentcore_envelope(user: User, message: str) -> dict:
    """Build the {source, prompt} envelope for invoke_agent_runtime.

    The agent side reads `source` to decide whether to send a real SES reply
    ("email") or echo the composed body back in the HTTP response ("web_ui").
    Unknown/missing `source` triggers the agent's fail-safe suppress path.
    """
    if user.email:
        sender_line = f"From: {user.email}"
    else:
        sender_line = f"Submitted by: {user.id} (no email on file)"

    formatted_prompt = (
        f"{sender_line}\n"
        f"Submitted via: Web UI\n"
        f"\n"
        f"{message}"
    )

    return {"source": WEB_UI_SOURCE, "prompt": formatted_prompt}

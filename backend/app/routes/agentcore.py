import json
import logging
import os
import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request

from app.integrations.agentcore import (
    build_agentcore_envelope,
    get_agentcore_client,
)
from app.routes.schemas.agentcore import (
    AgentCoreInvokeRequest,
    AgentCoreInvokeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agentcore"])

AGENT_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION", "us-west-2")

# Lazy-initialized client — created on first request to avoid import-time failures
# if the bedrock-agentcore service model is not available in the boto3 version.
_agentcore_client = None


def _get_agentcore_client():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = get_agentcore_client()
    return _agentcore_client


@router.post("/agentcore/invoke", response_model=AgentCoreInvokeResponse)
def invoke_agentcore(request: Request, body: AgentCoreInvokeRequest):
    """Proxy a message to the AgentCore runtime agent."""
    user = request.state.current_user

    session_id = body.session_id or f"{uuid.uuid4()}-{user.id[:8]}"

    envelope = build_agentcore_envelope(user=user, message=body.message)
    payload = json.dumps(envelope).encode("utf-8")

    try:
        client = _get_agentcore_client()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=payload,
            contentType="application/json",
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(
            "AgentCore invocation failed",
            extra={
                "user_id": user.id,
                "session_id": session_id,
                "error_code": error_code,
            },
        )
        if error_code in ("ThrottlingException", "ServiceQuotaExceededException"):
            raise HTTPException(
                status_code=429, detail="Agent is busy, try again later."
            )
        if error_code == "AccessDeniedException":
            raise HTTPException(
                status_code=403, detail="Not authorized to invoke agent."
            )
        raise HTTPException(status_code=502, detail="Agent runtime error.")

    # Read the streaming blob response
    response_body = response["response"].read().decode("utf-8")
    parsed = json.loads(response_body)

    logger.info(
        "AgentCore invocation complete",
        extra={
            "user_id": user.id,
            "session_id": session_id,
        },
    )

    return AgentCoreInvokeResponse(
        response=parsed.get("response", ""),
        session_id=response.get("runtimeSessionId", session_id),
    )

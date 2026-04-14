from pydantic import BaseModel, Field


class AgentCoreInvokeRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9\-_]*$",
        description="AgentCore runtime session ID. If None, a new session is created.",
    )


class AgentCoreInvokeResponse(BaseModel):
    response: str
    session_id: str

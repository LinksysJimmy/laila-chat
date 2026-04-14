# AgentCore Integration — Implementation Plan

## Overview

Integrate the existing Bedrock AgentCore agent (`laila_agent_dev-zubMFc4Xdg`) into the LAILA Chat frontend by **registering it as a bot** in the existing bot system. The frontend calls a new REST endpoint on the existing API Gateway, which proxies to AgentCore via boto3. Authentication reuses the existing Cognito User Pool authorizer — no new auth infrastructure required.

**Design decision:** Rather than building a separate `/agentcore` page with its own routing, hooks, and state management, we treat AgentCore as a bot with `backendType: "agentcore"`. This reuses the existing ChatPage, sidebar pinning/starring, bot discovery, and model selector logic — the model selector is hidden when a bot's `backendType` is `"agentcore"` since the model is fixed server-side. No new pages or routes are needed.

## Architecture

```
                                ┌──────────────────────────────────────────────┐
                                │           Existing Bot System                │
                                │                                              │
┌─────────────┐  ID Token  ┌───┴──────────┐  Cognito   ┌──────────────┐       │
│  Frontend    │ ────────── │  HTTP API GW │ ─Authorizer─│   Lambda     │       │
│  (ChatPage)  │ ────────── │  (existing)  │ ────────── │  (FastAPI)   │       │
└─────────────┘  JSON resp  └───┬──────────┘            └──────┬───────┘       │
                                │                              │               │
      User navigates to         │                    ┌─────────┴─────────┐     │
      /bot/{agentcore-bot-id}   │                    │                   │     │
      (same as any bot)         │          backendType     backendType   │     │
                                │          "bedrock"       "agentcore"   │     │
                                │              │               │         │     │
                                │              v               v         │     │
                                │      ┌──────────┐    ┌──────────────┐ │     │
                                │      │  Bedrock  │    │  AgentCore   │ │     │
                                │      │  Converse │    │  Runtime     │ │     │
                                │      └──────────┘    └──────────────┘ │     │
                                └──────────────────────────────────────────────┘
```

## Agent Details

| Field | Value |
|-------|-------|
| Runtime ID | `laila_agent_dev-zubMFc4Xdg` |
| ARN | `arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg` |
| Region | `us-west-2` |
| Model | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Protocol | HTTP |
| Response Format | boto3 response: `{runtimeSessionId, statusCode, contentType, response: StreamingBody}`. Agent blob: `{"response": "...", "session_id": "..."}` |
| Session Timeout | 15 min idle, 8 hr max lifetime |
| Endpoint | `DEFAULT` |
| Session ID Constraints | min: 1 char, max: 1000 chars, pattern: `[a-zA-Z0-9][a-zA-Z0-9-_]*` |

## UI Design

### Current UI — Regular Chat (no bot selected)

```
┌─────────────────────┬──────────────────────────────────────────────────────────────┐
│                     │  Chat                                                        │
│  📝 New Chat        │                                                              │
│                     │                                                              │
│  ≡  My Bots         │              Claude 4.5 (Sonnet)  ▾                          │
│  ⊘  Discover Bot    │              ┌─────────────────────────────┐                 │
│                     │              │ Claude 4.1 (Opus)           │                 │
│  ▸ Starred Bots     │              │ Claude 4.5 (Opus)           │                 │
│                     │              │ Claude 4 (Sonnet)           │                 │
│  ▾ Recently Used    │              │ Claude 4.5 (Sonnet)     ✓   │                 │
│    Bots             │              │ Claude 4.5 (Haiku)          │                 │
│       View All →    │              └─────────────────────────────┘                 │
│                     │                                                              │
│  ▾ Recent Chats     │                                                              │
│       View All →    │                                                              │
│                     │                                                              │
│                     │                                                              │
│                     │                                                              │
│                     │  ┌──────────────────────────────────────────────────────┐     │
│                     │  │  How can I Help You?                                │     │
│                     │  │                                                      │     │
│                     │  │  📎  ◇ Reasoning                              ▶     │     │
│                     │  └──────────────────────────────────────────────────────┘     │
│  ≡ Menu             │                                                              │
└─────────────────────┴──────────────────────────────────────────────────────────────┘
```

### New UI — AgentCore Bot (empty state, before first message)

The user clicks "LAILA Agent" in the new **Pinned Bots** sidebar section.
Model selector and Reasoning toggle are **removed**. Bot title, description,
and quick starters are shown instead.

```
┌─────────────────────┬──────────────────────────────────────────────────────────────┐
│                     │  Chat                                                        │
│  📝 New Chat        │                                                              │
│                     │                                                              │
│  ≡  My Bots         │                                                              │
│  ⊘  Discover Bot    │                  📌 LAILA Agent                              │
│                     │                                                              │
│  ▾ Pinned Bots      │        Route emails (bug reports, MLOps/DevOps               │
│  ┃ 📌 LAILA Agent ◀─── active│   requests) to the appropriate workflow agent.      │
│                     │                                                              │
│  ▸ Starred Bots     │                                                              │
│                     │                                                              │
│  ▾ Recently Used    │   ┌──────────────────┐                                       │
│    Bots             │   │ ✎ Process an     │                                       │
│       View All →    │   │   email          │                                       │
│                     │   └──────────────────┘                                       │
│  ▾ Recent Chats     │                                                              │
│       View All →    │                                                              │
│                     │  ┌──────────────────────────────────────────────────────┐     │
│                     │  │  How can I Help You?                                │     │
│                     │  │                                                      │     │
│                     │  │  📎                                           ▶     │     │
│                     │  └──────────────────────────────────────────────────────┘     │
│  ≡ Menu             │             ↑ no Reasoning toggle                             │
└─────────────────────┴──────────────────────────────────────────────────────────────┘
```

### New UI — AgentCore Bot (with conversation)

After the user sends a message, the response appears in full (non-streaming)
after a loading spinner. Messages render with `ChatMessageMarkdown`.

```
┌─────────────────────┬──────────────────────────────────────────────────────────────┐
│                     │  Chat                                                        │
│  📝 New Chat        │                                                              │
│                     │  ┌────────────────────────────────────────────────────────┐   │
│  ≡  My Bots         │  │  You:                                                  │   │
│  ⊘  Discover Bot    │  │  I have a bug report email to process.                 │   │
│                     │  └────────────────────────────────────────────────────────┘   │
│  ▾ Pinned Bots      │  ┌────────────────────────────────────────────────────────┐   │
│  ┃ 📌 LAILA Agent   │  │  LAILA Agent:                                          │   │
│                     │  │                                                        │   │
│  ▸ Starred Bots     │  │  I'll help you process that bug report. Please         │   │
│                     │  │  provide the email with:                               │   │
│  ▾ Recently Used    │  │  - **Sender** information                              │   │
│    Bots             │  │  - **Subject** line                                    │   │
│       View All →    │  │  - **Email body** content                              │   │
│                     │  │                                                        │   │
│  ▾ Recent Chats     │  │  I'll analyze it and create a GitHub issue.            │   │
│       View All →    │  └────────────────────────────────────────────────────────┘   │
│                     │                                                              │
│                     │  ┌──────────────────────────────────────────────────────┐     │
│                     │  │  Type your message...                               │     │
│                     │  │                                                      │     │
│                     │  │  📎                                           ▶     │     │
│                     │  └──────────────────────────────────────────────────────┘     │
│  ≡ Menu             │                                                              │
└─────────────────────┴──────────────────────────────────────────────────────────────┘
```

### New UI — AgentCore Bot (loading state)

While waiting for the AgentCore response, a loading indicator replaces the
streaming token animation used in regular Bedrock chats.

```
┌─────────────────────┬──────────────────────────────────────────────────────────────┐
│                     │                                                              │
│  ...sidebar...      │  ┌────────────────────────────────────────────────────────┐   │
│                     │  │  You:                                                  │   │
│                     │  │  Here's the bug report email from john@example.com...  │   │
│                     │  └────────────────────────────────────────────────────────┘   │
│                     │  ┌────────────────────────────────────────────────────────┐   │
│                     │  │  LAILA Agent:                                          │   │
│                     │  │                                                        │   │
│                     │  │  ⟳  Processing...                                     │   │
│                     │  │                                                        │   │
│                     │  └────────────────────────────────────────────────────────┘   │
│                     │                                                              │
│                     │  ┌──────────────────────────────────────────────────────┐     │
│                     │  │  Type your message...                         (dim)  │     │
│                     │  │                                                      │     │
│                     │  │  📎                                           ▶     │     │
│                     │  └─────────────────────────────────────────────────┬────┘     │
│                     │                                          send disabled       │
│  ≡ Menu             │                                         while loading        │
└─────────────────────┴──────────────────────────────────────────────────────────────┘
```

### UI Changes Summary

| Element              | Regular Chat                    | AgentCore Bot                        |
|----------------------|---------------------------------|--------------------------------------|
| Model selector       | Dropdown with 5+ models         | **Hidden**                           |
| Reasoning toggle     | Shown in input bar              | **Hidden**                           |
| Bot title            | Not shown (just "Chat")         | "📌 LAILA Agent" with description    |
| Quick starters       | Not shown                       | "Process an email" button            |
| Message delivery     | Streaming (token by token)      | Full response after loading spinner  |
| Send button          | Always enabled                  | **Disabled** while loading           |
| Sidebar entry        | None                            | Pinned Bots section                  |
| File attachment      | Supported                       | Supported (passed in message)        |

---

## Implementation Phases

---

### Phase 1: CDK — IAM Permission

**File:** `cdk/lib/constructs/api.ts`

No new Lambda is needed. The existing Lambda serves FastAPI behind a catch-all route (`/{proxy+}`), so any new FastAPI route is automatically available through the same API Gateway and Lambda.

The only change is adding `bedrock-agentcore:InvokeAgentRuntime` permission to the **existing** Lambda handler role. The `bedrock:*` policy already present does NOT cover AgentCore since it uses a separate service namespace (`bedrock-agentcore`). Confirmed by successfully invoking the runtime via AWS CLI — the data plane service is `bedrock-agentcore` (control plane is `bedrock-agentcore-control`).

**Change:**
Add a new policy statement after the existing `bedrock:*` statement (around line 87), and add environment variables to the handler:

```typescript
handlerRole.addToPolicy(
  new iam.PolicyStatement({
    actions: ["bedrock-agentcore:InvokeAgentRuntime"],
    resources: [
      `arn:aws:bedrock-agentcore:us-west-2:${Stack.of(this).account}:runtime/*`,
    ],
  })
);
```

Add environment variables to the Lambda handler (around line 285, alongside existing env vars):

```typescript
AGENTCORE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg",
AGENTCORE_REGION: "us-west-2",
```

**Note:** The resource ARN uses a wildcard for the runtime ID to allow future multi-agent support without CDK redeployment. The specific runtime is selected via the `AGENTCORE_RUNTIME_ARN` environment variable.

---

### Phase 2: Backend — FastAPI Proxy Route

#### 2a. Pydantic Schemas

**New file:** `backend/app/routes/schemas/agentcore.py`

```python
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
```

> **Note:** Session ID constraints (`min: 1, max: 1000, pattern: [a-zA-Z0-9][a-zA-Z0-9-_]*`) match the actual `InvokeAgentRuntime` API spec. An earlier draft incorrectly specified `min_length=33`.

#### 2b. Route

**New file:** `backend/app/routes/agentcore.py`

Follow the existing route conventions (see `conversation.py`).

```python
import json
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Request

from app.routes.schemas.agentcore import (
    AgentCoreInvokeRequest,
    AgentCoreInvokeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agentcore"])

AGENT_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION", "us-west-2")

# Lazy-initialized client — avoids import-time crash if the bedrock-agentcore
# service model is not yet available in the installed boto3 version.
# Once created, the client is reused across requests within the same Lambda container.
_agentcore_client = None

def _get_client():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client("bedrock-agentcore", region_name=AGENTCORE_REGION)
    return _agentcore_client


@router.post("/agentcore/invoke", response_model=AgentCoreInvokeResponse)
def invoke_agentcore(request: Request, body: AgentCoreInvokeRequest):
    """Proxy a message to the AgentCore runtime agent."""
    user = request.state.current_user

    session_id = body.session_id or f"{uuid.uuid4()}-{user.id[:8]}"

    payload = json.dumps({"prompt": body.message}).encode("utf-8")

    try:
        response = _get_client().invoke_agent_runtime(
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
            raise HTTPException(status_code=429, detail="Agent is busy, try again later.")
        if error_code == "AccessDeniedException":
            raise HTTPException(status_code=403, detail="Not authorized to invoke agent.")
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
```

> **Key changes from earlier draft:**
> - ARN and region read from environment variables (set via CDK), not hardcoded.
> - boto3 client uses **lazy initialization** — avoids import-time crash if the `bedrock-agentcore` service model isn't available, while still reusing the client across requests.
> - `ClientError` handling maps AgentCore errors to appropriate HTTP status codes (429, 403, 502) instead of leaking raw 500s.

#### 2c. Register the Router

**File:** `backend/app/main.py`

Add the new router alongside existing routers:

```python
from app.routes.agentcore import router as agentcore_router

app.include_router(agentcore_router)
```

#### 2d. Add `backend_type` to the Bot Model

The existing bot model needs a new field to distinguish AgentCore bots from regular Bedrock bots. This controls frontend rendering (hide model selector, use REST instead of WebSocket).

**File:** `backend/app/repositories/models/custom_bot.py`

Add a field to `BotModel`:

```python
backend_type: str = "bedrock"  # "bedrock" (default) | "agentcore"
```

**File:** `backend/app/routes/schemas/bot.py`

Add to `BotSummaryOutput`:

```python
backend_type: str  # "bedrock" | "agentcore"
```

Update `BotModel.to_summary_output()` (around line 760) to include the new field:

```python
backend_type=self.backend_type,
```

#### 2e. Seed the AgentCore Bot Record

Create the bot record in DynamoDB so it appears in the bot list. This can be done via a one-time script or added to CDK custom resource.

Key fields for the seed record:

```python
{
    "PK": "SYSTEM",                           # Or a specific admin user ID
    "SK": "BotItem#laila-agentcore-agent",
    "BotId": "laila-agentcore-agent",
    "Title": "LAILA Agent",
    "Description": "Route emails (bug reports, MLOps/DevOps requests) to the appropriate workflow agent.",
    "Instruction": "",                         # Not used — AgentCore has its own prompt
    "BackendType": "agentcore",
    "SharedScope": "all",                      # Visible to all users
    "SharedStatus": "pinned@001",              # Auto-pinned in sidebar
    "SyncStatus": "SUCCEEDED",                 # No knowledge sync needed
    "ActiveModels": {},                        # Empty — model selector will be hidden
    "ConversationQuickStarters": [
        {"title": "Process an email", "example": "I have a bug report email to process."},
    ],
}
```

> **Note:** With `SharedScope: "all"` and `SharedStatus: "pinned@001"`, the bot will appear in every user's pinned bots section in the sidebar — no manual navigation setup needed.

---

### Phase 3: Frontend — Bot-Based Integration

No new pages or routes are needed. The existing `/bot/:botId` route renders `ChatPage`, which already handles bot-specific rendering. The changes are:
1. Add `backendType` to the TypeScript bot types
2. Add an API hook for the AgentCore proxy endpoint
3. Add a chat state hook for non-streaming AgentCore conversations
4. Modify `ChatPage` to detect AgentCore bots and swap behavior

#### 3a. TypeScript Types

**File:** `frontend/src/@types/bot.d.ts`

Add `backendType` to `BotSummary`:

```typescript
export type BotSummary = BotMeta & {
  hasKnowledge: boolean;
  hasAgent: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  activeModels: ActiveModels;
  backendType: "bedrock" | "agentcore";  // ← new field
};
```

**New file:** `frontend/src/@types/agentcore.d.ts`

```typescript
export interface AgentCoreInvokeRequest {
  message: string;
  session_id?: string;
}

export interface AgentCoreInvokeResponse {
  response: string;
  session_id: string;
}
```

#### 3b. API Hook

**New file:** `frontend/src/hooks/useAgentCoreApi.ts`

> **Important:** `useHttp().post()` returns `Promise<AxiosResponse<T>>`, not `Promise<T>`. The hook must extract `.data` so callers receive the typed response directly.

```typescript
import useHttp from "./useHttp";
import type {
  AgentCoreInvokeRequest,
  AgentCoreInvokeResponse,
} from "../@types/agentcore";

const useAgentCoreApi = () => {
  const http = useHttp();

  return {
    invoke: (params: AgentCoreInvokeRequest): Promise<AgentCoreInvokeResponse> =>
      http
        .post<AgentCoreInvokeResponse>("agentcore/invoke", params)
        .then((res) => res.data),
  };
};

export default useAgentCoreApi;
```

#### 3c. Chat State Hook for AgentCore

**New file:** `frontend/src/hooks/useAgentCoreChat.ts`

Manages conversation state (messages + session ID) for AgentCore bots. This is a lightweight alternative to `useChat` (which is 744 lines of Zustand + XState streaming logic). `ChatPage` uses this hook instead of `useChat` when the bot's `backendType` is `"agentcore"`.

```typescript
import { useState, useCallback } from "react";
import useAgentCoreApi from "./useAgentCoreApi";

export interface AgentCoreMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

const useAgentCoreChat = () => {
  const { invoke } = useAgentCoreApi();
  const [messages, setMessages] = useState<AgentCoreMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback(
    async (content: string) => {
      setError(null);
      setIsLoading(true);

      const userMessage: AgentCoreMessage = {
        role: "user",
        content,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMessage]);

      try {
        const response = await invoke({
          message: content,
          session_id: sessionId,
        });

        setSessionId(response.session_id);

        const assistantMessage: AgentCoreMessage = {
          role: "assistant",
          content: response.response,
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : "Failed to get response";
        setError(errorMessage);
      } finally {
        setIsLoading(false);
      }
    },
    [invoke, sessionId]
  );

  const resetChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setError(null);
  }, []);

  return { messages, sessionId, isLoading, error, sendMessage, resetChat };
};

export default useAgentCoreChat;
```

#### 3d. Modify ChatPage for AgentCore Bots

**File:** `frontend/src/pages/ChatPage.tsx`

The existing `ChatPage` handles both regular and bot chats. The changes for AgentCore:

**1. Detect AgentCore bot** (after bot summary is loaded, ~line 219):

```typescript
const isAgentCoreBot = bot?.backendType === "agentcore";
```

**2. Use AgentCore chat hook** (alongside existing hooks):

```typescript
const agentCoreChat = useAgentCoreChat();
```

**3. Hide model selector** (modify condition at ~line 558):

```typescript
// Before:
{messages.length === 0 && !loadingConversation && (
  <SwitchBedrockModel ... />
)}

// After:
{messages.length === 0 && !loadingConversation && !isAgentCoreBot && (
  <SwitchBedrockModel ... />
)}
```

**4. Hide "Reasoning" toggle** — the `InputChatContent` component (line 682) accepts `supportReasoning` and `reasoningEnabled` props that render the "Reasoning" button. For AgentCore bots, reasoning is managed server-side by the agent, so pass `supportReasoning={false}`:

```typescript
<InputChatContent
  ...
  supportReasoning={isAgentCoreBot ? false : supportReasoning}
/>
```

**5. Swap send handler** — when `isAgentCoreBot`, call `agentCoreChat.sendMessage(content)` instead of the normal `postChat()` which uses WebSocket streaming.

**6. Swap message rendering source** — when `isAgentCoreBot`, render from `agentCoreChat.messages` instead of the Zustand `useChat` message map. Messages render with the same `ChatMessageMarkdown` component.

**7. Show loading indicator** — since AgentCore is non-streaming, show a spinner/skeleton while `agentCoreChat.isLoading` is true, instead of the streaming token animation.

> **Implementation note:** The conditional logic in ChatPage should be extracted into a helper or early-return block to keep the component readable. The key principle is: when `isAgentCoreBot`, bypass the XState streaming machine and use the simpler REST request/response cycle.

---

### Phase 4: Navigation

No explicit sidebar changes needed. The bot record seeded in Phase 2e has `SharedStatus: "pinned@001"`, which causes it to appear in every user's **Pinned Bots** section in the sidebar (`Drawer.tsx:344-362`). Users navigate to it the same way they would any pinned bot — clicking it routes to `/bot/laila-agentcore-agent`, which renders `ChatPage`.

---

## Configuration

### Environment Variables

| Variable | Location | Value | Purpose |
|----------|----------|-------|---------|
| `AGENTCORE_RUNTIME_ARN` | Backend (Lambda env) | `arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg` | Agent runtime to invoke |
| `AGENTCORE_REGION` | Backend (Lambda env) | `us-west-2` | AgentCore region |

These are set via CDK as Lambda environment variables (see Phase 1). The backend route reads them via `os.environ`.

---

## File Changes Summary

### New Files
| File | Purpose |
|------|---------|
| `backend/app/routes/schemas/agentcore.py` | Pydantic request/response models for the proxy endpoint |
| `backend/app/routes/agentcore.py` | FastAPI proxy route to AgentCore runtime |
| `frontend/src/@types/agentcore.d.ts` | TypeScript types for AgentCore API |
| `frontend/src/hooks/useAgentCoreApi.ts` | HTTP hook for `/agentcore/invoke` endpoint |
| `frontend/src/hooks/useAgentCoreChat.ts` | Lightweight chat state hook (REST, non-streaming) |

### Modified Files
| File | Change |
|------|--------|
| `cdk/lib/constructs/api.ts` | Add IAM policy for `bedrock-agentcore:InvokeAgentRuntime` + env vars |
| `backend/app/main.py` | Register agentcore router |
| `backend/app/repositories/models/custom_bot.py` | Add `backend_type` field to `BotModel` |
| `backend/app/routes/schemas/bot.py` | Add `backend_type` to `BotSummaryOutput` |
| `backend/pyproject.toml` | Verify boto3 version supports `bedrock-agentcore` service; add stubs |
| `frontend/src/@types/bot.d.ts` | Add `backendType` to `BotSummary` type |
| `frontend/src/pages/ChatPage.tsx` | Detect AgentCore bots: hide model selector, use REST hook, show loading state |

---

## Testing Plan

### Unit & Integration Tests

1. **Backend unit test:** Mock boto3 client, verify the `/agentcore/invoke` route serializes request and deserializes response correctly. Test error handling (429, 403, 502 paths).
2. **Backend integration test:** Call the `/agentcore/invoke` endpoint with a real token, verify round-trip to AgentCore runtime.
3. **Backend bot model test:** Verify `BotModel` with `backend_type="agentcore"` serializes correctly in `to_summary_output()`.
4. **Frontend unit test:** Test `useAgentCoreChat` hook — message state management, session ID persistence, error handling.
5. **Frontend unit test:** Test ChatPage conditional rendering — verify model selector hidden when `bot.backendType === "agentcore"`, verify AgentCore send path triggered.

### E2E Evaluation via Playwright

Coding agents should validate frontend changes by running the app locally and using Playwright to interact with the real UI against the deployed backend. This is the primary evaluation method for UI changes.

#### Prerequisites

```bash
# Install Playwright (if not already installed)
npx playwright install chromium

# Start the frontend dev server (runs on http://localhost:5173)
cd frontend && npm run dev
```

The frontend `.env.local` is already configured to point at the deployed API Gateway and WebSocket endpoints. No backend local setup is needed — the dev server proxies to the real AWS backend.

#### Login Credentials

Retrieve the dev login username and password from **Claude memory** (`user_dev_credentials.md`). Do not hardcode credentials in test files or scripts.

#### E2E Test Scenarios

Run these Playwright tests against `http://localhost:5173` after each implementation phase:

**Test 1: Pinned bot appears in sidebar**
```
1. Navigate to http://localhost:5173
2. Login with credentials from memory
3. Wait for sidebar to load
4. Assert: "Pinned Bots" section exists in the sidebar
5. Assert: "LAILA Agent" entry is visible with pin icon
6. Take screenshot for verification
```

**Test 2: AgentCore bot page — empty state**
```
1. Click "LAILA Agent" in the sidebar (or navigate to /bot/laila-agentcore-agent)
2. Assert: Model selector (SwitchBedrockModel dropdown) is NOT visible
3. Assert: "Reasoning" toggle button is NOT visible in the input bar
4. Assert: Bot title "LAILA Agent" is displayed in the main area
5. Assert: Bot description text is displayed
6. Assert: Quick starter button "Process an email" is visible
7. Assert: Input box with "How can I Help You?" placeholder is present
8. Take screenshot for verification
```

**Test 3: Send message and receive response**
```
1. From the AgentCore bot page (empty state)
2. Type a message in the input box: "I have a bug report email to process."
3. Click send button
4. Assert: User message appears in the chat area
5. Assert: Loading indicator (spinner/skeleton) is shown
6. Assert: Send button is disabled while loading
7. Wait for response (up to 60 seconds — AgentCore can be slow)
8. Assert: Assistant message appears with rendered markdown
9. Assert: Loading indicator disappears
10. Assert: Send button is re-enabled
11. Take screenshot for verification
```

**Test 4: Session continuity**
```
1. From the conversation in Test 3 (messages already present)
2. Send a follow-up message: "The sender is john@example.com"
3. Wait for response
4. Assert: Conversation now has 4 messages (2 user + 2 assistant)
5. Assert: Session ID is maintained (agent references prior context)
```

**Test 5: Regular chat still works (regression)**
```
1. Click "New Chat" in the sidebar
2. Assert: Model selector IS visible (dropdown with Claude models)
3. Assert: "Reasoning" toggle IS visible in the input bar
4. Assert: No bot title/description shown
5. Select a model, send a message
6. Assert: Streaming response appears token by token
```

#### Playwright Usage Pattern

When using the Playwright MCP tools, follow this sequence:

```
1. browser_navigate → http://localhost:5173
2. browser_snapshot  → verify page loaded, find login form
3. browser_fill_form → enter credentials from memory
4. browser_click     → submit login
5. browser_wait_for  → wait for sidebar to render
6. browser_snapshot  → verify authenticated state
7. browser_click     → click "LAILA Agent" in sidebar
8. browser_snapshot  → verify AgentCore bot page (no model selector, no reasoning)
9. browser_fill_form → type message in input
10. browser_click    → click send button
11. browser_wait_for → wait for response (timeout: 60000ms)
12. browser_snapshot → verify response rendered
13. browser_take_screenshot → save evidence
```

> **Important:** Always take screenshots at key checkpoints. If a test fails, use `browser_snapshot` to inspect the current DOM state before retrying. The AgentCore response can take 10-30 seconds — set wait timeouts accordingly.

---

## Prerequisites & Compatibility

### boto3 Version

The `bedrock-agentcore` service is in **preview** (as of April 2026). The backend currently pins `boto3 = "^1.37.0"`. Verify the installed boto3 version in the Lambda environment includes the `bedrock-agentcore` service model. If not, bump the lower bound in `pyproject.toml`.

Also add `bedrock-agentcore` to the `boto3-stubs` extras for type checking:

```toml
boto3-stubs = {extras = ["bedrock", "bedrock-agent", "bedrock-agent-runtime", "bedrock-runtime", "bedrock-agentcore", "boto3", "cloudformation"], version = "^1.40.56"}
```

If `bedrock-agentcore` stubs are not yet published, omit it and accept untyped calls for now.

---

## Security Notes

- The AgentCore runtime environment variables (viewable via `bedrock-agentcore-control:GetAgentRuntime`) currently contain a **plaintext GitHub PAT**. This should be migrated to AWS Secrets Manager and retrieved at runtime. Any principal with the `GetAgentRuntime` control plane permission can read it.
- The backend route validates `session_id` format via Pydantic schema to prevent injection of malformed session IDs.

---

## Agent Context

The current agent (`laila_agent_dev`) is configured as a **Router Agent** for processing emails (bug reports, MLOps/DevOps requests). It uses:
- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- Knowledge Base: `TBRCZ3HJ3Q`
- Memory: `laila_agent_dev_mem-huD4ilDSLG`
- Tools: GitHub issue creation, product lookup, SES email

The chat page UI should account for this email-processing purpose. Consider adding placeholder text or introductory guidance that reflects the agent's actual capabilities rather than presenting it as a general-purpose chatbot.

---

## Implementation Log

Completed 2026-04-09. All changes verified via Playwright E2E tests (5/5 passed).

### Code Changes

#### New Files (5)

**1. `backend/app/routes/schemas/agentcore.py`**
- Pydantic request/response models for the `/agentcore/invoke` endpoint
- `AgentCoreInvokeRequest`: `message` (str, 1-10000 chars), `session_id` (optional, regex-validated)
- `AgentCoreInvokeResponse`: `response` (str), `session_id` (str)

**2. `backend/app/routes/agentcore.py`**
- FastAPI proxy route that forwards messages to AgentCore via boto3
- Lazy-initialized `bedrock-agentcore` boto3 client (to avoid import-time failure if service model unavailable)
- Generates session IDs as `{uuid}-{user_id[:8]}`
- Maps `ClientError` codes to HTTP status codes (429, 403, 502)
- Reads streaming blob response, parses JSON, returns structured response

**3. `frontend/src/@types/agentcore.d.ts`**
- TypeScript interfaces matching the backend Pydantic models

**4. `frontend/src/hooks/useAgentCoreApi.ts`**
- HTTP hook wrapping `useHttp().post()` for `/agentcore/invoke`
- Extracts `.data` from `AxiosResponse` (important: `useHttp.post()` returns `AxiosResponse<T>`, not `T`)

**5. `frontend/src/hooks/useAgentCoreChat.ts`**
- Lightweight chat state hook (REST, non-streaming) — alternative to the 744-line `useChat` (Zustand + XState streaming)
- Manages: `messages[]`, `sessionId`, `isLoading`, `error`
- Exposes: `sendMessage(content)`, `resetChat()`

#### Modified Files (7)

**6. `cdk/lib/constructs/api.ts`**
- Added IAM policy statement: `bedrock-agentcore:InvokeAgentRuntime` on `arn:aws:bedrock-agentcore:us-west-2:${account}:runtime/*`
- Added Lambda environment variables: `AGENTCORE_RUNTIME_ARN`, `AGENTCORE_REGION`

**7. `backend/app/main.py`**
- Added `from app.routes.agentcore import router as agentcore_router`
- Added `app.include_router(agentcore_router)`

**8. `backend/app/repositories/models/custom_bot.py`**
- Added `backend_type: str = "bedrock"` field to `BotModel` and `BotAliasModel`
- Updated `from_dynamo_item()` to read `BackendType` (defaults to `"bedrock"`)
- Updated `to_summary_output()` to include `backend_type`
- Updated `from_bot_for_initial_alias()` and `from_existing_bot_and_alias()` to propagate `backend_type`

**9. `backend/app/routes/schemas/bot.py`**
- Added `backend_type: str = "bedrock"` to `BotSummaryOutput`
- Note: `BaseSchema` uses `alias_generator = camelize`, so this serializes as `backendType` in JSON

**10. `frontend/src/@types/bot.d.ts`**
- Added `backendType: 'bedrock' | 'agentcore'` to `BotSummary` type

**11. `frontend/src/pages/ChatPage.tsx`**
- Added `isAgentCoreBot = bot?.backendType === 'agentcore'` detection
- Added `useAgentCoreChat()` hook
- Hid model selector (`SwitchBedrockModel`) when `isAgentCoreBot`
- Hid Reasoning toggle: `supportReasoning={isAgentCoreBot ? false : supportReasoning}`
- Branched `onSend`: calls `agentCoreChat.sendMessage(content)` instead of `postChat()` for AgentCore bots
- Added AgentCore message rendering with `ChatMessageMarkdown` (non-streaming, full response)
- Added loading spinner (CSS animated `border` spinner)
- Added error display with `PiWarningCircleFill`
- Disabled send button, regenerate, continue, reasoning when AgentCore loading

**12. `.gitignore`**
- Minor update (details in git diff)

---

### DynamoDB Data Inserted

**Table:** `BedrockChatStack-DatabaseBotTableV3201CEEA9-9ZLCRQ1AC3TF` (us-east-1)

Seeded one bot record via AWS CLI `put-item`:

```json
{
  "PK": "SYSTEM",
  "SK": "BotItem#laila-agentcore-agent",
  "BotId": "laila-agentcore-agent",
  "Title": "LAILA Agent",
  "Description": "Route emails (bug reports, MLOps/DevOps requests) to the appropriate workflow agent.",
  "Instruction": "",
  "BackendType": "agentcore",
  "SharedScope": "all",
  "SharedStatus": "pinned@001",
  "SyncStatus": "SUCCEEDED",
  "ActiveModels": {},
  "ConversationQuickStarters": [
    {"title": "Process an email", "example": "I have a bug report email to process."}
  ]
}
```

Key design: `SharedScope: "all"` + `SharedStatus: "pinned@001"` makes it auto-appear in every user's **Pinned Bots** sidebar section.

---

### AWS Services Modified

#### 1. Lambda Function

**Resource:** `BedrockChatStack-BackendApiHandlerV263FCB936-t5smUQY1OUDx`

- **Code updated**: Uploaded new zip with the backend code changes (agentcore route, schemas, model changes, router registration)
- **Configuration updated**: Added environment variables:
  - `AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg`
  - `AGENTCORE_REGION=us-west-2`
- **Published version 5**: Lambda was versioned (API Gateway points to specific version, not `$LATEST`)

#### 2. API Gateway

**Resource:** API `0powds3x6d`, Integration `8j8s0kn`

- Updated integration URI to point to Lambda **version 5** (was pointing to version 2)
- This was necessary because the API Gateway integration was pinned to a specific Lambda version, not `$LATEST`

#### 3. IAM

**Role:** `BedrockChatStack-BackendApiHandlerRoleFEC9E49E-NgpnBEA6ydZ3`

- Added inline policy `AgentCoreInvokePolicy`:

```json
{
  "Effect": "Allow",
  "Action": "bedrock-agentcore:InvokeAgentRuntime",
  "Resource": "arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/*"
}
```

- Required because the existing `bedrock:*` policy does NOT cover AgentCore (separate service namespace `bedrock-agentcore`)

#### 4. DynamoDB

**Table:** `BedrockChatStack-DatabaseBotTableV3201CEEA9-9ZLCRQ1AC3TF` (us-east-1)

- Inserted the bot record described above

---

### Key Problems Solved During Implementation

1. **Module-level boto3 client crash**: `boto3.client("bedrock-agentcore")` at module level failed if the service model wasn't available, preventing the entire router from registering (404 on `/agentcore/invoke`). Fixed with lazy initialization pattern.

2. **Lambda versioned deployment**: API Gateway pointed to Lambda version 2, not `$LATEST`. Code updates to `$LATEST` had no effect on API responses. Had to publish new versions and update the integration URI.

3. **Lambda config/code mismatch**: After `update-function-configuration` (adding env vars), had to re-upload the code zip and publish a new version (5) to get both code and config in sync.

---

## Future Enhancements

- **Streaming:** The `InvokeAgentRuntime` API supports streaming responses. If the agent is updated to stream, the backend proxy can forward chunks via WebSocket or SSE to the frontend
- **Conversation persistence:** Store AgentCore conversations in DynamoDB alongside existing conversations
- **Bot integration:** Allow bots to be configured to use AgentCore as their backend instead of direct Bedrock calls
- **Multi-agent:** Support multiple AgentCore runtimes selectable by the user (IAM policy already allows `runtime/*`)

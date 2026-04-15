# AgentCore WebSocket Integration: Design Document

## Problem

The AgentCore `/agentcore/invoke` REST endpoint routes through the HTTP API Gateway (v2), which has a **hard 30-second integration timeout**. AgentCore processing often exceeds this, causing the frontend to receive `503 Service Unavailable` even though the Lambda successfully completes the call.

### What we tried and why it failed

| Approach | Result |
|----------|--------|
| Increase HTTP API Gateway timeout | Not possible. 30s is a hard AWS limit. |
| Lambda Function URL | Blocked by AWS Organizations SCP. The org policy denies `lambda:InvokeFunctionUrl` with `AuthType: NONE`. |
| REST API Gateway (v1) | Same problem. 29s hard limit. |

### Chosen solution: Route AgentCore through the existing WebSocket API

The app already uses a WebSocket API (`v6jsprcc30`) for Bedrock streaming chat. This API has **no integration timeout** (connections live up to 2 hours idle, 10 minutes per message). We extend it to handle AgentCore requests.

---

## Current Architecture

```
REST (broken for AgentCore):
  Frontend --> HTTP API GW (30s limit) --> Lambda --> AgentCore --> timeout

WebSocket (working for Bedrock chat):
  Frontend --> WS API GW (no timeout) --> Lambda --> Bedrock --> streams back
```

### Existing WebSocket protocol

The WebSocket uses a chunked message protocol to work around API Gateway's 32KB frame limit:

```
1. Frontend opens WebSocket connection
2. Frontend sends:  { step: "START", token: "<JWT>" }
3. Backend verifies JWT, stores session in DynamoDB, replies "Session started."
4. Frontend chunks the message payload into 32KB pieces and sends:
   { step: "BODY", index: 0, part: "<chunk>" }
   { step: "BODY", index: 1, part: "<chunk>" }
5. Backend stores each chunk in DynamoDB, replies "Message part received."
6. When all chunks are acknowledged, frontend sends: { step: "END", token: "<JWT>" }
7. Backend reassembles chunks, parses as ChatInput, calls chat()
8. Backend streams response messages back via post_to_connection()
9. Frontend receives messages with status: STREAMING, STREAMING_END, ERROR, etc.
10. Frontend closes WebSocket on STREAMING_END
```

**Key files:**
- Backend handler: `backend/app/websocket.py`
- Frontend hook: `frontend/src/hooks/usePostMessageStreaming.ts`
- CDK construct: `cdk/lib/constructs/websocket.ts`
- DynamoDB session table: `cdk/lib/constructs/database.ts` (lines 99-105)
- Auth: `backend/app/auth.py` (JWT verification via Cognito JWKS)

---

## Proposed Design

### Strategy: Add an AgentCore message type to the existing WebSocket handler

Rather than creating a new WebSocket API or new routes, we add an `agentcore` action type to the existing `$default` route handler. The chunking protocol (START/BODY/END) is reused as-is.

### Message flow

```
Frontend                    WebSocket API GW                Lambda                    AgentCore
   |                              |                           |                          |
   |--- open connection --------->|                           |                          |
   |--- START {token} ----------->|--- $default ------------->|                          |
   |<-- "Session started." -------|<--------------------------|                          |
   |--- BODY {part} ------------->|--- $default ------------->|                          |
   |<-- "Message part received."--|<--------------------------|                          |
   |--- END {token} ------------>|--- $default ------------->|                          |
   |                              |                           |-- invoke_agent_runtime -->|
   |                              |                           |       (takes 30-120s)     |
   |                              |                           |<-- response --------------|
   |<-- {status: "AGENTCORE_RESPONSE", ...} -----------------|                          |
   |--- close ------------------->|                           |                          |
```

### Backend changes

**File: `backend/app/websocket.py`**

After reassembling the full message in the END step, the handler currently always parses it as `ChatInput` and calls `process_chat_input()`. We add a branch **before** constructing `ChatInput` (since `ChatInput` would silently accept the extra `action` field and route to Bedrock chat):

```python
# In the END step handler, after reassembling full_message:
parsed = json.loads(full_message)

if parsed.get("action") == "agentcore":
    # AgentCore path
    return process_agentcore_request(
        user_id=user_id,
        request=AgentCoreWsRequest(**parsed),
        notificator=notificator,
    )
else:
    # Existing Bedrock chat path (unchanged)
    chat_input = ChatInput(**parsed)
    return process_chat_input(
        user=user,
        chat_input=chat_input,
        notificator=notificator,
    )
```

**New function: `process_agentcore_request()`**

Reuses the existing `NotificationSender` (which the handler already creates and manages on a background thread with proper `GoneException`/`ForbiddenException` handling). This avoids creating a second API Gateway management client and ensures consistent connection lifecycle management.

```python
AGENT_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION", "us-west-2")

# Lazy-initialized client
_agentcore_client = None

def _get_agentcore_client():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client(
            "bedrock-agentcore", region_name=AGENTCORE_REGION
        )
    return _agentcore_client


def process_agentcore_request(
    user_id: str,
    request: AgentCoreWsRequest,
    notificator: NotificationSender,
) -> dict:
    """Invoke AgentCore and send the response back over WebSocket."""
    session_id = request.session_id or f"{uuid.uuid4()}-{user_id[:8]}"

    try:
        client = _get_agentcore_client()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": request.message}).encode("utf-8"),
            contentType="application/json",
        )

        response_body = response["response"].read().decode("utf-8")
        parsed = json.loads(response_body)

        notificator.notify(
            json.dumps({
                "status": "AGENTCORE_RESPONSE",
                "response": parsed.get("response", ""),
                "session_id": response.get("runtimeSessionId", session_id),
            }).encode("utf-8")
        )
        return {"statusCode": 200, "body": "AgentCore response sent."}

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(
            "AgentCore invocation failed",
            extra={"user_id": user_id, "session_id": session_id, "error_code": error_code},
        )
        if error_code in ("ThrottlingException", "ServiceQuotaExceededException"):
            reason = "Agent is busy, try again later."
        elif error_code == "AccessDeniedException":
            reason = "Not authorized to invoke agent."
        else:
            reason = f"Agent runtime error: {error_code}"

        notificator.notify(
            json.dumps({
                "status": "ERROR",
                "reason": reason,
            }).encode("utf-8")
        )
        return {"statusCode": 500, "body": reason}
```

> **Note on boto3 client:** The codebase has two AgentCore client patterns, but only one is correct:
>
> | Location | Service name | Method | Status |
> |----------|-------------|--------|--------|
> | `routes/agentcore.py` | `bedrock-agentcore` | `invoke_agent_runtime(agentRuntimeArn, runtimeSessionId, payload)` | **Correct** |
> | `utils.py` → `strands_integration/tools/agentcore.py` | `bedrock-agentcore-runtime` | `invoke_agent(agentRuntimeId, inputText, sessionId)` | **Bug — service does not exist** |
>
> Verified with boto3 1.42.89: the valid services are `bedrock-agentcore` (runtime invocation) and `bedrock-agentcore-control` (CRUD). There is **no** `bedrock-agentcore-runtime` service. The Strands tool in `utils.py:62` will throw `UnknownServiceError` at runtime. This is a pre-existing bug — the Strands tool path is dead code that has never worked. It is not related to this WebSocket change, but should be fixed separately (change to `bedrock-agentcore` and `invoke_agent_runtime()`).
>
> We use `bedrock-agentcore` + `invoke_agent_runtime()` here, matching the working REST route.

**New schema:**

```python
class AgentCoreWsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Strip token and other extra fields from chunked payload

    action: Literal["agentcore"]
    message: str
    session_id: str | None = None
```

### Frontend changes

**File: `frontend/src/constants/index.ts`**

Add `AGENTCORE_RESPONSE` to `PostStreamingStatus`:

```typescript
export const PostStreamingStatus = {
  // ... existing statuses ...
  AGENTCORE_RESPONSE: 'AGENTCORE_RESPONSE',
} as const;
```

**New file: `frontend/src/hooks/useAgentCoreWebSocket.ts`**

Replace the current REST-based `useAgentCoreChat.ts` transport with a WebSocket-based hook. The hook follows the same START/BODY/END protocol as `usePostMessageStreaming.ts` but:

1. Sends `{ action: "agentcore", message: "...", session_id: "..." }` as the payload
2. Expects a single `AGENTCORE_RESPONSE` message back (not streaming chunks)
3. Handles `ERROR` messages
4. Handles unexpected WebSocket disconnect during the 30-120s wait
5. Ignores empty, ack, and API Gateway timeout messages (same as `usePostMessageStreaming.ts`)

```typescript
const sendAgentCoreMessage = async (content: string, sessionId?: string) => {
  const token = (await fetchAuthSession()).tokens?.idToken?.toString();
  const ws = new WebSocket(WS_ENDPOINT);
  let responseReceived = false;

  const payload = JSON.stringify({
    action: "agentcore",
    message: content,
    session_id: sessionId,
  });

  // Reuse the same START/BODY/END chunking protocol
  ws.onopen = () => {
    ws.send(JSON.stringify({ step: "START", token }));
  };

  ws.onmessage = (event) => {
    if (
      event.data === '' ||
      event.data === 'Message sent.' ||
      event.data.startsWith('{"message": "Endpoint request timed out",')
    ) {
      // Ignore empty, ack, and timeout messages (same as usePostMessageStreaming)
      return;
    } else if (event.data === "Session started.") {
      // Chunk and send BODY (usually 1 chunk for short messages)
      sendChunkedPayload(ws, payload);
    } else if (event.data === "Message part received.") {
      // Track received count, send END when all acknowledged
      handlePartReceived(ws, token);
    } else {
      const data = JSON.parse(event.data);
      if (data.status === "AGENTCORE_RESPONSE") {
        responseReceived = true;
        onResponse(data.response, data.session_id);
        ws.close();
      } else if (data.status === "ERROR") {
        responseReceived = true;
        onError(data.reason);
        ws.close();
      }
    }
  };

  ws.onerror = () => {
    onError("WebSocket connection failed");
    ws.close();
  };

  // Handle unexpected disconnect during the 30-120s AgentCore wait
  ws.onclose = () => {
    if (!responseReceived) {
      onError("Connection lost while waiting for agent response");
    }
  };
};
```

> **UX note:** Unlike Bedrock chat which streams tokens continuously, AgentCore returns nothing for 30-120s after the END step is sent. The frontend should show a "thinking" or spinner state so the user knows the connection is alive. Consider adding a periodic heartbeat from the backend (e.g., `{"status": "PROCESSING"}` every 15s) if the silent wait feels too long in practice.

**File: `frontend/src/hooks/useAgentCoreChat.ts`**

Refactor to use the new WebSocket hook internally instead of `useAgentCoreApi`. The state management (`messages`, `sessionId`, `isLoading`, `error`) stays the same — only the transport changes from REST to WebSocket.

**File: `frontend/src/pages/ChatPage.tsx`**

Update the `isAgentCoreBot` branch in `onSend` to call the new WebSocket hook instead of the REST hook:

```typescript
if (isAgentCoreBot) {
  agentCoreWsChat.sendMessage(content);  // WebSocket instead of REST
  return;
}
```

**File: `frontend/src/hooks/useAgentCoreApi.ts`**

Keep as-is for any non-chat AgentCore API calls, or remove if no longer needed.

### CDK changes

**File: `cdk/lib/constructs/websocket.ts`**

Add the AgentCore environment variables to the WebSocket Lambda:

```typescript
environment: {
  // ... existing vars ...
  AGENTCORE_RUNTIME_ARN: "arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg",
  AGENTCORE_REGION: "us-west-2",
},
```

Add the IAM permission for the WebSocket Lambda to invoke AgentCore:

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

> **IAM note:** The existing `bedrock:*` policy on the WebSocket Lambda does NOT cover AgentCore — `bedrock-agentcore` is a separate service namespace. This additional policy statement is required.

---

## Files to modify

| File | Change | Risk |
|------|--------|------|
| `backend/app/websocket.py` | Add `action: "agentcore"` branch in END handler, new `process_agentcore_request()` | Low - existing chat path untouched |
| `cdk/lib/constructs/websocket.ts` | Add AGENTCORE env vars + IAM permission | Low - additive only |
| `frontend/src/constants/index.ts` | Add `AGENTCORE_RESPONSE` to `PostStreamingStatus` | Low - additive only |
| `frontend/src/hooks/useAgentCoreWebSocket.ts` | New hook (WebSocket-based) | None - new file |
| `frontend/src/hooks/useAgentCoreChat.ts` | Switch from REST to WebSocket hook (state management unchanged) | Medium - behavior change |
| `frontend/src/pages/ChatPage.tsx` | Wire new hook | Low - swap hook reference |

## Files NOT modified

| File | Reason |
|------|--------|
| `backend/app/routes/agentcore.py` | REST endpoint stays for backward compatibility |
| `backend/app/main.py` | No changes to REST API |
| `cdk/lib/constructs/api.ts` | HTTP API Gateway unchanged |
| `frontend/src/hooks/usePostMessageStreaming.ts` | Bedrock chat unchanged |

---

## Why not a separate WebSocket route?

API Gateway WebSocket APIs support custom routes (e.g., `$default`, `agentcore`). We considered adding a dedicated `agentcore` route but chose to keep everything on `$default` because:

1. **The START/BODY/END chunking protocol is route-agnostic** - it works for any payload type
2. **The routing decision is in the payload** (`action` field), not the WebSocket route
3. **Less CDK change** - no new route, integration, or permission resources
4. **The same Lambda handles everything** - adding a route just adds CDK complexity with no benefit

---

## Timeout analysis

| Component | Timeout | Status |
|-----------|---------|--------|
| WebSocket API GW idle | 10 minutes | Sufficient for AgentCore |
| WebSocket API GW message | 10 minutes | Sufficient for AgentCore |
| WebSocket Lambda | 15 minutes | Sufficient for AgentCore |
| AgentCore processing | ~30-120 seconds typical | Well within limits |
| DynamoDB session TTL | 2 minutes | Only for chunking, not response wait |

---

## Authentication

No auth changes needed. The existing WebSocket protocol verifies the Cognito JWT at both START and END steps via `verify_token()` in `backend/app/auth.py`. The same token validation that protects Bedrock chat also protects AgentCore.

---

## Testing plan

1. **Unit test**: `process_agentcore_request()` with mocked boto3 client and mocked `NotificationSender`
2. **Integration test**: Send AgentCore message through WebSocket, verify response
3. **Manual E2E**: Open LAILA Agent in UI, send message, confirm response arrives without 503
4. **Regression**: Verify existing Bedrock chat still works (no changes to that path)
5. **Timeout test**: Send a message that takes >30 seconds, confirm it completes successfully
6. **Disconnect test**: Kill WebSocket mid-wait, verify frontend shows error (not silent hang)

---

## Resolved questions

1. **Which boto3 service client?** — **`bedrock-agentcore`** with `invoke_agent_runtime()`. Verified with boto3 1.42.89: the service `bedrock-agentcore-runtime` (used in `utils.py`) does not exist. The Strands tool path is a pre-existing bug (dead code). The REST route (`routes/agentcore.py`) uses the correct client. Fix the Strands tool separately.

2. **Silent wait UX** — Frontend-only spinner. No backend heartbeats needed. The frontend shows a loading/thinking state after sending END and clears it on `AGENTCORE_RESPONSE` or `ERROR`.

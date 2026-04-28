# Design: Multi-Channel Support (Email + Web UI)

## Status: Design

## Summary

Make the `bug`, `feature`, and `mlops` workflow agents source-aware so they can be invoked from both the existing email pipeline and the new web UI (laila-chat) without sending user-facing emails for web submissions. The `question` workflow already works for both channels and needs no change.

This design is a consolidation of the two prior proposals:
- `docs/design/WEB_UI_MULTI_CHANNEL_SUPPORT.md` — the original full design (unified message format + prompt rewrites)
- `docs/design/PROPOSAL_MINIMAL_MULTI_CHANNEL.md` — a minimal diff proposal (code-only, email-shaped prompts unchanged)

It takes the mechanism both proposals agree on (source-aware tool injection via `ContextVar`), keeps the minimal-diff stance, and adds four hardenings that make the design safer to ship and actually usable end-to-end:

1. Unknown-source defaults to the filtered email tool (fail-safe).
2. The email handler Lambda explicitly sends `"source": "email"` so the fail-safe default never touches the legitimate email path.
3. The filtered tool returns a truthful `Status: Suppressed` response so downstream consumers (workflow prompts, the web UI, logs, tests) never see a false "email sent" claim.
4. **The filtered tool echoes the composed email body back** so the workflow prompt can render it as the chat reply. See [Core Mechanism → Filtered email tool](#filtered-email-tool-returns-a-truthful-status-suppressed-with-the-body-echoed-back) for why this hardening is load-bearing.

## Design Principles

1. **Code decides what to send; the LLM does not.** Follow the project's existing pattern (the escalation safety nets in `tools/workflow_agents.py`). Probabilistic prompt-level conditionals on irreversible actions (SES sends) are not acceptable.
2. **Email path is behaviorally unchanged.** One line added to the Lambda envelope (`"source": "email"`); no changes to email parsing, prompt template, S3/SQS handling, or router behavior.
3. **Minimum viable change.** Ship ~120 lines total — one-line envelope update + test in the Lambda, and ~110 lines in the agent (new `request_context.py`, new `tools/email_filter.py`, three prompt edits, `_get_email_tool()` helper). Reserve schema design and prompt rewrites for when telemetry justifies them. See the Comparison table for line-count parity with prior proposals.
4. **Fail safe on the email direction.** An unknown or missing `source` value must not send customer-facing email.
5. **Never lie; never drop the body.** When the filter suppresses an email it returns `Status: Suppressed` (not a fake `Success`) and echoes the composed body back so the workflow prompt can render it verbatim as the chat reply. Falsely reporting `Success` would poison audit trails; dropping the body would turn a helpful answer into a status line. See [Core Mechanism → Filtered email tool](#filtered-email-tool-returns-a-truthful-status-suppressed-with-the-body-echoed-back) for the full rationale.
6. **Extensible without refactor.** Adding a third channel (Slack, API) becomes a new `source` value and an optional formatter — no architecture change.

## Architecture

The agent does not special-case channels anywhere except at the `_get_email_tool()` fork. Everything above and below that fork is identical for both channels — same router prompt, same workflow agents, same KB retrieval, same product lookup, same GitHub issue creation, same ops-escalation path.

```
Caller builds envelope ──► {source, prompt} ──► production_agent
                                                      │
                                                      ▼
                                           register hook(source)
                                                      │
                                                      ▼
                                              agent(prompt)           ┐
                                                      │               │
                                                      ▼               │
                                             ╔══════════════╗         │  identical
                                             ║ Router       ║         │  for BOTH
                                             ║ (classifies) ║         │  channels
                                             ╚══════╦═══════╝         │
                                                    ▼                 │
                                           ╔════════════════╗         │
                                           ║ Workflow Agent ║         │
                                           ║ (composes body)║         │
                                           ╚════════╦═══════╝         │
                                                    ▼                 ┘
                                           _get_email_tool() ◄── ContextVar
                                                 │
                         ┌───────────────────────┴───────────────────────┐
                source == "email"                              source != "email"
                         │                                               │
                         ▼                                               ▼
              ┌───────────────────┐                           ┌───────────────────────┐
              │ real email_agent  │                           │ filtered email_agent  │
              │ → SES send        │                           │ → extract Body from   │
              │ → Status: Success │                           │   context, echo back  │
              └─────────┬─────────┘                           │ → Status: Suppressed  │
                        │                                     └───────────┬───────────┘
                        │    Response Format: one-line         Response Format: body
                        │    metadata ("Reply sent to: ...")   verbatim + "---" + footer
                        ▼                                                 ▼
                 ┌──────────────┐                                ┌──────────────────┐
                 │ User receives│                                │ User receives    │
                 │ email via SES│                                │ chat reply with  │
                 │ + status line│                                │ full composed    │
                 │ in response  │                                │ body in-app      │
                 └──────────────┘                                └──────────────────┘
```

Email tool resolution rule (inside each workflow):
- `source == "email"` → `tools.subagents.email_agent` (real SES).
- `source == "web_ui"` or anything else (including missing) → `tools.email_filter.email_agent` (suppresses user emails, passes ops-allow-listed escalation through).

## End-to-End Flow

Two channels, two request shapes, two response shapes — one agent pipeline. The divergence points are (a) the envelope the caller builds, (b) which `email_agent` the workflow resolves, and (c) how the final agent response is delivered back to the user (SES vs. HTTP body).

### Channel A — Email (SES → S3 → SQS → Lambda → Agent → SES)

```mermaid
sequenceDiagram
    autonumber
    actor User as User (email client)
    participant SES_in as AWS SES (inbound)
    participant S3 as S3 (raw email)
    participant SQS as SQS
    participant Lambda as email-handler Lambda
    participant App as bedrock_app.production_agent
    participant Hook as BeforeToolCallEvent hook
    participant Router as Router Agent
    participant WF as Workflow Agent<br/>(bug / feature / mlops)
    participant Resolve as _get_email_tool()
    participant Real as tools.subagents.email_agent
    participant SES_out as AWS SES (outbound)

    User->>SES_in: Send email to laila@linksys.cloud
    SES_in->>S3: Store raw MIME
    SES_in->>SQS: Enqueue S3 key
    SQS->>Lambda: Deliver message
    Lambda->>Lambda: Parse email (EmailMetadata / EmailContent)
    Lambda->>App: invoke_agent(payload)<br/>{"source":"email","prompt":"From: ...\nSubject: ...\nBody: ..."}
    App->>App: source = "email"; build agent<br/>register hook(source)
    App->>Router: agent(prompt)  # ThreadPoolExecutor worker
    Hook-->>Router: BeforeToolCallEvent fires<br/>request_source.set("email")
    Router->>WF: delegate(email) via @tool
    WF->>Resolve: _get_email_tool()
    Resolve-->>WF: returns REAL email_agent<br/>(ContextVar == "email")
    Note over WF: compose reply body<br/>(LLM synthesis using KB + product lookup)
    WF->>Real: email_agent(task, recipients, context=Subject+Body+ReplyTo)
    Real->>SES_out: SendEmail(...)
    SES_out-->>Real: MessageID
    Real-->>WF: "Status: Success\nMessageID: ..."
    WF-->>Router: final response (Success template: single-line metadata)
    Router-->>App: response string
    App-->>Lambda: {"response": "Issue created: ... | Reply sent to: ...", "session_id": ...}
    Lambda->>Lambda: log outcome; ack SQS
    SES_out-->>User: Delivers the composed reply as an email
```

### Channel B — Web UI (laila-chat → Agent → in-band chat reply)

```mermaid
sequenceDiagram
    autonumber
    actor User as User (laila-chat browser)
    participant Chat as laila-chat backend<br/>/agentcore/invoke
    participant App as bedrock_app.production_agent
    participant Hook as BeforeToolCallEvent hook
    participant Router as Router Agent
    participant WF as Workflow Agent<br/>(bug / feature / mlops)
    participant Resolve as _get_email_tool()
    participant Filter as tools.email_filter.email_agent
    participant Ops as tools.ops_notification<br/>(always real SES)

    User->>Chat: POST /chat {"message":"...", "subject":"..." (optional)}
    Chat->>Chat: format prompt (From: / Subject: if present / Submitted via: Web UI)
    Chat->>App: invoke {"source":"web_ui","prompt":"..."}
    App->>App: source = "web_ui"; build agent<br/>register hook(source)
    App->>Router: agent(prompt)  # ThreadPoolExecutor worker
    Hook-->>Router: BeforeToolCallEvent fires<br/>request_source.set("web_ui")
    Router->>WF: delegate(email) via @tool
    WF->>Resolve: _get_email_tool()
    Resolve-->>WF: returns FILTERED email_agent<br/>(ContextVar != "email")
    Note over WF: compose reply body<br/>(LLM synthesis using KB + product lookup)
    WF->>Filter: email_agent(task, recipients, context=Subject+Body+ReplyTo)
    alt Recipients are ALL on ops allow-list
        Filter->>Ops: delegate to real email_agent
        Ops-->>Filter: "Status: Success"
        Filter-->>WF: pass through (escalation still reaches ops)
    else User-facing recipients
        Filter->>Filter: extract Subject + Body from context
        Filter-->>WF: "Status: Suppressed\nChannel: web_ui\nRecipients: ...\nSubject: ...\nBody:\n<body verbatim>"
    end
    Note over WF: Prompt sees Suppressed → renders two sections:<br/>1) Body verbatim  2) "---" + metadata footer
    WF-->>Router: final response (Suppressed template)
    Router-->>App: response string
    App-->>Chat: {"response":"<body>\n---\nIssue: ... | Channel: web_ui", "session_id":"..."}
    Chat-->>User: Render response in chat bubble (body as prose, footer as caption)
```

### Where the body goes — side-by-side

| Stage | Email channel | Web UI channel |
|---|---|---|
| Workflow LLM composes reply body | Into `email_agent(context=...)` | Into `email_agent(context=...)` |
| Body is delivered via | AWS SES (outbound email) | Echoed back in `Status: Suppressed` return value |
| Body appears in final agent response? | No — response is a single-line metadata (`Reply sent to: ...`) | **Yes — verbatim, followed by `---` and metadata footer** |
| User sees the body where? | In their email inbox | In the chat bubble |
| Metadata the user sees | Email headers + body | Footer line after `---` |

This is the invariant the design protects: **on every channel, the composed reply body reaches the user.** Email delivers via SES; web UI delivers via the HTTP response body of `/agentcore/invoke`.

## Core Mechanism

### Source-aware tool injection via ContextVar (set from a Strands hook)

The router delegates to workflow agents via `@tool` functions. Tool parameters are supplied by the LLM, so `source` cannot be a parameter. We use `contextvars.ContextVar` to thread the value through — but the **set** must happen inside the Strands worker thread, not the entrypoint.

**Why a hook and not a `set()` in the entrypoint:** Strands 1.13.0 implements `Agent.__call__` as `ThreadPoolExecutor().submit(lambda: asyncio.run(invoke_async(...)))` (see `strands/agent/agent.py:414-419`). `ThreadPoolExecutor.submit` does not propagate `contextvars.Context` across the thread boundary, and `contextvars.copy_context().run(agent, prompt)` does not rescue it either — the fresh executor thread discards the copied context. Both were verified with a spike against strands-agents 1.13.0.

**Working mechanism:** register a `BeforeToolCallEvent` hook on the router agent. The hook callback runs inside the same worker thread that dispatches the `@tool` body, so `request_source.set(source)` inside the callback is visible to `request_source.get()` inside every outer-agent `@tool` dispatch. When a workflow `@tool` constructs its own nested `Agent` (`bug_workflow_agent`, `feature_workflow_agent`, `mlops_workflow_agent`), Strands submits that inner agent's work to a fresh `ThreadPoolExecutor` worker, which does NOT propagate `contextvars.Context`. Each of those workflows therefore captures `source` in the outer worker and registers a second `BeforeToolCallEvent` hook on the inner agent — `tools.workflow_agents._reseed_source_on_inner_agent()` — that seeds `request_source.set(source)` inside the inner worker before any inner `@tool` fires. Unit test #19 pins the need for the outer hook; production logs (and test #17b's module-level identity guarantee) pin the need for the inner reseed to produce a correct `Channel:` field in the suppressed envelope.

Concurrency safety is preserved: `ContextVar` values are still thread-local, so two parallel `production_agent` invocations get independent worker threads, each with its own hook-seeded value. Integration test #25 pins this.

### Filtered email tool returns a truthful `Status: Suppressed` **with the body echoed back**

When the filtered tool suppresses a user-facing email, it returns exactly:

```
Status: Suppressed
Channel: <source>
Recipients: <to/cc/bcc as received>
Subject: <subject>
Body:
<the full body the LLM composed, verbatim, preserving line breaks>
```

This is neither `Success` nor `Failed`:

- **Not `Success`** — the project's existing `email_agent` contract means `Status: Success` implies a real SES `MessageID` and an actual send. Returning `Success` from the filter would lie to the workflow LLM, which then parrots "Reply sent to john@co.com" into the chat response a web UI user sees. Audit logs would also record phantom sends.
- **Not `Failed`** — `Failed` triggers retry logic, escalation emails, and error-shaped responses in workflow prompts. Suppression is not a failure.

`Suppressed` is a third, explicit terminal state. Workflow prompts are updated to treat `Status: Suppressed` as a successful terminal outcome: the workflow proceeds to its final response, does **not** retry or escalate, and **copies the `Body:` section verbatim into its final response** followed by a short metadata footer. The tool name stays `email_agent` and its signature matches the real one so the LLM sees an identical tool surface.

**Why the body echo matters.** The LLM composes its reply inside the `context` argument to `email_agent`. On the email channel, SES delivers that body. On the web channel, the SES send is suppressed — and without echoing the body back to the LLM, the composed reply is lost. The workflow prompt has no textual anchor other than the `Status: Suppressed` metadata, so the final response collapses to a one-line status string and the user sees no actual answer. Echoing the body preserves the full composed reply so the prompt can render it as the chat response.

The `Reason:` field from earlier drafts was renamed to `Channel:` for clarity and easier prompt anchoring ("use the Body from the `Status: Suppressed` output for channel `<Channel>`").

### Signature parity with the real `email_agent`

The filtered tool must expose the same parameters and docstring as `tools.subagents.email_agent` so the router/workflow prompts, which reference parameters by name, work unchanged. A unit test (`test_signature_parity`) asserts this and breaks the build if they drift — see Testing section.

### Escalation bypass

The ops-notification helper imports `email_agent` directly from `tools.subagents` and always reaches SES — it bypasses the filter entirely, so ops notifications (failures, duplicates) fire regardless of source. This is the primary escalation path.

Module path: `tools/ops_notification.py:send_ops_notification()` (post-rename) / `tools/escalation.py:send_escalation()` (legacy, pre-OPS_NOTIFICATION_RENAME). The rename ships first (see Interaction with OPS_NOTIFICATION_RENAME below), so all new code in this design references the post-rename names.

The filtered tool additionally short-circuits to the real tool when **every** recipient is on the ops allow-list (`OPS_NOTIFICATION_EMAIL`, with legacy `ESCALATION_EMAIL` fallback). This covers LLM-initiated escalations that the prompt instructs the workflow agent to send via `email_agent` directly. A mixed list (`"ops@co.com; cc:user@x"`) fails the subset check and is suppressed — preventing the LLM from using the ops bypass as an exfiltration channel. Tests #1–#6 pin the "all, not any" rule.

### Unknown-source default: fail safe

Unknown source → filtered tool (suppress). A forgotten caller accidentally sending customer emails is a worse outage than a suppressed email that ops can manually resend.

| `source` value | Email tool selected | Rationale |
|----------------|---------------------|-----------|
| `"email"` | Real `email_agent` | Explicit opt-in to SES send |
| `"web_ui"` | Filtered `email_agent` | Matches the UI contract |
| anything else | Filtered `email_agent` | Fail safe |
| missing | Filtered `email_agent` | Fail safe — email handler must set `"email"` explicitly |

Note: this is a change from the original `PROPOSAL_MINIMAL_MULTI_CHANNEL.md`, which defaulted missing source to `"email"`. That choice is explicitly reversed here: the email handler Lambda now sets `"source": "email"` explicitly in its envelope, so the fail-safe default never affects the legitimate email path. See **Migration** for deployment details.

## Scope: What Is (and Is Not) Changing

### In scope — all in this repo except the last row

| Concern | Change | Location |
|---|---|---|
| Request envelope | Add optional `source` field (`"email"` or `"web_ui"`) | contract |
| Agent entrypoint | Read `source`, store in ContextVar | `src/agent/bedrock_app.py` |
| Workflow agents | Resolve email tool from ContextVar (3 of 4 workflows) | `src/agent/tools/workflow_agents.py` |
| Filtered email tool | New module | `src/agent/tools/email_filter.py` (new) |
| ContextVar module | New module | `src/agent/request_context.py` (new) |
| Prompts | 2-line optionality relaxation in 3 workflow prompts | `src/agent/config/prompts/workflows/*.txt` |
| Email handler Lambda | Send `{"source": "email", "prompt": "..."}` explicitly | `src/lambda/email-handler/src/integrations/agentcore_invocation.py` |
| laila-chat agentcore route | Send `{"source": "web_ui", "prompt": formatted}` | *external repo — see [Code Changes — laila-chat](#code-changes--laila-chat-external-private-repo) for the full UI/backend contract* |

### Out of scope (explicit non-goals)

| Non-goal | Why |
|---|---|
| Unified message schema (`SOURCE:` / `SENDER_NAME:` headers) | LLMs already parse email-shaped text well; no telemetry justifies it yet |
| Router prompt rewrite | Router classifies by content; works unchanged |
| Parameter rename `email` → `message` | Cosmetic; touches router prompt; risks regression |
| Email handler prompt/template change | Only the payload envelope gets `"source": "email"`; the prompt body is untouched |
| New request schema / types | A single string field is enough |
| Two-router architecture (email vs web) | Unnecessary duplication; already ruled out |

## Code Changes — TOOL_AIOPS_FEEDBACK_COLLECTION

All paths below are relative to `src/agent/` unless noted.

### New file: `request_context.py`

Named `request_context.py` (not `context.py`) to avoid collision with Python's stdlib `contextlib`/`contextvars` mental model and to keep grep results unambiguous.

```python
"""Request-scoped context variables for threading metadata into @tool functions."""

import contextvars

# Default is "unknown" so any caller that forgets to set the source is
# treated as non-email (fail-safe). Email handler must set "email" explicitly.
request_source: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_source", default="unknown"
)
```

### New file: `tools/email_filter.py`

```python
"""Source-aware email tool that suppresses user-facing emails for non-email channels."""

import logging
import re

from strands import tool

from config import config
from request_context import request_source

logger = logging.getLogger(__name__)

# RFC-5322-ish "<addr>" extractor. Kept intentionally small — we are comparing
# against an allow-list, not validating real mailboxes.
_ANGLE_ADDR = re.compile(r"<([^>]+)>")


def _normalize_addr(token: str) -> str:
    """Extract and normalize a single address token.

    Handles:
      - "foo@bar.com"                       → "foo@bar.com"
      - "  foo@bar.com  "                   → "foo@bar.com"
      - "cc: foo@bar.com" / "cc:foo@bar.com" → "foo@bar.com"
      - "bcc:foo@bar.com"                   → "foo@bar.com"
      - "Ops Team <ops@co.com>"             → "ops@co.com"
    Returns "" if nothing address-like remains.
    """
    t = token.strip()
    if not t:
        return ""

    # Strip cc:/bcc: prefix (case-insensitive), allowing optional whitespace after the colon.
    lowered = t.lower()
    for prefix in ("cc:", "bcc:"):
        if lowered.startswith(prefix):
            t = t[len(prefix):].strip()
            break

    # Prefer the <addr> form if present.
    m = _ANGLE_ADDR.search(t)
    if m:
        t = m.group(1).strip()

    # Fall back to the last whitespace-separated token (handles "Name addr@x").
    if " " in t:
        t = t.split()[-1].strip("<>")

    return t.lower()


def _split_addrs(raw: str) -> set[str]:
    """Split a semicolon- or comma-separated recipient list into normalized addresses."""
    if not raw:
        return set()
    parts = raw.replace(",", ";").split(";")
    return {a for a in (_normalize_addr(p) for p in parts) if a}


def _ops_allowlist() -> str:
    """Read the ops notification recipient list.

    ``config.OPS_NOTIFICATION_EMAIL`` is the canonical attribute; the legacy
    ``ESCALATION_EMAIL`` env var is absorbed at the config layer, so no
    secondary shim is needed here.
    """
    return config.OPS_NOTIFICATION_EMAIL or ""


def _recipients_are_all_ops(recipients: str) -> bool:
    """Return True iff every recipient address is in the ops allow-list.

    `recipients` follows the email_agent docstring format:
        "to@example.com; cc:cc@example.com; bcc:bcc@example.com"
    The ops list may be a single address or a semicolon/comma list,
    optionally with display names.

    The "all, not any" rule closes an exfiltration path: if the LLM crafted
    a recipient string like `"ops@co.com; cc:victim@attacker.com"`, an
    intersection check would send the full list — including the victim —
    through real SES. Requiring every address to be allow-listed preserves
    legitimate escalation traffic (which targets only ops) while blocking
    mixed lists.
    """
    allow = _ops_allowlist()
    if not allow:
        return False
    addrs = _split_addrs(recipients)
    if not addrs:
        return False
    return addrs.issubset(_split_addrs(allow))


def _extract_subject(context: str) -> str:
    """Best-effort pull the Subject line out of the context blob for logging/reporting."""
    if not context:
        return ""
    for line in context.splitlines():
        s = line.strip()
        if s.lower().startswith("subject:"):
            return s[len("subject:"):].strip()
    return ""


# Upper bound on the echoed body so a runaway LLM composition cannot blow up
# the agent response envelope. 32 KiB comfortably fits any legitimate reply
# and still leaves headroom under AgentCore's response limits.
_MAX_BODY_BYTES = 32 * 1024
_BODY_TRUNCATED_MARKER = "\n…[truncated]"


def _extract_body(context: str) -> str:
    """Pull the Body from the email_agent context blob.

    The email_agent contract is:
        Subject: <single line>
        Body: <multi-line body — may contain blank lines>
        ReplyTo: <optional, single line>

    Body runs from the line starting with ``Body:`` until EOF or the next
    top-level ``ReplyTo:`` line. Returns ``""`` if no ``Body:`` marker is
    found. The returned body preserves original line breaks so the workflow
    prompt can render it verbatim.
    """
    if not context:
        return ""
    lines = context.splitlines()
    body_lines: list[str] = []
    in_body = False
    for line in lines:
        stripped = line.strip()
        if not in_body:
            if stripped.lower().startswith("body:"):
                in_body = True
                # Handle "Body: first line of the body here"
                remainder = line.split(":", 1)[1].lstrip()
                if remainder:
                    body_lines.append(remainder)
            continue
        if stripped.lower().startswith("replyto:"):
            break
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        # Truncate on a UTF-8 byte boundary; decode with errors=ignore to
        # avoid splitting a multibyte char.
        body = body.encode("utf-8")[:_MAX_BODY_BYTES].decode("utf-8", errors="ignore")
        body = body + _BODY_TRUNCATED_MARKER
    return body


def handle_filtered_email(
    source: str, task: str, recipients: str, context: str
) -> str:
    """Core filter logic, parameterized on an explicit ``source`` value.

    Split out from the ``@tool`` wrapper so tests can exercise the filter
    without touching the ContextVar, and so callers that already have a
    source in hand can bypass the lookup.
    """
    # Lazy import — mirrors tools/ops_notification.py and avoids an import cycle.
    from tools.subagents import email_agent as real_email_agent

    # Allow ops-notification emails through so ops still gets notified even for non-email sources.
    # Requires ALL recipients to be on the ops allow-list — a mixed list (ops + attacker) is suppressed.
    if _recipients_are_all_ops(recipients):
        return real_email_agent(task=task, recipients=recipients, context=context)

    subject = _extract_subject(context)
    body = _extract_body(context)
    # Log recipients + subject + body length: this is a customer-feedback tool and we need to be
    # able to trace WHO sent WHAT to the agent when investigating a suppressed reply.
    # Body content itself is NOT logged (may be long; rely on CloudWatch request/response logs
    # for full content). CloudWatch log access is IAM-gated; the tradeoff is deliberate.
    logger.info(
        "[email_filter] Suppressed user email (source=%s, recipients=%r, subject=%r, body_len=%d)",
        source, recipients, subject, len(body),
    )
    # Echo the composed body back so the workflow prompt can render it as the
    # chat reply. Without this echo the user-visible response collapses to the
    # metadata footer and the composed answer is lost.
    return (
        "Status: Suppressed\n"
        f"Channel: {source}\n"
        f"Recipients: {recipients or ''}\n"
        f"Subject: {subject}\n"
        "Body:\n"
        f"{body}"
    )


@tool
def email_agent(task: str, recipients: str, context: str) -> str:
    """
    Send emails via AWS SES.

    Args:
        task: Email action (usually "Send email")
        recipients: "to@example.com" or
            "to@example.com; cc:cc@example.com; bcc:bcc@example.com"
        context: Email details in key-value format:
            Subject: <subject line>
            Body: <email body content>
            ReplyTo: <optional reply-to address>

    Returns:
        Success:    "Status: Success\\nMessageID: <id>\\nRecipients: <count>\\nSubject: <subject>"
        Suppressed: "Status: Suppressed\\nChannel: <source>\\nRecipients: <as received>\\nSubject: <subject>\\nBody:\\n<body verbatim>"
        Failure:    "Status: Failed\\nError: <description>"
    """
    # Read source at dispatch time via the ContextVar. The outer bedrock_app
    # hook and the inner-agent reseed hook in tools.workflow_agents keep the
    # ContextVar populated across Strands worker-thread boundaries.
    return handle_filtered_email(request_source.get(), task, recipients, context)
```

Key implementation notes:
- Module-level `@tool` — not created per request — so it is not re-decorated on every invocation.
- The name `email_agent` matches the real tool so prompt references ("reply via email_agent") work unchanged.
- Signature and docstring mirror the real tool. A signature-parity test (see Testing) fails the build if they drift.
- `Status: Suppressed` is a new terminal state — see **Core Mechanism → Filtered email tool**. Workflow prompts get a one-line addition instructing them to accept it as a successful terminal state and copy the delivery status into their final response.

### Modified: `bedrock_app.py`

```python
from bedrock_agentcore import BedrockAgentCoreApp
from strands.hooks import BeforeToolCallEvent  # NEW
from agent.strands_agent import create_agent
from config import setup_logging, config, load_system_prompt_by_name
from request_context import request_source  # NEW
from tools import get_workflow_agent_tools
import logging

setup_logging()
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

logger.info("Initializing tools...")
AGENT_TOOLS = get_workflow_agent_tools()
logger.info(f"Initialized {len(AGENT_TOOLS)} agent tools")

# Load the router system prompt once at startup. It is tightly coupled to
# AGENT_TOOLS (classification rules name the workflow tools), so it is
# loaded explicitly here rather than via env-var-driven indirection.
ROUTER_SYSTEM_PROMPT = load_system_prompt_by_name("router")


@app.entrypoint
def production_agent(request):
    session_id = request.get("session_id", "unknown")

    try:
        prompt = request.get("prompt", "")
        if not prompt:
            return {"error": "No prompt provided", "session_id": session_id}

        # Any caller that does not explicitly identify as "email" is treated
        # as non-email (fail-safe for customer-facing email).
        source = request.get("source", "unknown")

        if source == "unknown":
            # A missing `source` in prod is almost always a bug in a caller;
            # raise the signal so it's noticed before becoming a silent regression.
            logger.warning(
                "Request arrived without `source` (session: %s) — user emails will be suppressed",
                session_id,
            )
        else:
            logger.info("Processing request (session: %s, source: %s)", session_id, source)

        agent = create_agent(
            system_prompt=ROUTER_SYSTEM_PROMPT,
            model=config.MODEL_ID,
            additional_tools=AGENT_TOOLS,
            include_retrieve=False,
        )

        # Seed the ContextVar INSIDE the Strands worker thread.
        # `agent()` runs on a ThreadPoolExecutor worker; `request_source.set()` in the
        # entrypoint thread would not propagate (verified against strands-agents 1.13.0).
        # A BeforeToolCallEvent hook fires in the worker thread before any @tool runs,
        # so every workflow @tool observes the correct value via request_source.get().
        def _seed_request_source(_event: BeforeToolCallEvent) -> None:
            request_source.set(source)

        agent.hooks.add_callback(BeforeToolCallEvent, _seed_request_source)

        response = agent(prompt)

        logger.info(f"Request processed (session: {session_id})")
        return {"response": str(response), "session_id": session_id}

    except Exception as e:
        logger.error(f"Error (session: {session_id}): {e}", exc_info=True)
        return {"error": str(e), "session_id": session_id}
```

`AGENT_TOOLS` stays module-level. Workflow agent tool objects are the same across sources; source-awareness happens inside each workflow agent when it resolves the email tool.

The hook closure captures the per-request `source` string, so two concurrent `production_agent` invocations produce two `agent` instances with two different closures — no shared mutable state. This is the invariant integration test #25 pins.

### Modified: `tools/workflow_agents.py`

Add a private helper and use it in the 3 workflows that currently bind `email_agent`.

```python
def _get_email_tool():
    """Return the appropriate email tool based on request source.

    The filtered tool keeps the name `email_agent`, so workflow prompts
    (which reference the tool by name) work unchanged.

    Default: any value other than "email" → filtered tool (fail-safe).
    """
    from request_context import request_source

    if request_source.get() == "email":
        from tools.subagents import email_agent
        return email_agent

    from tools.email_filter import email_agent as filtered_email_agent
    return filtered_email_agent


def _reseed_source_on_inner_agent(inner_agent, source: str) -> None:
    """Register a BeforeToolCallEvent hook on a workflow's inner Agent so
    request_source is visible inside the inner ThreadPoolExecutor worker.

    ``source`` must be captured in the OUTER worker (via request_source.get()
    immediately after _get_email_tool resolves the right tool) and closed
    over here. Reading request_source.get() inside the hook body would be
    too late — by then we are already on the inner worker with a fresh
    (default-valued) context.
    """
    from strands.hooks import BeforeToolCallEvent

    def _seed(_event: BeforeToolCallEvent) -> None:
        request_source.set(source)

    inner_agent.hooks.add_callback(BeforeToolCallEvent, _seed)
```

Apply both to each of the three workflows that create an inner `Agent` with an email tool:
- `bug_workflow_agent` — tools list: `[retrieve, github_agent, email_agent, product_lookup]`
- `feature_workflow_agent` — tools list: `[github_agent, email_agent]`
- `mlops_workflow_agent` — tools list: `[github_agent, email_agent]`

Call pattern inside each workflow:

```python
source = request_source.get()         # capture in outer worker
email_agent = _get_email_tool()       # resolved once, module-level object
workflow_agent = Agent(tools=[..., email_agent, ...], ...)
_reseed_source_on_inner_agent(workflow_agent, source)  # seed inner worker
```

`tools/__init__.py` is NOT changed. It currently re-exports only the three `get_*_tools()` factory functions used by `bedrock_app.py` (not individual tools). The filtered `email_agent` is reached via the qualified `tools.email_filter` import inside `_get_email_tool()`, so the contributor guide's "Export via `tools/__init__.py`" rule in `.claude/rules/agent-development.md` does not apply here — that rule is for tools that get wired into `Agent(tools=[...])` lists at module import time, which the filtered tool is not.

Example (bug workflow):

```python
# Before
from tools.subagents import email_agent, github_agent
...
workflow_agent = Agent(
    tools=[retrieve, github_agent, email_agent, product_lookup],
    system_prompt=system_prompt,
    model=model_id,
)

# After
from tools.subagents import github_agent
email_agent = _get_email_tool()
...
workflow_agent = Agent(
    tools=[retrieve, github_agent, email_agent, product_lookup],
    system_prompt=system_prompt,
    model=model_id,
)
```

`question_workflow_agent` is not touched — it has no email tool.

`tools/ops_notification.py` (née `tools/escalation.py`) is not touched — it imports the real `email_agent` directly from `tools.subagents`, so ops notifications always reach SES regardless of source.

## Prompt Changes

Three workflow prompts get three small additions:
1. A one-line relaxation so web submissions without a sender email don't hard-stop.
2. An `IMPORTANT — email_agent return values` block (the rule: what each `Status:` means and what to do with it).
3. A `Suppressed:` row added to the `Response Format` list (the template: the two-section shape to emit).

The block and the template are complementary — the block tells the model *what the return state means*, the template gives it the *exact shape to emit*. Under long-context pressure the LLM pattern-matches the Response Format and can ignore the IMPORTANT block, which is why both ship together.

The rest of every workflow prompt stays identical — the prompts still say "reply via email_agent"; the code layer decides whether that reaches SES, and whether the body becomes an email or a chat reply.

### Shared addition — `Status: Suppressed` handling

Add the following block to each of the three workflow prompts under the existing "Use the email_agent tool to ..." step. (Exact wording may vary by workflow; the semantics must match.)

```
IMPORTANT — email_agent return values:
- "Status: Success ..."    → reply was sent via SES. Report it in the final response
  using the Success template in Response Format.
- "Status: Suppressed ..." → reply was intentionally NOT delivered via email. The
  "Body:" section of the tool output IS the reply the user will see in-app (web
  submissions render it in chat; unknown-source requests are suppressed as a safety
  default and logged for ops).
  This is a successful terminal outcome. Do NOT retry, do NOT call escalation,
  do NOT paraphrase or summarize the Body.
  In your final response, COPY the entire "Body:" section VERBATIM (preserving
  line breaks and formatting) as the primary response, then append the metadata
  footer shown in the Suppressed template in Response Format.
  Do NOT include a "Reply sent to <address>" phrase.
- "Status: Failed ..."     → treat as failure per the normal failure path.
```

### Shared addition — Response Format must enumerate the `Suppressed` variant

The `Suppressed` template is **two sections**, not a single line:

1. The `Body:` section from the `email_agent` tool output, copied verbatim (preserves line breaks).
2. One blank line, then `---`, then a single-line metadata footer.

A single-line template would lose the body and collapse the user-visible response to metadata.

#### `config/prompts/workflows/bug_workflow.txt` — Response Format

```diff
 Response Format:
 - Success: "Issue created: [URL] | Reply sent to: [sender] | Product: [name] | Type: [Firmware/UI]"
+- Suppressed: use the Body section from the email_agent "Status: Suppressed" output VERBATIM
+  (preserving line breaks) as the primary response, then ONE blank line, then "---", then
+  this single-line metadata footer:
+
+      Issue: [URL] | Product: [name] | Type: [Firmware/UI] | Channel: [channel]
+
+  Do NOT paraphrase, truncate, or re-format the Body. Do NOT include "Reply sent to".
 - Duplicate: "Comment added to existing issue: [URL] | Reply sent to: [sender] | Product: [name] | Notified: [email]"
+- Duplicate (suppressed): same two-section shape as Suppressed; footer is
+      "Comment: [URL] | Product: [name] | Notified: [email] | Channel: [channel]"
 - Not Found: "Issue created: [URL] | Reply sent to: [sender] | Product: Unknown ([query]) | Routed to: [repo]"
 - Partial: "Issue created: [URL] | Email failed: [error]"
 - Error: "ERROR: [details] | Notification sent to: [sender] | Escalated to: [email]"
```

#### `config/prompts/workflows/feature_workflow.txt` — Response Format

```diff
 Response Format:
 - Success: "Issue created: [URL] | Reply sent to: [sender] | Repo: linksys/PRODUCT_MANAGEMENT"
+- Suppressed: use the Body section from the email_agent "Status: Suppressed" output VERBATIM
+  (preserving line breaks) as the primary response, then ONE blank line, then "---", then
+  this single-line metadata footer:
+
+      Issue: [URL] | Repo: linksys/PRODUCT_MANAGEMENT | Channel: [channel]
+
+  Do NOT paraphrase, truncate, or re-format the Body. Do NOT include "Reply sent to".
 - Duplicate: "Comment added to existing issue: [URL] | Reply sent to: [sender] | Notified: [email]"
+- Duplicate (suppressed): same two-section shape as Suppressed; footer is
+      "Comment: [URL] | Notified: [email] | Channel: [channel]"
 - Partial: "Issue created: [URL] | Email failed: [error]"
 - Error: "ERROR: [details] | Notification sent to: [sender] | Escalated to: [email]"
```

#### `config/prompts/workflows/mlops_workflow.txt` — Response Format

```diff
 Response Format:
 - Success: "Issue created: [URL] | Reply sent to: [sender]"
+- Suppressed: use the Body section from the email_agent "Status: Suppressed" output VERBATIM
+  (preserving line breaks) as the primary response, then ONE blank line, then "---", then
+  this single-line metadata footer:
+
+      Issue: [URL] | Channel: [channel]
+
+  Do NOT paraphrase, truncate, or re-format the Body. Do NOT include "Reply sent to".
 - Partial: "Issue created: [URL] | Email failed: [error]"
 - Error: "ERROR: [details] | Notification sent to: [sender]"
```

The `[channel]` placeholder mirrors the `Channel:` field in the `Status: Suppressed` return value — the LLM is instructed to copy it verbatim. Integration tests enforce both halves of the contract:

- Test #24: final response for `source=web_ui` does NOT contain `"Reply sent to"` (phantom-send guard).
- Test #22: final response contains the `Channel: web_ui` footer AND a sentinel phrase seeded into the body, proving the composed reply reached the user.

### `config/prompts/workflows/bug_workflow.txt` — step 2

```diff
 2. Read the email and validate against the guide. Extract:
-   - Sender email address
+   - Sender email address (if available — web submissions may omit this)
    - Product identifier: model number (e.g., SPNM62, MX4200, M60TB-EU) or SKU (e.g., Pinnacle 2.2, Velop)
    - Bug details: description, steps to reproduce, expected behavior, device/firmware info
-   Hard stop if required fields (per the guide) are missing.
+   Hard stop if required fields (per the guide) are missing. Sender email is optional.
```

### `config/prompts/workflows/feature_workflow.txt` — step 1

```diff
 1. Analyze the email. Extract:
-   - Sender email address (for reply)
+   - Sender email address (for reply, if available)
    - Feature description (the requested capability or integration)
```

### `config/prompts/workflows/mlops_workflow.txt` — step 1

```diff
 1. Analyze the email. Extract:
-   - Sender email address (for reply)
+   - Sender email address (for reply, if available)
    - Subject line and timestamp
```

### Prompts NOT changed

| File | Why |
|---|---|
| `config/prompts/router.txt` | Classifies by content, not by format |
| `config/prompts/workflows/question_workflow.txt` | Already channel-native |
| `config/prompts/laila.txt` | Unchanged |
| `config/prompts/github_agent.txt` | Unchanged |
| `config/prompts/email_agent.txt` | Unchanged — same contract |

## Code Changes — laila-chat (external private repo)

This section is the contract for **the laila-chat frontend and backend engineers**. It covers (a) what the UI sends to the laila-chat backend, (b) what the laila-chat backend sends to AgentCore, (c) what the UI receives and how to render it, and (d) loading/error states.

laila-chat is a **chatbot UI** — a chat stream of user/assistant turns, not a form with submit. The design accommodates this: `subject` is optional, a single chat message maps to a single AgentCore invocation, and the assistant's reply is the full response body (no out-of-band email).

### Turn model

One user chat bubble == one `POST /chat` == one `agentcore invoke` == one assistant chat bubble.

- **No `subject` field is required from the UI.** The user just types a message and hits enter. If the UI later adds a "quick actions" affordance that injects a short title, it can be passed through; for Phase 1 assume `subject` is absent.
- **No multi-turn conversation state yet.** Each invocation is a fresh session (see Open Q3). The UI should NOT try to thread replies into an AgentCore conversation — the backend generates a new `session_id` per request.
- **Streaming is not supported in Phase 1.** The assistant bubble shows a loading indicator until the full response arrives (typically 30–90s for bug reports that hit GitHub MCP; 5–20s for question workflows).

### UI → laila-chat backend (request payload)

The route path is owned by the laila-chat repo and is out of scope for this design. The payload must carry enough information for the backend to build the AgentCore `prompt` string below:

```json
{
  "message": "<user's free-text chat bubble content>",
  "subject": "<optional, usually omitted>"
}
```

| Field | Required | Notes |
|---|---|---|
| `message` | Yes | The user's chat bubble content, verbatim. |
| `subject` | No | Omit unless the UI has an affordance that sets one. Do not send a placeholder like `"User submission"` — the backend omits the `Subject:` line entirely when this is absent, and the workflow LLM will derive a GitHub issue title from the body on its own. |

The authenticated user comes from the session (`request.state.current_user`) — the UI does not send sender identity in the body.

### laila-chat backend → AgentCore (envelope contract)

The backend formats an email-shaped prompt and forwards it. **This is the stable contract AgentCore depends on.** Every field below is required unless marked optional.

```json
{
  "source": "web_ui",
  "prompt": "From: <user.email>\nSubmitted via: Web UI\n\n<body.message>"
}
```

| Envelope field | Required | Value |
|---|---|---|
| `source` | Yes | Literal string `"web_ui"`. Anything else triggers the fail-safe (suppress + WARN log) but still processes the request. |
| `prompt` | Yes | Email-shaped plain text. See formatter below. |

**Prompt body shape — in order, `\n`-separated:**

1. **Sender line** (exactly one of):
   - `From: <user.email>` — when the authenticated user has a real email address.
   - `Submitted by: <user.id> (no email on file)` — when only a GitHub/OAuth handle is available. Don't fake a `From:` address; `bug_workflow`'s extraction heuristics expect `From:` to be a real mailbox.
2. **Subject line** (optional): `Subject: <body.subject>` — **omit the line entirely when the UI didn't provide one**. Do not send `Subject: User submission` as a placeholder; the workflow LLM will derive a title from the body on its own, and a fake subject confuses extraction.
3. **Provenance line:** literal `Submitted via: Web UI`. This string propagates into the GitHub issue body via the workflow's "include context" behavior — it's how triagers know a ticket came from chat vs. email. Do not change the wording.
4. **Blank line.**
5. **User's message verbatim** (`body.message`).

### Reference formatter (`backend/app/routes/agentcore.py`)

```python
# Before
payload = json.dumps({"prompt": body.message}).encode("utf-8")

# After
user = request.state.current_user

# 1. Sender line
if user.email:
    sender_line = f"From: {user.email}"
else:
    sender_line = f"Submitted by: {user.id} (no email on file)"

# 2. Subject line — OMIT when absent (chatbot UI typically has no subject field)
subject_line = f"Subject: {body.subject}\n" if body.subject else ""

# 3-5. Provenance + blank line + message
formatted_prompt = (
    f"{sender_line}\n"
    f"{subject_line}"
    f"Submitted via: Web UI\n"
    f"\n"
    f"{body.message}"
)

payload = json.dumps(
    {"source": "web_ui", "prompt": formatted_prompt}
).encode("utf-8")
```

### AgentCore → laila-chat backend → UI (response contract)

The `agentcore invoke` response is:

```json
{
  "response": "<assistant_message_string>",
  "session_id": "<uuid>"
}
```

`response` is a single plain-text string. For the web_ui channel it is **either**:

- **Two-section Suppressed shape** (bug / feature / mlops workflows that composed a customer-facing reply):

  ```
  <body prose, multi-line, may include markdown>

  ---
  Issue: <url> | Product: <name> | Type: <Firmware/UI> | Channel: web_ui
  ```

- **Single-section** (question workflow, or workflows where no `email_agent` call happened): just prose, no `---` divider.

The backend forwards `response` verbatim to the UI. It does not parse or rewrite it.

### UI rendering — Phase 1 (display as-is, recommended start)

Render the `response` string directly as the assistant's chat bubble. Markdown rendering (headings, bullets, code blocks, links) is expected — the workflow prompts emit GitHub-flavored markdown.

- The `---` divider is literal markdown for a horizontal rule. If the UI's markdown renderer displays it as an `<hr>`, that's the intended visual separation between reply body and metadata footer.
- The footer line contains a GitHub issue URL. The markdown renderer should auto-link it; if it doesn't, wrap URLs manually.
- No frontend string-scrubbing is needed. There is no "Reply sent to" line to hide.

### UI rendering — Phase 2 (optional: split body and footer for richer display)

If the UI wants the footer as a small caption or chip instead of inline text:

```ts
// TypeScript
const [body, footer] = response.split(/\n---\n/, 2);
const issueUrlMatch = (footer ?? "").match(/https:\/\/github\.com\/[^\s|]+/);
const issueUrl = issueUrlMatch?.[0];
```

or equivalently in Python (backend-side if preferred):

```python
import re
parts = re.split(r"\n---\n", response_text, maxsplit=1)
body = parts[0].strip()
footer = parts[1].strip() if len(parts) == 2 else ""
url_match = re.search(r"https://github\.com/[^\s|]+", footer or response_text)
```

Rules:

- Split on **exactly** `\n---\n` (newline, three dashes, newline). A footer that's split off but has no `---` means the workflow didn't produce one (question workflow path) — render the whole string as body, no footer.
- Do not try to parse the footer's pipe-separated key/value pairs structurally. The footer shape varies by workflow (`Issue:`, `Comment:`, different fields). If you want a "View issue" button, regex for the GitHub URL and stop there.
- Never strip or rewrite the body content. It is the LLM's actual reply to the user.

### UI states

| State | Trigger | UI treatment |
|---|---|---|
| **Loading** | `POST /chat` in flight | Disable input; show typing indicator on the assistant bubble. Expect 5–90s latency; set client timeout to at least 5 minutes to match the AgentCore side. |
| **Success** | `response` field present, non-empty | Render as assistant bubble (Phase 1) or body+footer (Phase 2). |
| **Error — agent** | Response JSON has `"error"` key (see `bedrock_app.py:582-584`) | Show a generic "Something went wrong — please try again" message. Do NOT show the raw `error` string to the user (it may include stack trace fragments). Log the `session_id` client-side so support can correlate with CloudWatch. |
| **Error — network/timeout** | HTTP error from `/chat` | Standard chat UX: offer a "retry" affordance that re-sends the same message. |
| **Empty response** | `response` is an empty string | Treat as error. Should not happen in practice but guard against it. |

### Example round trip

**User types in chat:** `"My SPNM62 crashes after firmware 1.2.3 update. Happens every time I reboot."`

**UI → laila-chat:**

```json
POST /chat
{ "message": "My SPNM62 crashes after firmware 1.2.3 update. Happens every time I reboot." }
```

**laila-chat → AgentCore:**

```json
{
  "source": "web_ui",
  "prompt": "From: alex@example.com\nSubmitted via: Web UI\n\nMy SPNM62 crashes after firmware 1.2.3 update. Happens every time I reboot."
}
```

**AgentCore → laila-chat:**

```json
{
  "response": "Hi Alex! Thanks for reporting the crash on your SPNM62 after the firmware 1.2.3 update.\n\nI've filed this as an issue so our firmware team can investigate. In the meantime, you can try:\n\n1. Power-cycling the router (hold reset for 10 seconds).\n2. Downgrading to firmware 1.2.2 if the crash is blocking you.\n\nWe'll follow up once the team has a fix.\n\n---\nIssue: https://github.com/linksys/LinksysWRT/issues/259 | Product: SPNM62 | Type: Firmware | Channel: web_ui",
  "session_id": "e0555dfe-972b-4b64-94fe-c7b917ec141e"
}
```

**UI renders** (Phase 1): the full response string as markdown in the assistant bubble. The `---` becomes a horizontal rule; the issue URL auto-links.

### What the UI MUST NOT do

- **Do not insert a `Subject:` placeholder** when the user didn't provide one. Omit the line. A fake subject distorts the workflow LLM's extraction.
- **Do not send `source` values other than `"web_ui"`.** Anything else triggers the fail-safe suppress + a WARN log. If you add a new channel later, coordinate a new `source` value with the agent team (e.g. `"mobile_app"`).
- **Do not attempt to parse or rewrite the reply body.** The workflow prompt is responsible for the shape; the UI is a passive renderer.
- **Do not try to re-invoke with `"source": "email"`** to "force an email reply" from a chatbot context — that would send a real SES email to the user's address. Email confirmation from chat is an explicit deferred feature (Open Q4).
- **Do not expose raw `error` strings to end users.** They may leak internal detail.

## Code Changes — Email Handler Lambda

The email handler lives in this repo at `src/lambda/email-handler/`, so the agent + Lambda changes land in a single PR and are deployed in the same release window (two separate `bin/deploy.sh` runs — agent first, then Lambda; see Migration → Phase 1 for the rationale). No cross-repo coordination with laila-chat is required for the email path.

### `src/lambda/email-handler/src/integrations/agentcore_invocation.py`

Add `"source": "email"` to the AgentCore payload so the agent does not fall back to the fail-safe default.

```python
# Before (line ~200)
payload = json.dumps({"prompt": prompt})

# After
payload = json.dumps({"source": "email", "prompt": prompt})
```

This is the only change in the Lambda. All email parsing, S3 fetching, attachment upload, prompt formatting, SQS handling, and invocation wiring stay as-is. The change stays inside the `integrations` layer per the Lambda's four-layer architecture.

A single unit test pins the envelope shape so a future refactor cannot silently drop the source field:

```python
# tests/test_integrations_agentcore.py
import json

from integrations.agentcore_invocation import invoke_agent


def test_invoke_agent_sends_source_email(monkeypatch):
    captured = {}

    def fake_invoke(**kwargs):
        captured["payload"] = kwargs["payload"]
        return {
            "response": type(
                "S", (), {"read": lambda self: b'{"response":"ok"}'}
            )()
        }

    # Patch the module-level bedrock_client's method on the module under test.
    monkeypatch.setattr(
        "integrations.agentcore_invocation.bedrock_client.invoke_agent_runtime",
        fake_invoke,
    )

    invoke_agent("hello")
    envelope = json.loads(captured["payload"])
    assert envelope["source"] == "email"
    # Out-of-order-deploy guard: the old agent reads "prompt"; this field
    # must never be dropped when adding new envelope keys.
    assert envelope["prompt"] == "hello"
```

## Concurrency Safety

`ContextVar` is thread-safe and asyncio-aware by design:
- Threads: each thread gets its own value
- Asyncio tasks: each task inherits a snapshot at creation

**However, the Strands SDK does NOT auto-propagate the caller's context across its `ThreadPoolExecutor` boundary.** `Agent.__call__` wraps `asyncio.run(invoke_async(...))` in a fresh executor worker thread, and `ThreadPoolExecutor.submit` does not copy `contextvars.Context` into the worker by default. A `copy_context().run(agent, prompt)` wrapper in the entrypoint also does not rescue it (verified against strands-agents 1.13.0 with a spike).

That is why this design seeds the ContextVar from a `BeforeToolCallEvent` hook (see `bedrock_app.py` above). The hook body runs inside the worker thread, so the subsequent `request_source.get()` calls inside every workflow `@tool` see the correct value.

Concurrency safety under this approach:
- Two parallel `production_agent` invocations each get their own `agent` instance, their own hook closure (capturing their own `source`), and their own worker thread — no cross-talk.
- Strands `ConcurrentToolExecutor` dispatches parallel tools via `asyncio.create_task` within a single worker thread (see `strands/tools/executors/concurrent.py`). `create_task` snapshots the current `Context`, so once the hook has seeded the worker-thread context, every concurrent child task sees the same value. No additional guarding needed.
- Integration test #25 pins the end-to-end invariant; unit test #19 pins the hook-in-thread propagation.

## Web UI Response Handling

See [Code Changes — laila-chat → UI rendering](#ui-rendering--phase-1-display-as-is-recommended-start) for the full rendering contract (Phase 1 / Phase 2, loading and error states, what the UI must not do).

For reference, an email-path response is unchanged (single-line metadata; the full body was delivered via SES):

```
Issue created: https://github.com/linksys/firmware/issues/123 | Reply sent to: john@co.com | Product: SPNM62 | Type: Firmware
```

The email path never reaches the UI — laila-chat only sees `source=web_ui` responses.

## Migration

### Phase 1 — ship the code-only change (this design)

The email handler Lambda and the agent live in the same repo, so both land in a single PR. Deployment is still two `bin/deploy.sh` invocations — agent first, then Lambda — because AgentCore and Lambda are separate runtimes and there is no atomic release across them. Step 2 below pins the order and explains why the reverse order is also safe (see the "behaves identically" note after the deploy sequence).

1. **Single PR in TOOL_AIOPS_FEEDBACK_COLLECTION** containing:
   - Agent: `src/agent/request_context.py`, `src/agent/tools/email_filter.py`, `src/agent/tools/workflow_agents.py`, `src/agent/bedrock_app.py`, 3 prompt tweaks.
   - Lambda: `src/lambda/email-handler/src/integrations/agentcore_invocation.py` (payload gets `"source": "email"`).
2. **Deploy sequence** (same release window, order matters — agent first):
   - Agent: from `src/agent/`, run `./bin/deploy.sh dev`. Must land before the Lambda so the agent is already tolerating the new `source` field when the Lambda starts sending it.
   - Email handler: from `src/lambda/email-handler/`, run `bin/deploy.sh dev`.
   - Verify with a real email + `agentcore invoke '{"source": "web_ui", ...}'`.
   - Promote to prod by repeating both with `prod`.
3. **Deploy laila-chat** (external repo) with `"source": "web_ui"` and formatted prompt. This is always safe to deploy independently — sending `"web_ui"` can't over-suppress an email that never existed.

Phase-1 risk is low: the email path behaves identically (explicit `"source": "email"` in the Lambda), and the new code paths only activate for non-email sources. The agent ignores unknown envelope fields, so deploying the Lambda's `"source": "email"` addition before the agent upgrade is also safe — but the reverse order (agent first) is preferred so any laila-chat caller that races the rollout is immediately handled correctly.

### Rollback

Each component rolls back independently; no data migrations, no schema changes.

| Symptom | Rollback action | Scope |
|---|---|---|
| Agent misbehaves on `source=web_ui` (e.g. filter bug, wrong tool resolved) | Re-deploy previous agent image: `cd src/agent && ./bin/deploy.sh <env>` from the prior commit. Web submissions revert to "broken" (pre-Phase-1 state) but email path is unaffected. | Agent only |
| Email path regresses (e.g. real emails being suppressed) | Re-deploy previous Lambda: `cd src/lambda/email-handler && bin/deploy.sh <env>` from the prior commit. The old envelope lacks `"source"` so the agent's fail-safe default would still suppress user replies — therefore also roll back the agent to restore the pre-Phase-1 "default-to-email" behavior. **Roll both back together.** | Agent + Lambda |
| `Status: Suppressed` breaks a workflow prompt (LLM retries or escalates anyway) | Hotfix the three workflow prompts via `src/lambda/email-handler/bin/update-prompts.sh <env>` if those prompts are served from S3, or re-deploy the agent with reverted prompt files. No Lambda change needed. | Prompts only |
| laila-chat needs to be pulled | Revert the laila-chat deploy. Agent continues to accept `"web_ui"` if any stragglers arrive; filter continues to suppress — harmless. | laila-chat only |

Do not rely on toggling an env var to disable the filter: there is no kill switch by design (a kill switch that re-enables user emails on web submissions is the outage we are preventing). Revert via redeploy.

### Why keep the fail-safe default even though we own the Lambda?

Defaulting missing-source to "suppress" costs us nothing (the real email path is explicit) and protects against:
- Manual `agentcore invoke '{"prompt": "..."}'` from the CLI or Jenkins that forgets `source`.
- Future handlers (Slack webhook, internal API, batch script) written by someone who didn't read this doc.
- Partial environment rollouts where an older handler talks to a newer agent.

The alternative — default to `"email"` — trades that safety for zero operational benefit, since the legitimate email handler always sets `"source": "email"` explicitly.

### Phase 2 — observe

Once web submissions are live, monitor:
- Router classification accuracy on plain-text web prompts.
- Extraction quality in workflow agents (product, description).
- GitHub issue quality vs email-originated issues.

**Instrumentation required in Phase 1 so Phase 2 is data-driven, not anecdotal.** Emit one structured log line per request at the end of `production_agent` with these fields:

| Field | Type | Derivation |
|---|---|---|
| `session_id` | str | `request.get("session_id", "unknown")` |
| `source` | str | `request_source.get()` |
| `classified_intent` | str | Name of the workflow tool the router invoked (captured via a thin wrapper on each `@tool` in `tools/workflow_agents.py`, or parsed from the agent trace). |
| `email_tool_outcome` | enum: `sent` \| `suppressed` \| `failed` \| `not_invoked` | First token after `Status:` in `email_agent`'s last return value; `not_invoked` if the tool was never called. |
| `issue_created` | bool | Regex match `https://github\.com/[^/]+/[^/]+/issues/\d+` in the final response. |

These fields land in CloudWatch Logs as a single JSON line and are cheap to query via Logs Insights. Without them, Phase 2 decisions will be based on developer hunches.

If accuracy drops, consider Phase 3. Drive the decision from data, not speculation.

### Phase 3 — minimal structure (only if needed)

Add lightweight inline headers to the web prompt (e.g., `PRODUCT:`, `SUBJECT:`) and teach workflow prompts to prefer them when present. Still no full schema. Still no router rewrite.

### Interaction with OPS_NOTIFICATION_RENAME

Current state (`src/agent/config.py`) exposes only `ESCALATION_EMAIL = os.getenv("ESCALATION_EMAIL", "")`; there is no `OPS_NOTIFICATION_EMAIL` attribute yet. The filter's `_ops_allowlist()` therefore uses `getattr(config, "OPS_NOTIFICATION_EMAIL", None) or getattr(config, "ESCALATION_EMAIL", "")` so it works unchanged against today's config (falling through to `ESCALATION_EMAIL`) AND against the post-rename config (preferring `OPS_NOTIFICATION_EMAIL`). No ordering bomb.

`docs/feature/OPS_NOTIFICATION_RENAME.md` renames `ESCALATION_EMAIL` → `OPS_NOTIFICATION_EMAIL`. Because both PRs touch the ops allow-list surface, the ordering is still called out explicitly below.

**Decision: ship the rename first**, with a dual-read shim in `config.py`:

```python
# config.py (rename PR)
OPS_NOTIFICATION_EMAIL = os.getenv("OPS_NOTIFICATION_EMAIL") or os.getenv("ESCALATION_EMAIL", "")
# Keep the old attribute as an alias for one release so in-flight code isn't broken.
ESCALATION_EMAIL = OPS_NOTIFICATION_EMAIL
```

Then this design's `email_filter.py` reads `config.OPS_NOTIFICATION_EMAIL` (updated from the snippet above, which currently reads `config.ESCALATION_EMAIL`). This avoids touching the filter twice: once to add it, once to rename the constant inside it.

Do not merge both features in the same PR.

## Testing

### Unit tests

**`tools/email_filter` — ops-list passthrough**

1. All recipients on ops allow-list → delegates to `tools.subagents.email_agent` (assert via patch).
2. Single recipient with spaced `cc:` prefix (`"cc: ops@co.com"`), fully allow-listed → passthrough still triggers. **Guards against a parser bug where leading whitespace after the prefix left the address un-normalized.**
3. Single recipient in display-name form (`"Ops Team <ops@co.com>"`), fully allow-listed → passthrough triggers.
4. Ops allow-list configured with display names (`"Ops Team <ops@co.com>; Lead <lead@co.com>"`), recipients are a fully-allow-listed subset → passthrough.
5. Ops allow-list configured as comma-separated list (`"ops@co.com, lead@co.com"`), recipients fully allow-listed → passthrough.
6. **Mixed list (ops + non-ops) — SUPPRESS.** Recipients `"ops@co.com; cc:victim@attacker.com"` with only `ops@co.com` on the allow-list → returns `Status: Suppressed`; real SES is NOT called. **Pins the "all, not any" rule that prevents exfiltration via a crafted recipient string.**
7. Ops allow-list unset / empty → never passthrough.
   (The legacy `ESCALATION_EMAIL` env var is absorbed at the config layer into `config.OPS_NOTIFICATION_EMAIL`, so the filter reads a single canonical attribute — no filter-level fallback test needed.)

**`tools/email_filter` — suppression contract**

9. Recipients contain only user addresses with `source="web_ui"` → returns a `Status: Suppressed` string containing `Channel: web_ui`, the recipients verbatim, and the subject; does NOT call `tools.subagents.email_agent`.
10. Subject extraction handles `"Subject: Hello"` and `"subject:   Hello  "` (case and whitespace variants).
11. Source defaults to `"unknown"` when the ContextVar is not set → suppressed response body contains `Channel: unknown`.

**`tools/email_filter` — body echo (the Phase-1-critical fix)**

These tests pin the "composed reply is preserved and rendered" contract. Without them, a regression that drops the body would silently return the user to a metadata-only response.

11a. **Single-line body** — context `"Subject: Hi\nBody: thanks for reporting"` → return value contains `"Body:\nthanks for reporting"` and ends with the body (no trailing metadata fields).
11b. **Multi-line body, preserves line breaks** — context with `"Body: line1\n\nline2\nline3"` → body section in the return value contains all three lines separated by the original newlines; blank lines preserved.
11c. **Body stops at `ReplyTo:`** — context `"Body: line1\nline2\nReplyTo: x@y.com"` → body section is exactly `"line1\nline2"`; no `ReplyTo` content leaks into the echoed body.
11d. **Body marker absent** — context with only `"Subject: Hi"` (no `Body:`) → return value still well-formed; `Body:` section is present but empty (no crash, no exception).
11e. **Case-insensitive marker** — `"body: hello"` and `"BODY: hello"` are both recognized.
11f. **Size cap** — body > 32 KiB → truncated on a UTF-8 boundary with the `…[truncated]` marker appended; no `UnicodeDecodeError`.
11g. **Multibyte safety** — body contains CJK/emoji characters that straddle the 32 KiB boundary → truncation does not produce invalid UTF-8.

**`tools/email_filter` — signature parity (drift guard)**

12. `inspect.signature(tools.email_filter.email_agent)` equals `inspect.signature(tools.subagents.email_agent)`. **Fails the build if the real tool gains/renames a parameter without the filter being updated.**
13. Docstring `Args:` parity — each parameter name from `inspect.signature(tools.subagents.email_agent)` appears in `tools.email_filter.email_agent.__doc__`. Docstring `Returns:` superset — every `Status: <state>` line in the real tool's docstring also appears in the filter's docstring (the filter may add states like `Suppressed` but must not drop `Success` or `Failed`). **Catches renames/removals; allows additive contract extension.**

**`tools/workflow_agents._get_email_tool`** (fail-safe default)

14. ContextVar set to `"email"` → returns `tools.subagents.email_agent`.
15. ContextVar set to `"web_ui"` → returns `tools.email_filter.email_agent`.
16. ContextVar set to `"unknown"` (default) → returns filtered tool. **Pins the fail-safe default so future refactors cannot flip it silently.**
17. ContextVar set to any unrecognized string (e.g. `"slack"`) → returns filtered tool.

**`request_context.request_source` threading**

18. Hook-seeded value is readable inside a workflow `@tool` during the same `agent(prompt)` call: register a `BeforeToolCallEvent` hook that calls `request_source.set("email")`, invoke a probe `@tool` that asserts `request_source.get() == "email"`. **Pins the hook-in-worker-thread propagation mechanism this design depends on.**
19. Negative control — removing the hook makes the same probe read the default (`"unknown"`). **Pins the regression guard: if a future Strands upgrade changes threading behavior such that the hook becomes unnecessary, this test fails visibly and we can simplify; if Strands keeps the current threadpool behavior, this test stays green and documents WHY the hook exists.**

### Integration tests

20. **Email regression** — Invoke with `{"source": "email", "prompt": ...}` → identical behavior to current system (GitHub issue created + real email reply sent + final response contains `Reply sent to: ...`).
21. **Unset source** — Invoke with `{"prompt": ...}` only → GitHub issue created, no user email sent (fail-safe default), and a WARNING-level log record whose message contains the substring `"Request arrived without"` is emitted by the `bedrock_app` logger (assert via `caplog` at level `logging.WARNING`). **Pins the exact phrase so the bedrock_app log signature is part of the contract.**
22. **Web UI flow — composed body is rendered** — Invoke with `{"source": "web_ui", "prompt": <seeded so the workflow will compose a reply containing the sentinel phrase "XYZZY-REPLY-MARKER">}` → (a) GitHub issue created, (b) no user-facing email sent, (c) the final agent response contains the `XYZZY-REPLY-MARKER` sentinel (proves the body survived suppression), (d) the final response contains `Channel: web_ui` in the metadata footer, (e) the `---` divider separates body from footer. **This is the test that would have caught the "web user sees only a status line" gap.**
23. **Ops passthrough** — Trigger workflow failure on `source=web_ui` → escalation email still reaches ops via SES (real SES call, asserted against SES stub/local).
24. **Truthful-status propagation** — For `source=web_ui`, assert the final agent response does NOT contain the literal `"Reply sent to"`. **Pins the prompt-level fix that prevents phantom send claims.**
24a. **Body is NOT truncated or paraphrased by the prompt** — Seed a body containing a distinctive multi-line passage (3+ sentences with a URL and a numbered list). Assert the final response contains the URL, the numbered list markers, and at least 80% of the seeded character count. **Detects prompt drift where the LLM "summarizes" instead of rendering verbatim.**
25. **Concurrency** — Submit two `production_agent` invocations in parallel with sources `"email"` and `"web_ui"` via `ThreadPoolExecutor`; each workflow's resolved `email_agent` must match its own request's source (no cross-talk). Because each invocation builds its own `agent` with its own `BeforeToolCallEvent` closure, there is no shared state to corrupt — this test guards against accidental regressions (e.g. someone promoting the closure to a module-level hook).
26. **Router classification regression** — Replay a fixed corpus of ≥10 labeled web-style prompts (see `tests/fixtures/router_web_prompts.yml`) through the router and assert classification accuracy against the expected intent. **Detection-only guard for the "router misclassifies plain-text web submissions" risk.**

### Manual verification

From `src/agent/`:

```bash
./bin/deploy.sh dev
```

Then:

```bash
# 1. Email regression (via real email path)
#    Send a test email to the dev inbox. Verify GitHub issue + reply received.

# 2. Web UI path
agentcore invoke '{"source": "web_ui", "prompt": "From: test@co.com\nSubject: Router crashes\nSubmitted via: Web UI\n\nMy SPNM62 crashes after firmware 1.2.3..."}'
#    Verify: issue created, no user-facing email sent, escalation reaches ops on failure.

# 3. Unset source (fail-safe verification)
agentcore invoke '{"prompt": "From: test@co.com\nSubject: ...\n\n..."}'
#    Verify: no user email sent (fail-safe); escalation still works.

# 4. Confirm laila-chat response body exposes the issue URL for the frontend.

# 5. Filter logs
./bin/logs.sh 15m -e dev -f "email_filter"
```

## Risk Analysis

| Risk | Severity | Mitigation |
|---|---|---|
| Lambda + agent deploy out of order within this repo | Low | Two failure modes, each with a concrete guard: **(a) agent new, Lambda old** (`{"prompt"}` without `source`) → fail-safe default suppresses user emails until the Lambda catches up — pinned by integration test #21 and unit test #16; **(b) agent old, Lambda new** (`{"source": "email", "prompt"}`) → old agent does `request.get("prompt", "")` and ignores unknown keys, so behavior is unchanged — pinned by the Lambda envelope-shape unit test that keeps `"prompt"` present alongside `"source"` (see `tests/test_integrations_agentcore.py`). Agent-first deploy order (see Migration) makes (a) the only observable window. |
| Manual `agentcore invoke` forgets `source` | Low | Fail-safe default suppresses the user email; WARN log alerts the next operator; ops escalation still fires |
| LLM hallucinates a "sender" and the filtered tool lets it through | Low | Filter only passes when **every** recipient is on the ops allow-list (normalized `<addr>` + spaced-prefix safe); mixed ops+non-ops lists are suppressed; otherwise always suppresses user emails (test #6) |
| `ESCALATION_EMAIL` rename breaks the filter | Resolved | Rename landed first (`OPS_NOTIFICATION_RENAME`); `config.OPS_NOTIFICATION_EMAIL` absorbs both env var names at the config layer, so the filter reads a single canonical attribute |
| Real `email_agent` signature drifts → filtered tool silently mismatches | Low | Signature-parity test (#12) fails the build on drift |
| LLM parrots "Reply sent to user@x" when email was suppressed | Low | Filter returns `Status: Suppressed` (not `Success`); workflow prompts copy the status verbatim; integration test #24 asserts no phantom-send string in response |
| Composed reply body is lost on web channel (user sees only metadata) | **High impact, mitigated** | Filter echoes the full `Body:` back in the `Status: Suppressed` return; workflow prompts' two-section `Suppressed` template copies the body verbatim into the final response. Integration test #22 seeds a sentinel phrase into the body and asserts it appears in the final response — the test that would have caught this gap in the original design. Test #24a additionally guards against the LLM "summarizing" the body. |
| Body grows unbounded (LLM-generated runaway content blows up agent response envelope) | Low | Filter truncates echoed body at 32 KiB on a UTF-8 boundary with a `…[truncated]` marker. Test #11f pins the cap; test #11g pins multibyte safety. |
| Prompt injection via retrieved content reaching user verbatim through the body echo | Low | Workflow prompts compose the `context` body from LLM synthesis, not raw retrieved text. Ops-notification passthrough is exempt (goes to ops, not users). Flag for review when adding any future workflow that copies external strings (KB docs, GitHub comments) directly into `email_agent(context=...)`. |
| Router misclassifies plain-text web submissions | Medium | **Detection, not prevention.** Phase-1 structured logging (source, intent, outcome, issue_created) surfaces misclassification; Phase-2 prompt headers (deferred) would prevent. Seed-prompt regression test: run a fixed set of ≥10 labeled web-style prompts against the router in CI and assert classification accuracy ≥ threshold — alerts on drift without shipping a prevention mechanism yet. See Phase 3 in Migration for the prevention path if telemetry shows it's needed. |
| Concurrency cross-contamination | Low | Each request builds its own `agent` + per-request `BeforeToolCallEvent` closure (capturing its own `source`); Strands' internal `ConcurrentToolExecutor` uses `asyncio.create_task`, which snapshots context. Tests #18/#19 pin the hook-in-worker-thread mechanism; test #25 pins end-to-end parallel invocations. |
| Strands upgrade changes tool-dispatch threading model | Low | The hook mechanism is documented inline in `bedrock_app.py`; test #19 (hook-removed → default read) will flip to green on an SDK version that propagates context automatically, flagging the hook as now-redundant. Either state is safe — the test will tell us which. |

## Comparison to Prior Proposals

Both prior proposals (`WEB_UI_MULTI_CHANNEL_SUPPORT` and `PROPOSAL_MINIMAL_MULTI_CHANNEL`) agree on the core mechanism (source-aware tool injection via `ContextVar`) and on keeping two new files. This table shows only the rows where this design diverges from one or both of them — for the full enumeration of changes see [Scope](#scope-what-is-and-is-not-changing) and [Code Changes](#code-changes--tool_aiops_feedback_collection).

| Aspect | `WEB_UI_MULTI_CHANNEL_SUPPORT` | `PROPOSAL_MINIMAL_MULTI_CHANNEL` | **This Design** |
|---|---|---|---|
| Suppression return value | Fake `Status: Success` | Fake `Status: Success` | **Truthful `Status: Suppressed`** (3rd terminal state) |
| **Composed reply on web channel** | **Lost** (body discarded) | **Lost** (body discarded) | **Preserved** — body echoed back; prompt renders it verbatim |
| Message format | New unified schema (6 headers) | Email-shaped plain text | Email-shaped plain text |
| Parameter rename | `email` → `message` | None | None |
| Router changes | Channel-neutral rewrite | None | None |
| Unknown-source default | N/A | `"email"` | `"unknown"` → filtered (fail-safe) + WARN log |
| Signature-drift guard | None | None | Unit test pins signature/docstring parity |
| Ops list source | `ESCALATION_EMAIL` | `ESCALATION_EMAIL` | `OPS_NOTIFICATION_EMAIL` with legacy fallback |
| Body-echo integration test | N/A | N/A | Sentinel-phrase assertion in final response (test #22) |
| Phase-2 instrumentation | Implied | None | Structured log line per request (5 fields) |
| Lines changed | ~200+ | ~85 | ~140 |
| Regression risk | Medium | Low | Low |

## Open Questions

### Q1: Should GitHub issues record the source channel?

Yes, implicitly. The web UI formatter inserts `Submitted via: Web UI` into the prompt; that phrase propagates into the issue body via the existing "include relevant context" behavior in the workflow prompts. No prompt change needed. Revisit only if triagers report the signal isn't reaching them reliably.

### Q2: Web UI users without an email (GitHub OAuth only)?

Use `Submitted by: <username> (no email on file)` instead of `From: <username>`. This keeps the `From:` header shape reserved for real email addresses (which the `bug_workflow` prompt expects), avoids confusing the LLM's extraction heuristics, and is purely cosmetic since the filtered tool suppresses replies for web_ui anyway. See the `laila-chat` code snippet above.

### Q3: Sessions for web UI?

Start with a fresh session per request (same as email). Revisit conversational follow-up in a later design — it's orthogonal to multi-channel routing.

### Q4: Web UI opt-in to email confirmations?

Deferred. If required later, add an `email_copy: bool` field that the filter checks before suppressing. Out of scope for Phase 1.

### Q5: Chatbot subject line — omit or synthesize?

**Omit when the UI doesn't provide one.** A chatbot UI typically has no subject field; the user just types a message. The reference formatter conditionally drops the `Subject:` line instead of defaulting to a placeholder like `"User submission"`. The workflow LLM derives a GitHub issue title from the body content on its own (verified in manual test — Test 1 produced `[SPNM62] Router crashes after firmware 1.2.3 update on reboot` from a body-only prompt). Sending a fake subject distorts the bug extraction heuristics.

If a future UI adds an optional "title this conversation" affordance, the formatter already accepts `body.subject` and forwards it.

## Decision

Ship this design in Phase 1. It is the minimal diff that (a) unblocks web UI submissions with an **actual conversational reply** — not just a status line — by echoing the composed body back through the suppressed tool return, (b) keeps the email path behaviorally unchanged once the handler envelope updates, (c) fails safe on customer-facing email, (d) never lies to downstream consumers about email delivery, and (e) fails the build if the filtered tool drifts from the real one or if the composed body is ever dropped from the final response. Defer schema design and prompt rewrites until Phase-2 telemetry (structured per-request logs) shows they're warranted.

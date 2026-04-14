# Proposal: Migrate LAILA Chat to Open-Source WebUI + Bedrock AgentCore

**Author:** Marcus (Staff AI Engineer)
**Date:** 2026-04-14
**Status:** Draft for Review

---

## Executive Summary

We have successfully integrated Amazon Bedrock AgentCore into LAILA Chat as a backend for our AI agent. This integration exposed a fundamental question: **why are we maintaining a custom-built chat platform when AgentCore handles the hard parts, and battle-tested open-source UIs handle the rest?**

This document proposes replacing our custom LAILA Chat frontend and backend with an **open-source AI chat UI** (e.g., Open WebUI or LibreChat) connected to **Bedrock AgentCore agents**. This eliminates ~16 AWS services, ~13 Lambda functions, and thousands of lines of custom infrastructure code -- while gaining features we don't have today (MCP, long-term memory, agent observability, streaming).

---

## 1. The Problem: We Are Maintaining Infrastructure That AgentCore Already Provides

### What We Currently Maintain

LAILA Chat is a production-grade, custom-built platform deployed across **16 AWS services**:

| Component | Count | Purpose |
|-----------|-------|---------|
| Lambda Functions | 13 | API handling, WebSocket, KB sync, Cognito triggers |
| DynamoDB Tables | 3-4 | Conversations, Bots, WebSocket sessions |
| S3 Buckets | 7 | Documents, assets, logs, exports, query results |
| API Gateways | 2 | HTTP REST + WebSocket |
| Step Functions | 1 | Knowledge base synchronization workflow |
| CDK Constructs | 15+ | Custom infrastructure abstractions |
| CodeBuild Projects | 3 | Bot creation, KB builds, API publication |
| Other Services | -- | CloudFront, Cognito, WAF (x3), OpenSearch Serverless, Athena, Glue, KMS |

The backend is **~5,000+ lines of Python** (FastAPI) with 9 route modules, 6 use-case layers, and a custom Bedrock invocation layer supporting 27 models. The frontend is a full **React/TypeScript application** with Zustand state management, XState streaming machines, and i18n for 14+ languages.

### What We Actually Need

An AI chatbot that:
- Connects to our Bedrock AgentCore agent (already built and working)
- Provides a good chat UX (streaming, markdown, file upload)
- Supports tool use visualization
- Has basic auth (SSO/LDAP)
- Is maintainable by a small team

**We have already proven that AgentCore works.** Our integration (completed 2026-04-09, 5/5 E2E tests passing) routes chat through AgentCore Runtime, which handles model invocation, tool orchestration, session management, and memory -- all server-side. The LAILA Chat codebase becomes a passthrough proxy at that point.

---

## 2. What AgentCore Replaces (Things We No Longer Need to Build)

AgentCore is a fully managed agentic platform with **nine integrated services**. Here is what each one replaces from our current stack:

| AgentCore Service | What It Replaces in LAILA Chat | Status |
|---|---|---|
| **Runtime** | Lambda + API Gateway + custom Bedrock invocation (bedrock.py, stream.py) | Already integrated |
| **Memory (Short-term)** | DynamoDB ConversationTableV3 + custom session management code | Available, already configured (`laila_agent_dev_mem`) |
| **Memory (Long-term)** | No equivalent today -- we don't have this | New capability gained |
| **Gateway** | Custom tool orchestration code (agents/tools/*, usecases/chat.py tool loops) | Available |
| **Identity** | Cognito User Pool + custom JWT validation (auth.py) + 2 Cognito trigger Lambdas | Available |
| **Observability** | Custom CloudWatch metrics + Athena/Glue usage analytics pipeline + 2 S3 buckets | Available |
| **Evaluations** | No equivalent today -- we don't have quality scoring | New capability gained |
| **Policy** | IAM policies + WAF rules (partially) | Available |
| **Code Interpreter** | No equivalent today | New capability gained |
| **Browser** | No equivalent today | New capability gained |

### Services We Can Decommission

With AgentCore handling agent logic and an open-source UI handling the frontend:

- **13 Lambda functions** -- AgentCore Runtime replaces the main API Lambda; the WebSocket Lambda is unnecessary if the UI handles streaming natively; KB sync Lambdas are replaced by AgentCore's knowledge base integration
- **3-4 DynamoDB tables** -- AgentCore Memory replaces conversation storage; session management is built-in
- **7 S3 buckets** -- Document storage moves to AgentCore knowledge bases; asset hosting moves to the open-source UI's standard deployment; analytics buckets are replaced by AgentCore Observability
- **2 API Gateways** -- AgentCore Runtime has its own endpoint; the open-source UI connects directly
- **1 Step Functions state machine** -- Knowledge base sync is managed by AgentCore
- **3 CodeBuild projects** -- No custom bot build pipeline needed
- **3 WAF Web ACLs** -- Reduced attack surface means simplified security
- **OpenSearch Serverless** -- AgentCore's knowledge base handles vector search
- **Athena + Glue** -- AgentCore Observability replaces custom usage analytics

---

## 3. Why Lambda + Bedrock Is Inferior to AgentCore for Agent Workloads

### 3.1 MCP (Model Context Protocol) Support

| Capability | Lambda + Bedrock (Current) | AgentCore |
|---|---|---|
| MCP Server connectivity | Not supported. Must implement MCP client from scratch. | Built-in via Gateway service. Connect to any MCP server. |
| Tool discovery | Hard-coded tool registry (agents/tools/*.py) | Semantic search -- agents discover relevant tools automatically |
| Adding new tools | Write Python code, deploy Lambda, update CDK | Register MCP server or API in Gateway. No deploy needed. |

**Why this matters:** MCP is becoming the industry standard for tool integration (adopted by Anthropic, OpenAI, Google, AWS). Our current architecture cannot participate in this ecosystem without significant custom development. AgentCore supports it natively.

### 3.2 Memory

| Capability | Lambda + Bedrock (Current) | AgentCore |
|---|---|---|
| Short-term memory | Custom DynamoDB conversation table + manual context window management | Built-in, managed per session |
| Long-term memory | Not implemented | Built-in. Learns from user interactions, persists across sessions. |
| Cross-session context | User must re-explain context every new conversation | Agent remembers user preferences, past interactions, learned facts |

**Why this matters:** Long-term memory is one of the most requested features for enterprise AI assistants. Building it ourselves would require: a vector database for memory embeddings, retrieval logic, relevance scoring, memory consolidation, and storage management. AgentCore provides this as a managed service.

### 3.3 Session Management

| Capability | Lambda + Bedrock (Current) | AgentCore |
|---|---|---|
| Session isolation | Custom code: WebSocketSessionTable + session ID generation + DynamoDB TTL | Built-in: complete session isolation, 15-min idle timeout, 8-hr max |
| Session duration | Limited by Lambda timeout (15 min max) | Up to 8 hours for long-running tasks |
| Concurrent sessions | Manual concurrency control via DynamoDB distributed locks | Managed scaling |

### 3.4 Observability

| Capability | Lambda + Bedrock (Current) | AgentCore |
|---|---|---|
| Token tracking | Custom pricing dict in bedrock.py, manual cost calculation | Built-in token usage tracking |
| Quality metrics | None | Correctness, helpfulness, safety, goal success rate |
| Latency monitoring | Generic CloudWatch Lambda metrics | Agent-specific latency dashboards |
| Audit trail | CloudWatch logs (unstructured) | Structured audit trails for agent decisions |
| Integration | Custom Athena pipeline (3 services: Athena + Glue + S3) | OpenTelemetry compatible, works with existing observability stack |

### 3.5 Scaling and Operations

| Capability | Lambda + Bedrock (Current) | AgentCore |
|---|---|---|
| Cold starts | Lambda cold starts affect latency (Python + FastAPI + boto3 = ~2-5s) | Managed warm pool |
| Scaling | Must configure Lambda concurrency limits, API Gateway throttling | Automatic scaling |
| Deployment | CDK deploy across 16 services, ~30min deploy cycle | Agent code/container push |
| Rollback | CloudFormation rollback (slow, sometimes fails) | Container versioning |

---

## 4. Open-Source Chat UIs Are Better Than Our Custom Frontend

We maintain a custom React frontend with ~15,000+ lines of TypeScript. Open-source alternatives provide **more features with zero maintenance burden**:

### Feature Comparison

| Feature | LAILA Chat (Custom) | Open WebUI | LibreChat |
|---|---|---|---|
| **GitHub Stars / Community** | Internal only | ~60,000+ | ~35,600+ |
| **Contributors** | Our team (2-3 people) | 500+ | 200+ |
| **Streaming** | Yes (custom WebSocket + XState) | Yes (native SSE) | Yes (resumable streams) |
| **Tool use display** | Basic | Yes (function calling workspace) | Yes (step-by-step display) |
| **File upload** | Yes | Yes (docs, images, code) | Yes (images, text, code) |
| **MCP support** | No | Yes (native Streamable HTTP) | Yes (YAML config per agent) |
| **RAG / Knowledge base** | Yes (custom, via Bedrock KB) | Yes (9 vector DBs, hybrid search) | Yes (file search, semantic) |
| **Voice (STT/TTS)** | No | Yes (multiple providers) | Yes |
| **Image generation** | No | Yes (DALL-E, ComfyUI, SD) | Yes (DALL-E, SD, Flux) |
| **Auth (SSO/LDAP)** | Cognito only | SSO, LDAP, SCIM 2.0 | OAuth2, LDAP |
| **Multi-model side-by-side** | No | Yes | Yes |
| **Agent builder (no-code)** | No | Yes | Yes (drag-and-drop) |
| **Agent chaining** | No | No | Yes (mixture of agents) |
| **Channels / Collaboration** | No | Yes (real-time, @model tagging) | No |
| **Desktop app** | No | Yes | No |
| **i18n** | Yes (14 languages) | Yes (35+ languages) | Yes (20+ languages) |
| **Self-hosting** | CDK (complex) | Docker (simple) | Docker (simple) |
| **Mobile responsive** | Yes | Yes | Yes |

### How Open-Source UIs Connect to AgentCore

All major open-source chat UIs support the **OpenAI-compatible API** pattern (`/v1/chat/completions`). The integration path:

1. Write a thin adapter service (or use **LiteLLM**) that exposes the OpenAI API format
2. The adapter translates requests to AgentCore `InvokeAgentRuntime` calls
3. The adapter streams AgentCore responses back as SSE events
4. The open-source UI handles rendering, tool use display, file handling, auth, etc.

This adapter is ~200 lines of code vs our current ~5,000+ line backend.

---

## 5. Cost Analysis

### Current LAILA Chat Infrastructure Costs (Monthly Estimates)

| Service | Estimated Monthly Cost | Notes |
|---|---|---|
| Lambda (13 functions) | $50-200 | Depends on invocation volume |
| API Gateway (2) | $30-100 | HTTP + WebSocket |
| DynamoDB (3-4 tables) | $50-300 | On-demand, depends on read/write volume |
| S3 (7 buckets) | $10-50 | Storage + transfer |
| CloudFront | $20-100 | CDN distribution |
| Cognito | $0-50 | Per-user pricing |
| WAF (3 ACLs) | $15-45 | $5/ACL + per-request |
| OpenSearch Serverless | $350-700+ | Minimum 2 OCUs for collection |
| Step Functions | $5-25 | Per state transition |
| CodeBuild | $5-20 | Per build minute |
| CloudWatch + Athena | $20-50 | Logs + queries |
| **Bedrock model tokens** | **$500-5,000+** | Main cost driver, unchanged |
| **Total Infrastructure** | **$555-1,640+** | Excluding Bedrock tokens |

### Proposed Architecture Costs (Monthly Estimates)

| Service | Estimated Monthly Cost | Notes |
|---|---|---|
| AgentCore Runtime | $50-300 | Per-second CPU+memory billing |
| AgentCore Memory | $10-50 | Per event + per record/day |
| AgentCore Gateway | $5-20 | Per MCP operation |
| AgentCore Observability | Included with CloudWatch | Standard CloudWatch pricing |
| Open-source UI hosting | $20-50 | Single Docker container (ECS Fargate or EC2) |
| Thin adapter service | $10-30 | Small Lambda or container |
| **Bedrock model tokens** | **$500-5,000+** | Main cost driver, unchanged |
| **Total Infrastructure** | **$95-450** | Excluding Bedrock tokens |

**Estimated infrastructure savings: 60-75%** (excluding model token costs, which are identical in both architectures).

> Note: AgentCore pricing is consumption-based. Exact costs depend on usage patterns. The above are directional estimates. OpenSearch Serverless alone ($350+/mo minimum) likely exceeds the entire AgentCore bill for moderate workloads.

---

## 6. Engineering Effort and Maintenance Burden

### Current Maintenance Requirements

| Area | Effort | Frequency |
|---|---|---|
| CDK infrastructure updates | 2-4 hours | Monthly (AWS SDK updates, security patches) |
| Python backend maintenance | 4-8 hours | Monthly (dependency updates, bug fixes) |
| React frontend maintenance | 4-8 hours | Monthly (dependency updates, browser compat) |
| Bedrock model onboarding | 2-4 hours | Per new model (update config, pricing, generation params) |
| Knowledge base pipeline | 2-4 hours | Per issue (Step Functions debugging, OpenSearch tuning) |
| Security patching | 2-4 hours | Monthly (Lambda runtimes, npm audit, pip audit) |
| **Total** | **~16-32 hours/month** | Ongoing |

### Proposed Maintenance Requirements

| Area | Effort | Frequency |
|---|---|---|
| Open-source UI updates | 1-2 hours | Monthly (docker pull + restart) |
| Thin adapter service | 1-2 hours | Quarterly (minimal surface area) |
| AgentCore agent updates | 2-4 hours | As needed (agent logic changes) |
| **Total** | **~4-8 hours/month** | Ongoing |

**Estimated maintenance reduction: 60-75%**

---

## 7. Feature Velocity: What We Gain Immediately

By switching to open-source UI + AgentCore, we gain these features **without writing any code**:

### From AgentCore
- Long-term memory (cross-session user context)
- MCP tool ecosystem (connect to any MCP server)
- Agent quality evaluations (correctness, helpfulness, safety scores)
- Policy engine (natural language guardrails)
- Code interpreter (agent can execute code in sandbox)
- Browser automation (agent can navigate websites)
- Up to 8-hour agent sessions (vs 15-min Lambda timeout)

### From Open-Source UI
- Voice input/output (STT + TTS)
- Image generation
- Multi-model side-by-side comparison
- No-code agent builder
- Real-time collaboration channels
- Desktop application
- 35+ language support (vs our 14)
- Plugin/extension ecosystem
- Community-driven bug fixes and features

**Building even a subset of these features in LAILA Chat would take months of engineering effort.**

---

## 8. Risk Assessment

### Risks of Migrating

| Risk | Severity | Mitigation |
|---|---|---|
| AgentCore is a newer service (launched 2025) | Medium | We've already validated it works. AWS is investing heavily (9 services, 9 regions). Worst case: fall back to direct Bedrock API calls, which open-source UIs also support natively. |
| Open-source UI may not match exact LAILA UX | Low | Open WebUI and LibreChat exceed our current feature set. Minor UX differences are a trade-off for eliminating maintenance. |
| Migration effort | Medium | Can be done incrementally -- run both systems in parallel during transition. |
| AgentCore pricing uncertainty | Low | Consumption-based with no minimums. Start small, measure, scale. Our current OpenSearch Serverless costs alone ($350+/mo) likely exceed AgentCore. |
| Vendor lock-in to AgentCore | Low | AgentCore is framework-agnostic. Agent code runs in standard Python containers. If we leave AgentCore, we keep the agent logic. |

### Risks of NOT Migrating

| Risk | Severity | Impact |
|---|---|---|
| Increasing maintenance burden | High | Every new feature requires changes across CDK + Python + React + DynamoDB. Team bandwidth consumed by infrastructure instead of agent capabilities. |
| Falling behind on MCP ecosystem | High | MCP is becoming the standard for AI tool integration. Our current architecture cannot adopt it without significant custom work. |
| No long-term memory | Medium | Users repeatedly re-explain context. Competitors offer persistent memory. |
| No agent quality metrics | Medium | We cannot measure or improve agent performance systematically. |
| Security surface area | Medium | 16 AWS services = 16 potential attack vectors to monitor and patch. |
| Bus factor | High | Custom codebase knowledge concentrated in 2-3 engineers. Open-source UI has community support. |

---

## 9. Migration Path

### Phase 1: Proof of Concept (1-2 weeks)
- Deploy Open WebUI or LibreChat via Docker
- Build thin OpenAI-compatible adapter for AgentCore
- Validate: streaming, tool use display, file upload, auth
- Side-by-side comparison with current LAILA Chat

### Phase 2: Feature Parity (2-3 weeks)
- Configure SSO/LDAP integration
- Set up AgentCore Memory (short-term + long-term)
- Connect MCP tools via AgentCore Gateway
- Migrate knowledge bases to AgentCore
- Enable AgentCore Observability

### Phase 3: Cutover (1 week)
- Parallel run period for validation
- DNS/routing switch
- Decommission LAILA Chat infrastructure

### Phase 4: Decommission (1-2 weeks)
- Remove CDK stacks (13 Lambdas, 3-4 DynamoDB tables, 7 S3 buckets, etc.)
- Archive LAILA Chat codebase
- Document new architecture

**Total estimated effort: 5-8 weeks** for a full migration, after which ongoing maintenance drops by 60-75%.

---

## 10. Recommendation

**Stop investing in LAILA Chat's custom infrastructure. Migrate to Open-Source UI + AgentCore.**

The evidence is clear:

1. **We already proved AgentCore works** -- it's integrated, tested, and running.
2. **Our custom backend is now a passthrough proxy** -- AgentCore handles model invocation, tool orchestration, session management, and memory.
3. **Open-source UIs offer more features than our custom frontend** -- voice, image gen, MCP, collaboration, no-code agents -- all maintained by thousands of contributors.
4. **We can reduce infrastructure from 16 AWS services to 3-4** -- cutting costs by 60-75% and maintenance by a similar margin.
5. **We gain capabilities we cannot build ourselves** -- long-term memory, MCP ecosystem, agent evaluations, policy engine, code interpreter, browser automation.
6. **The migration is low-risk** -- incremental, reversible, and builds on proven technology.

The question is not whether AgentCore is ready. We've already answered that. The question is: **why are we spending engineering hours maintaining infrastructure that adds no value on top of what AgentCore and open-source communities already provide?**

---

## Appendix A: Architecture Comparison

### Current Architecture
```
User -> CloudFront -> React App (custom)
                          |
                     API Gateway (HTTP + WebSocket)
                          |
                     Lambda (FastAPI, 5000+ lines)
                          |
          +---------------+---------------+
          |               |               |
     DynamoDB (3-4)   S3 (7)    Bedrock Converse API
          |                         |
     OpenSearch              Step Functions
     Serverless                    |
          |                   CodeBuild (3)
     Athena + Glue
          |
     CloudWatch

Total: ~16 AWS services, 13 Lambdas, 33+ resources
```

### Proposed Architecture
```
User -> Open WebUI / LibreChat (Docker container)
                    |
              Thin Adapter (~200 lines)
                    |
            AgentCore Runtime
                    |
        +-----------+-----------+
        |           |           |
    Memory      Gateway     Observability
  (short+long)  (MCP tools)  (dashboards)
        |
    Bedrock Models

Total: 3-4 AWS services, 0 Lambdas, ~5 resources
```

### Appendix B: Open-Source UI Recommendation

For our use case, **Open WebUI** or **LibreChat** are the strongest candidates:

- **Open WebUI**: Best if we want the most feature-rich platform (voice, image gen, channels, desktop app, 60k+ GitHub stars, massive community).
- **LibreChat**: Best if agent orchestration is the priority (agent chaining, per-agent MCP tool config, no-code agent builder).

Both support OpenAI-compatible backends, SSO/LDAP, file upload, streaming, tool use display, and self-hosted Docker deployment.

A POC with both UIs (1-2 days each) would allow us to make a data-driven choice.

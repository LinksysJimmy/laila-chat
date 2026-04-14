# AWS Infrastructure & CDK Reference

> Generated from CDK source analysis on 2026-04-14.

## Deployment Model

The backend is deployed on **AWS Lambda** (Python 3.13). It runs as a FastAPI application packaged as a Docker container image, served via Lambda Web Adapter. Separate Lambda functions handle HTTP API requests and WebSocket connections.

---

## CDK Stacks

| Stack | Region | Purpose |
|-------|--------|---------|
| **BedrockChatStack** | Default region | Main application stack (API, auth, DB, frontend, etc.) |
| **FrontendWafStack** | us-east-1 | CloudFront WAF with IP allowlists |
| **BedrockRegionResourcesStack** | Bedrock region | S3 bucket for Knowledge Base documents |
| **BedrockCustomBotStack** | Dynamic | Per-bot knowledge base infrastructure (deployed by CodeBuild) |
| **BedrockSharedKnowledgeBasesStack** | Dynamic | Shared/multi-tenant knowledge bases (deployed by CodeBuild) |
| **ApiPublishmentStack** | Dynamic | Published bot APIs for external consumption (deployed by CodeBuild) |

### CDK Entry Points

| File | Purpose |
|------|---------|
| `cdk/bin/bedrock-chat.ts` | Main stack orchestrator |
| `cdk/bin/bedrock-custom-bot.ts` | Custom bot / knowledge base deployment |
| `cdk/bin/bedrock-shared-knowledge-bases.ts` | Shared knowledge bases deployment |
| `cdk/bin/api-publish.ts` | Published API deployment |

### CDK Constructs

| Construct | Purpose | Key Resources |
|-----------|---------|---------------|
| `api.ts` | Backend HTTP API | Lambda, HttpApi, IAM, CodeBuild, Step Functions, Athena, Glue, AOSS, Secrets Manager |
| `auth.ts` | User authentication | Cognito User Pools, Clients, IdPs (Google, OIDC), WAF, Lambda triggers, Secrets Manager |
| `database.ts` | Data persistence | DynamoDB (3 tables), IAM roles, PITR |
| `websocket.ts` | Real-time communication | WebSocket API, Lambda handler, S3 (large payloads), DynamoDB, Cognito |
| `frontend.ts` | SPA hosting & CDN | S3, CloudFront, Route53, ACM certificates |
| `embedding.ts` | Knowledge base sync orchestration | Step Functions, Lambda (6 handlers), CodeBuild, DynamoDB Streams |
| `bot-store.ts` | Bot search | OpenSearch Serverless, DynamoDB, S3, OSIS pipeline |
| `usage-analysis.ts` | Analytics pipeline | S3, Athena, Glue Database, EventBridge, Lambda |
| `bedrock-custom-bot-codebuild.ts` | Custom bot deployment automation | CodeBuild project |
| `bedrock-shared-knowledge-bases-codebuild.ts` | Shared KB deployment automation | CodeBuild project |
| `api-publish-codebuild.ts` | API publishing automation | CodeBuild project |
| `webacl-for-cognito.ts` | Cognito security layer | WAF v2 |
| `webacl-for-published-api.ts` | API security layer | WAF v2 |

---

## AWS Services

### Compute

| Service | Usage |
|---------|-------|
| **AWS Lambda** | Backend handlers (Python 3.13, Docker container images), WebSocket handlers, Step Function task handlers, custom Cognito triggers, usage analysis |
| **AWS CodeBuild** | 3 projects for dynamically deploying custom bot, shared KB, and API publish stacks |

### API & Networking

| Service | Usage |
|---------|-------|
| **API Gateway v2 (HTTP API)** | REST API for the backend |
| **API Gateway v2 (WebSocket API)** | Real-time streaming to frontend |
| **API Gateway (REST API)** | Published bot API deployments |
| **Amazon CloudFront** | CDN for the frontend SPA (geo-restriction, custom domains) |
| **Amazon Route 53** | DNS records (A + AAAA) for custom domains |

### Storage

| Service | Usage |
|---------|-------|
| **Amazon DynamoDB** | 3 tables (Conversations, Bots, WebSocket Sessions) with GSIs, LSIs, PITR, Streams |
| **Amazon S3** | ~8 buckets: documents, frontend assets, large WebSocket messages, CodeBuild sources, access logs, Athena results, DDB exports, temp files |

### AI / ML

| Service | Usage |
|---------|-------|
| **Amazon Bedrock** | LLM inference (Converse API), Knowledge Bases (S3 + web crawler data sources), Guardrails, cross-region and global inference routing |
| **Amazon OpenSearch Serverless** | Vector store for RAG and bot store search |

### Authentication & Security

| Service | Usage |
|---------|-------|
| **Amazon Cognito** | User pools, OAuth, user groups, external IdP support (Google, OIDC) |
| **AWS WAF v2** | 3 WAFs: frontend (CloudFront scope), Cognito (regional), published API (regional) |
| **AWS Certificate Manager** | SSL/TLS certificates for custom domains |
| **AWS Secrets Manager** | OAuth credentials, IdP secrets, API keys |
| **AWS IAM** | Row-level DynamoDB access via TableAccessRole assumption |

### Orchestration & Events

| Service | Usage |
|---------|-------|
| **AWS Step Functions** | State machine for knowledge base embedding/sync workflows (map states, error handling, lock acquisition) |
| **Amazon EventBridge** | Scheduled rules for daily usage analysis exports |

### Analytics

| Service | Usage |
|---------|-------|
| **Amazon Athena** | SQL queries over DynamoDB exports for usage analysis |
| **AWS Glue** | Data catalog (database + tables) for Athena |

### Monitoring

| Service | Usage |
|---------|-------|
| **Amazon CloudWatch Logs** | Log groups for Lambda, CodeBuild, API Gateway (configurable retention) |
| **Amazon CloudWatch Metrics** | WAF metrics |

### Deployment

| Service | Usage |
|---------|-------|
| **AWS CloudFormation** | Stack deployment, cross-region references, custom resources |
| **Amazon ECR** | Docker image assets for Lambda container images |

---

## Key Deployment Patterns

### Multi-Region Strategy

- **Frontend WAF** deployed to `us-east-1` (CloudFront requirement)
- **Main stack** deployed to `CDK_DEFAULT_REGION`
- **Bedrock resources** deployed to a separate Bedrock-available region
- Cross-region references via CloudFormation exports/imports

### Event-Driven Architecture

- DynamoDB Streams trigger Step Functions for knowledge base synchronization
- EventBridge schedules daily usage analysis exports

### Dynamic Stack Deployment

- CodeBuild projects run `cdk deploy` to create/update per-bot and per-API stacks on demand
- Three CodeBuild projects: custom bot, shared KBs, API publish

### Row-Level Security

- Lambda assumes a scoped IAM role (`TableAccessRole`) for DynamoDB access
- Enforces tenant isolation at the data layer
- DynamoDB partition key pattern: `userId#TYPE#itemId`

### Large Message Routing

- WebSocket payloads exceeding 32KB are stored in S3 (`LargeMessageBucket`)
- Client retrieves from S3 instead of receiving directly over WebSocket

### Security Layers

1. **Frontend**: CloudFront WAF with IP allowlisting + geo-restriction
2. **Cognito**: Regional WAF with IP allowlisting
3. **Published API**: Regional WAF with IP allowlisting + usage plans with throttling

---

## DynamoDB Table Design

| Table | PK | SK | Features |
|-------|----|----|----------|
| **ConversationTable** | UserId | ConversationId | GSI: SKIndex |
| **BotTable** | UserId | ItemType | LSIs: StarredIndex, LastUsedTimeIndex; GSIs: BotIdIndex, SharedScopeIndex, ItemTypeIndex, SyncStatusIndex |
| **WebsocketSessionTable** | — | — | Session tracking |

All tables use PAY_PER_REQUEST billing with point-in-time recovery enabled.

---

## Step Functions Workflow (Embedding Orchestration)

The embedding state machine manages knowledge base ingestion:

1. Bootstrap initialization
2. Conditional check: shared KBs need sync?
3. CodeBuild task execution (triggers nested stack deployments)
4. Lambda handlers: UpdateSyncStatus, BootstrapStateMachine, FinalizeCustomBotBuild, FinalizeSharedKnowledgeBasesBuild, SynchronizeDataSource, AcquireLock/ReleaseLock
5. Map states for parallel processing of multiple knowledge bases
6. Error handling with custom error paths

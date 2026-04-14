Code Changes

  New Files (5)

  1. backend/app/routes/schemas/agentcore.py
  - Pydantic request/response models for the /agentcore/invoke endpoint
  - AgentCoreInvokeRequest: message (str, 1-10000 chars), session_id (optional, regex-validated)
  - AgentCoreInvokeResponse: response (str), session_id (str)

  2. backend/app/routes/agentcore.py
  - FastAPI proxy route that forwards messages to AgentCore via boto3
  - Lazy-initialized bedrock-agentcore boto3 client (to avoid import-time failure if service model unavailable)
  - Generates session IDs as {uuid}-{user_id[:8]}
  - Maps ClientError codes to HTTP status codes (429, 403, 502)
  - Reads streaming blob response, parses JSON, returns structured response

  3. frontend/src/@types/agentcore.d.ts
  - TypeScript interfaces matching the backend Pydantic models

  4. frontend/src/hooks/useAgentCoreApi.ts
  - HTTP hook wrapping useHttp().post() for /agentcore/invoke
  - Extracts .data from AxiosResponse (important: useHttp.post() returns AxiosResponse<T>, not T)

  5. frontend/src/hooks/useAgentCoreChat.ts
  - Lightweight chat state hook (REST, non-streaming) — alternative to the 744-line useChat (Zustand + XState streaming)
  - Manages: messages[], sessionId, isLoading, error
  - Exposes: sendMessage(content), resetChat()

  Modified Files (7)

  6. cdk/lib/constructs/api.ts
  - Added IAM policy statement: bedrock-agentcore:InvokeAgentRuntime on arn:aws:bedrock-agentcore:us-west-2:${account}:runtime/*
  - Added Lambda environment variables: AGENTCORE_RUNTIME_ARN, AGENTCORE_REGION

  7. backend/app/main.py
  - Added from app.routes.agentcore import router as agentcore_router
  - Added app.include_router(agentcore_router)

  8. backend/app/repositories/models/custom_bot.py
  - Added backend_type: str = "bedrock" field to BotModel and BotAliasModel
  - Updated from_dynamo_item() to read BackendType (defaults to "bedrock")
  - Updated to_summary_output() to include backend_type
  - Updated from_bot_for_initial_alias() and from_existing_bot_and_alias() to propagate backend_type

  9. backend/app/routes/schemas/bot.py
  - Added backend_type: str = "bedrock" to BotSummaryOutput
  - Note: BaseSchema uses alias_generator = camelize, so this serializes as backendType in JSON

  10. frontend/src/@types/bot.d.ts
  - Added backendType: 'bedrock' | 'agentcore' to BotSummary type

  11. frontend/src/pages/ChatPage.tsx
  - Added isAgentCoreBot = bot?.backendType === 'agentcore' detection
  - Added useAgentCoreChat() hook
  - Hid model selector (SwitchBedrockModel) when isAgentCoreBot
  - Hid Reasoning toggle: supportReasoning={isAgentCoreBot ? false : supportReasoning}
  - Branched onSend: calls agentCoreChat.sendMessage(content) instead of postChat() for AgentCore bots
  - Added AgentCore message rendering with ChatMessageMarkdown (non-streaming, full response)
  - Added loading spinner (CSS animated border spinner)
  - Added error display with PiWarningCircleFill
  - Disabled send button, regenerate, continue, reasoning when AgentCore loading

  12. .gitignore
  - Minor update (details in git diff)

  ---
  DynamoDB Data Inserted

  Table: BedrockChatStack-DatabaseBotTableV3201CEEA9-9ZLCRQ1AC3TF (us-east-1)

  Seeded one bot record via AWS CLI put-item:

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

  Key design: SharedScope: "all" + SharedStatus: "pinned@001" makes it auto-appear in every user's Pinned Bots sidebar section.

  ---
  AWS Services Modified

  1. Lambda Function

  Resource: BedrockChatStack-BackendApiHandlerV263FCB936-t5smUQY1OUDx

  - Code updated: Uploaded new zip with the backend code changes (agentcore route, schemas, model changes, router registration)
  - Configuration updated: Added environment variables:
    - AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/laila_agent_dev-zubMFc4Xdg
    - AGENTCORE_REGION=us-west-2
  - Published version 5: Lambda was versioned (API Gateway points to specific version, not $LATEST)

  2. API Gateway

  Resource: API 0powds3x6d, Integration 8j8s0kn

  - Updated integration URI to point to Lambda version 5 (was pointing to version 2)
  - This was necessary because the API Gateway integration was pinned to a specific Lambda version, not $LATEST

  3. IAM

  Role: BedrockChatStack-BackendApiHandlerRoleFEC9E49E-NgpnBEA6ydZ3

  - Added inline policy AgentCoreInvokePolicy:
  {
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": "arn:aws:bedrock-agentcore:us-west-2:799870512242:runtime/*"
  }
  - Required because the existing bedrock:* policy does NOT cover AgentCore (separate service namespace bedrock-agentcore)

  4. DynamoDB

  Table: BedrockChatStack-DatabaseBotTableV3201CEEA9-9ZLCRQ1AC3TF (us-east-1)

  - Inserted the bot record described above

  ---
  Key Problems Solved During Implementation

  1. Module-level boto3 client crash: boto3.client("bedrock-agentcore") at module level failed if the service model wasn't available, preventing the entire router from registering (404 on /agentcore/invoke). Fixed with lazy
  initialization pattern.
  2. Lambda versioned deployment: API Gateway pointed to Lambda version 2, not $LATEST. Code updates to $LATEST had no effect on API responses. Had to publish new versions and update the integration URI.
  3. Lambda config/code mismatch: After update-function-configuration (adding env vars), had to re-upload the code zip and publish a new version (5) to get both code and config in sync.
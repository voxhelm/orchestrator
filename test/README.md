# End-to-End Testing for DynamoDB Config Delivery

## Quick Start

```bash
# Run the complete end-to-end test
./test/test_e2e.sh
```

This will:
1. ✅ Start DynamoDB Local
2. ✅ Create test configuration in DynamoDB
3. ✅ Build and start test container with OpenClaw + openclaw-agent
4. ✅ Verify all services start correctly
5. ✅ Test OpenClaw query functionality
6. ✅ Check agent registration
7. ✅ Show logs

## What Gets Tested

### Services Started

The test container runs 4 services in a single container:

| Service | Port | Description |
|---------|------|-------------|
| **OpenClaw** | 18789 | LLM inference gateway |
| **openclaw-agent** | 8080 | Agent management API |
| **mock-auth-gateway** | 8789 | Mock authentication service |
| **mock-voice-gateway** | 9090 | Mock WebSocket voice service |

### Test Flow

```
1. DynamoDB Setup
   ├─ Create table: openclaw-containers
   ├─ Insert system config (auth URLs, openclaw URL)
   └─ Insert user config (API keys, LLM provider)

2. Container Startup
   ├─ Start OpenClaw gateway
   ├─ Start mock auth & voice gateways
   ├─ Fetch config from DynamoDB
   │  ├─ GET USER#test-user-123 + CONFIG#primary
   │  ├─ GET SYSTEM + CONFIG#defaults
   │  ├─ Build openclaw.json
   │  └─ Build voxhelm.json
   └─ Start openclaw-agent
      ├─ Reads ~/.voxhelm/voxhelm.json
      ├─ Authenticates with auth-gateway
      └─ Registers agents

3. Verification
   ├─ Test OpenClaw query (/v1/chat/completions)
   ├─ Check agent health (/health)
   └─ Verify agent registration
```

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- curl, jq
- Anthropic API key (or other LLM provider)

Set your API key in `.env`:

```bash
# orchestrator/.env
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
```

Or the test will use a placeholder (OpenClaw queries will fail).

## Viewing Logs

### All Logs (Live Tail)

```bash
docker logs orchestrator-test-container -f
```

### Specific Service Logs

```bash
# OpenClaw logs
docker exec orchestrator-test-container cat /tmp/openclaw.log

# Agent logs
docker exec orchestrator-test-container cat /tmp/agent.log

# Auth gateway logs
docker exec orchestrator-test-container cat /tmp/auth-gateway.log

# Voice gateway logs
docker exec orchestrator-test-container cat /tmp/voice-gateway.log
```

### Filter Logs by Service

All logs are prefixed with `[service-name]` for easy filtering:

```bash
# Show only OpenClaw logs
docker logs orchestrator-test-container 2>&1 | grep '\[openclaw\]'

# Show only agent logs
docker logs orchestrator-test-container 2>&1 | grep '\[agent\]'

# Show only entrypoint/startup logs
docker logs orchestrator-test-container 2>&1 | grep '\[entrypoint\]'

# Show errors only
docker logs orchestrator-test-container 2>&1 | grep -i error
```

## Manual Testing

After running `./test/test_e2e.sh`, services are running and you can test manually:

### Query OpenClaw

```bash
curl -X POST http://localhost:18789/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer test-token-123' \
  -d '{
    "model": "claude-3-haiku-20240307",
    "messages": [{"role": "user", "content": "Hello! How are you?"}],
    "max_tokens": 100
  }' | jq
```

### Check Agent Status

```bash
curl http://localhost:8082/agents/status | jq
```

### Check Agent Config

```bash
curl http://localhost:8082/config | jq
```

### View Registered Agents

```bash
curl http://localhost:8789/users/test-user-123/agents | jq
```

### Check Mock Gateway Metrics

```bash
curl http://localhost:9090/metrics | jq
```

## Inspecting Configuration

### View Configs Inside Container

```bash
# Agent config
docker exec orchestrator-test-container cat /home/node/.voxhelm/voxhelm.json | jq

# OpenClaw config
docker exec orchestrator-test-container cat /home/node/.openclaw/openclaw.json | jq
```

### View DynamoDB Config

```bash
# User config
aws dynamodb get-item \
  --table-name openclaw-containers \
  --key '{"pk":{"S":"USER#test-user-123"},"sk":{"S":"CONFIG#primary"}}' \
  --endpoint-url http://localhost:8000 \
  --region ap-southeast-2 \
  | jq

# System config
aws dynamodb get-item \
  --table-name openclaw-containers \
  --key '{"pk":{"S":"SYSTEM"},"sk":{"S":"CONFIG#defaults"}}' \
  --endpoint-url http://localhost:8000 \
  --region ap-southeast-2 \
  | jq
```

## Troubleshooting

### Container won't start

**Check build logs:**
```bash
docker-compose -f test/docker-compose.e2e.yml build --no-cache
```

**Check startup logs:**
```bash
docker logs orchestrator-test-container
```

### Config fetch fails

**Error:** `Failed to fetch configuration from DynamoDB`

**Check:**
1. DynamoDB is running: `curl http://localhost:8000`
2. Table exists: `aws dynamodb list-tables --endpoint-url http://localhost:8000`
3. Config exists: See "View DynamoDB Config" above
4. Container can reach DynamoDB: `docker exec orchestrator-test-container curl http://dynamodb-local:8000`

### OpenClaw query fails

**Error:** `choices[0].message.content not found`

**Likely causes:**
1. Invalid API key (check `.env` has valid `ANTHROPIC_API_KEY`)
2. OpenClaw not started (check `/tmp/openclaw.log`)
3. LLM provider configuration wrong

### No agents registered

This is **expected** on first startup. Agents are only registered after:
1. OpenClaw CLI agents are configured
2. Agent startup completes registration flow

To test agent registration, you'd need to:
1. Configure agents in OpenClaw CLI
2. Restart openclaw-agent
3. Check mock-auth-gateway for registered agents

## Cleanup

```bash
# Stop all containers
docker-compose -f test/docker-compose.e2e.yml down

# Remove volumes
docker-compose -f test/docker-compose.e2e.yml down -v

# Remove DynamoDB data (it's in-memory, so just restart)
docker restart orchestrator-dynamodb-test
```

## Advanced: Running Individual Steps

### 1. Start just DynamoDB

```bash
docker-compose -f test/docker-compose.e2e.yml up -d dynamodb-local
```

### 2. Create config manually

```bash
python3 scripts/setup_test_config.py \
  --user-id test-user-123 \
  --anthropic-key sk-ant-... \
  --endpoint http://localhost:8000
```

### 3. Test config fetch (outside container)

```bash
export USER_ID=test-user-123
export CONTAINER_ID=oc-test-001
export DYNAMODB_ENDPOINT=http://localhost:8000

python3 scripts/container/fetch_config.py
```

### 4. Build container only

```bash
docker-compose -f test/docker-compose.e2e.yml build test-container
```

### 5. Start container with shell (debug)

```bash
docker-compose -f test/docker-compose.e2e.yml run --rm test-container /bin/bash
```

## What This Tests

✅ DynamoDB configuration storage
✅ Configuration fetch from DynamoDB
✅ OpenClaw config generation
✅ openclaw-agent config generation
✅ Multi-service startup coordination
✅ OpenClaw query functionality
✅ Agent registration flow (when agents configured)
✅ Log accessibility and filtering

## Architecture Tested

```
┌─────────────────────────────────────────────────────┐
│ DynamoDB Local Container                             │
│   - User config (API keys, LLM provider)            │
│   - System config (URLs, tokens)                    │
└────────────────┬────────────────────────────────────┘
                 │
                 │ Fetch config on startup
                 │
┌────────────────▼────────────────────────────────────┐
│ Test Container                                       │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ 1. fetch_config.py                            │  │
│  │    ├─ Fetch from DynamoDB                     │  │
│  │    ├─ Build openclaw.json                     │  │
│  │    └─ Build voxhelm.json                     │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ 2. OpenClaw Gateway (port 18789)              │  │
│  │    └─ Handles LLM inference                   │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ 3. openclaw-agent (port 8080)                 │  │
│  │    ├─ Reads voxhelm.json                     │  │
│  │    ├─ Authenticates with auth-gateway         │  │
│  │    └─ Manages voice agents                    │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ 4. Mock Gateways (auth:8789, voice:9090)     │  │
│  │    └─ Simulate production services            │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

This setup validates the complete configuration delivery pipeline as it would work in production (minus encryption).

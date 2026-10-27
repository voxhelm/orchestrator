# Container Requirements - Based on openclaw-agent

This document outlines the requirements for running a container that includes both OpenClaw and openclaw-agent, based on the proven approach in `../openclaw-agent`.

## Overview

The openclaw-agent repository has a **complete, working container setup** that:
- ✅ Runs OpenClaw gateway (port 18789)
- ✅ Runs openclaw-agent (port 8080)
- ✅ Includes mock auth-gateway (port 8789) for testing
- ✅ Includes mock voice-gateway (port 9090) for testing
- ✅ Has proper startup orchestration
- ✅ Creates both config files dynamically
- ✅ Works in test and production modes

## Key Files from openclaw-agent

### 1. Dockerfile.test
**Location:** `../openclaw-agent/Dockerfile.test`

**What it does:**
- Multi-stage build with Go builder
- Builds `openclaw-agent`, `mock-voice-gateway`, `mock-auth-gateway`
- Uses `alpine/openclaw:2026.3.24` as base image (includes OpenClaw CLI)
- Copies binaries and startup script
- Runs as `node` user
- Exposes ports: 18789 (OpenClaw), 8080 (agent), 8789 (auth), 9090 (voice)

**Key sections:**
```dockerfile
# Build all binaries
FROM golang:1.24-alpine AS builder
RUN CGO_ENABLED=0 go build -o /openclaw-agent ./cmd/server
RUN CGO_ENABLED=0 go build -o /mock-voice-gateway ./test/integration/mock_voice_gateway
RUN CGO_ENABLED=0 go build -o /mock-auth-gateway ./test/integration/mock_auth_gateway

# Runtime with OpenClaw
FROM alpine/openclaw:2026.3.24
COPY --from=builder /openclaw-agent /usr/local/bin/openclaw-agent
COPY --from=builder /mock-voice-gateway /usr/local/bin/mock-voice-gateway
COPY --from=builder /mock-auth-gateway /usr/local/bin/mock-auth-gateway
COPY docker-start.sh /start.sh
ENTRYPOINT ["/start.sh"]
```

### 2. docker-start.sh
**Location:** `../openclaw-agent/docker-start.sh`

**What it does:**
1. Creates OpenClaw config at `/home/node/.openclaw/openclaw.json`
2. Sets permissions (700 for directory, 600 for file)
3. Starts OpenClaw gateway on port 18789 (background)
4. Waits for OpenClaw to be healthy
5. If RUN_MODE=test, starts mock-auth-gateway and mock-voice-gateway
6. Creates agent config at `/home/node/.voxhelm/voxhelm.json`
7. Detects LLM provider from environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY)
8. Starts openclaw-agent
9. Waits for any process to exit

**Critical startup order:**
```
OpenClaw → Mock Services → Agent Config → openclaw-agent
```

**Config creation logic:**
```bash
# Detects LLM provider from environment
if [ -n "$ANTHROPIC_API_KEY" ]; then
    LLM_PROVIDER="anthropic"
    LLM_KEY="$ANTHROPIC_API_KEY"
elif [ -n "$OPENAI_API_KEY" ]; then
    LLM_PROVIDER="openai"
    LLM_KEY="$OPENAI_API_KEY"
elif [ -n "$OPENROUTER_API_KEY" ]; then
    LLM_PROVIDER="openrouter"
    LLM_KEY="$OPENROUTER_API_KEY"
fi

# Creates voxhelm.json dynamically
cat > /home/node/.voxhelm/voxhelm.json << AGENTCONFIG
{
  "llm_provider": "$LLM_PROVIDER",
  "${LLM_PROVIDER}_api_key": "$LLM_KEY",
  "openclaw_model": "$LLM_MODEL",
  "openclaw_url": "http://localhost:18789/v1",
  "openclaw_token": "${OPENCLAW_GATEWAY_TOKEN:-}",
  "auth_gateway_url": "$AUTH_GATEWAY_URL",
  "auth_gateway_api_key": "$AUTH_GATEWAY_API_KEY",
  "agents": []
}
AGENTCONFIG
```

### 3. OpenClaw Config Template
**Location:** `../openclaw-agent/test/integration/openclaw.json`

**Structure:**
```json
{
  "gateway": {
    "port": 18789,
    "mode": "local",
    "bind": "lan",
    "auth": {
      "mode": "token",
      "token": "test-token-123"
    },
    "http": {
      "endpoints": {
        "chatCompletions": {
          "enabled": true
        }
      }
    }
  },
  "models": {
    "providers": {
      "openrouter": {
        "baseUrl": "https://openrouter.ai/api/v1",
        "apiKey": "${OPENROUTER_API_KEY}",
        "api": "openai-completions",
        "models": [...]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "openrouter/anthropic/claude-haiku-4.5"
      }
    }
  }
}
```

**Note:** Environment variables like `${OPENROUTER_API_KEY}` are expanded by OpenClaw at runtime.

### 4. Agent Config (voxhelm.json)
**Location:** Created dynamically in `docker-start.sh`

**Structure:**
```json
{
  "llm_provider": "anthropic",
  "anthropic_api_key": "sk-ant-...",
  "openclaw_model": "claude-3-haiku-20240307",
  "openclaw_url": "http://localhost:18789/v1",
  "openclaw_token": "test-token-123",
  "auth_gateway_url": "http://localhost:8789",
  "auth_gateway_api_key": "test-api-key",
  "agents": []
}
```

### 5. Environment Variables Required

**Minimal (for testing with mocks):**
```bash
RUN_MODE=test
ANTHROPIC_API_KEY=sk-ant-api03-...
OPENCLAW_GATEWAY_TOKEN=test-token-123
AUTH_GATEWAY_URL=http://localhost:8789
AUTH_GATEWAY_API_KEY=test-api-key
```

**Production:**
```bash
RUN_MODE=production
ANTHROPIC_API_KEY=sk-ant-api03-...  # From DynamoDB
OPENCLAW_GATEWAY_TOKEN=<secure-token>
AUTH_GATEWAY_URL=https://auth.voxhelm.com
AUTH_GATEWAY_API_KEY=<user-specific-key>  # From DynamoDB
USER_ID=<user-id>  # For config fetch
CONTAINER_ID=<container-id>
DYNAMODB_TABLE=openclaw-containers
AWS_REGION=ap-southeast-2
```

## How This Maps to Orchestrator

### Current Approach (DynamoDB Config Fetch)

Our orchestrator implementation uses the same pattern but with DynamoDB:

1. **Container startup:** `entrypoint-e2e.sh` (similar to `docker-start.sh`)
2. **Config fetch:** `fetch_config.py` fetches from DynamoDB
3. **Config files:** Creates both `openclaw.json` and `voxhelm.json`
4. **Service startup:** OpenClaw → Mocks → Agent (same order)

### Differences

| openclaw-agent | orchestrator |
|----------------|--------------|
| Config from environment variables | Config from DynamoDB |
| `docker-start.sh` creates config inline | `fetch_config.py` fetches from DynamoDB |
| Environment vars: `ANTHROPIC_API_KEY`, etc. | DynamoDB stores encrypted keys |
| `RUN_MODE=test` for mocks | Same approach |
| Dockerfile.test builds everything | Dockerfile.e2e builds everything |

### What We Reuse

✅ **Reused from openclaw-agent:**
- Base image: `alpine/openclaw:2026.3.24`
- Startup order: OpenClaw → Mocks → Agent
- Port allocation: 18789 (OpenClaw), 8080 (agent), 8789 (auth), 9090 (voice)
- Permission handling: 700 for directories, 600 for config files
- Mock services for testing: `mock-auth-gateway`, `mock-voice-gateway`

✅ **Added for production:**
- DynamoDB config storage
- Python script to fetch config from DynamoDB
- Encryption support (placeholder)
- User/container ID for multi-tenancy

## Required Components for Production Container

### 1. Base Image
```dockerfile
FROM alpine/openclaw:2026.3.24
```
**Provides:** OpenClaw CLI binary at `/usr/local/bin/openclaw`

### 2. Binaries Needed
```
/usr/local/bin/openclaw          # From base image
/usr/local/bin/openclaw-agent    # Built from openclaw-agent repo
/usr/local/bin/mock-auth-gateway # For testing only
/usr/local/bin/mock-voice-gateway # For testing only
```

### 3. Scripts Needed
```
/opt/scripts/fetch_config.py     # Fetch config from DynamoDB
/opt/scripts/entrypoint.sh       # Startup orchestration
```

### 4. Dependencies
```
apk add --no-cache python3 py3-pip
pip3 install boto3
```

### 5. Directory Structure
```
/home/node/.openclaw/
  openclaw.json (600)           # OpenClaw gateway config

/home/node/.voxhelm/
  voxhelm.json (600)           # openclaw-agent config

/tmp/
  openclaw.log                  # OpenClaw logs
  agent.log                     # Agent logs
  auth-gateway.log              # Auth gateway logs (test mode)
  voice-gateway.log             # Voice gateway logs (test mode)
```

## Configuration File Requirements

### openclaw.json (OpenClaw Gateway)

**Purpose:** Configure OpenClaw gateway with LLM providers and authentication

**Required sections:**
1. `gateway` - Port, auth mode, token
2. `models.providers` - LLM provider configs (anthropic, openrouter, openai)
3. `agents.defaults.model.primary` - Default model to use

**Critical fields:**
- `gateway.auth.token` - Must match what agent uses
- `models.providers.{provider}.apiKey` - LLM API key
- Environment variable expansion: `${ANTHROPIC_API_KEY}`

### voxhelm.json (openclaw-agent)

**Purpose:** Configure openclaw-agent with LLM, auth gateway, and OpenClaw connection

**Required fields:**
- `llm_provider` - "anthropic", "openrouter", or "openai"
- `{provider}_api_key` - API key for the chosen provider
- `openclaw_model` - Model identifier (e.g., "claude-3-haiku-20240307")
- `openclaw_url` - URL to OpenClaw gateway (http://localhost:18789/v1)
- `openclaw_token` - Token for OpenClaw auth (must match gateway config)
- `auth_gateway_url` - Auth gateway URL for agent registration
- `auth_gateway_api_key` - API key for auth gateway (user-specific)
- `user_id` - User ID (optional, for tracking)
- `agents` - Array of configured agents (initially empty)

## Health Checks

**From docker-compose.test.yml:**
```yaml
healthcheck:
  test: ["CMD", "sh", "-c",
    "curl -f http://localhost:18789/healthz &&
     curl -f http://localhost:8080/health &&
     curl -f http://localhost:8789/health &&
     curl -f http://localhost:9090/health"]
  interval: 5s
  timeout: 3s
  retries: 10
  start_period: 40s
```

**Endpoints:**
- OpenClaw: `GET http://localhost:18789/healthz`
- openclaw-agent: `GET http://localhost:8080/health`
- mock-auth-gateway: `GET http://localhost:8789/health` (test mode only)
- mock-voice-gateway: `GET http://localhost:9090/health` (test mode only)

## Startup Sequence (Verified Working)

From `docker-start.sh`:

```
1. Configure OpenClaw
   - Create /home/node/.openclaw/openclaw.json
   - Set permissions (700/600)

2. Start OpenClaw
   - Run: openclaw gateway --port 18789 &
   - Wait for health: http://localhost:18789/healthz

3. Start Mock Services (if RUN_MODE=test)
   - mock-auth-gateway on port 8789
   - mock-voice-gateway on port 9090
   - Wait for each to be healthy

4. Create Agent Config
   - Detect LLM provider from env vars
   - Create /home/node/.voxhelm/voxhelm.json
   - Set permissions (700/600)

5. Start openclaw-agent
   - Run: openclaw-agent &
   - Exports: PORT=8080, OPENCLAW_URL, GIN_MODE=release

6. Wait for any process to exit
   - If one fails, kill all others
```

## Testing Approach

From `docker-compose.test.yml` and `.env`:

**Environment:**
```bash
RUN_MODE=test
ANTHROPIC_API_KEY=sk-ant-api03-...
OPENCLAW_GATEWAY_TOKEN=test-token-123
AUTH_GATEWAY_URL=http://localhost:8789
AUTH_GATEWAY_API_KEY=test-api-key
VOICE_GATEWAY_URL=ws://localhost:9090/ws
```

**Build and run:**
```bash
docker compose -f docker-compose.test.yml up -d
docker logs openclaw-agent-test -f
```

**Test queries:**
```bash
# OpenClaw query
curl -X POST http://localhost:18789/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer test-token-123' \
  -d '{
    "model": "claude-3-haiku-20240307",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Agent health
curl http://localhost:8080/health

# Agent config
curl http://localhost:8080/config
```

## Production Dockerfile (Dockerfile.ecs)

**Current approach in openclaw-agent:**
```dockerfile
FROM alpine:3.19
RUN apk add --no-cache aws-cli ca-certificates wget
COPY --from=builder /openclaw-agent /openclaw-agent
COPY scripts/docker-entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

**What's needed for orchestrator:**
```dockerfile
FROM alpine/openclaw:2026.3.24
RUN apk add --no-cache python3 py3-pip ca-certificates curl
RUN pip3 install boto3
COPY --from=builder /openclaw-agent /usr/local/bin/openclaw-agent
COPY --from=builder /mock-auth-gateway /usr/local/bin/mock-auth-gateway  # Test only
COPY --from=builder /mock-voice-gateway /usr/local/bin/mock-voice-gateway  # Test only
COPY scripts/container/fetch_config.py /opt/scripts/fetch_config.py
COPY scripts/container/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

## Key Takeaways

1. **OpenClaw base image is essential** - Provides the OpenClaw CLI
2. **Startup order matters** - OpenClaw must be ready before agent starts
3. **Config files need strict permissions** - 700 for dirs, 600 for files
4. **Mock services simplify testing** - No need for real auth/voice gateways
5. **Environment variable detection works** - Check for API keys to determine provider
6. **Health checks are comprehensive** - Check all services, not just agent
7. **The pattern is proven** - openclaw-agent has this working in production

## Next Steps for Orchestrator

1. ✅ Update `.env.example` with all required vars (DONE)
2. ⏳ Test `make test-e2e-aws` with real ANTHROPIC_API_KEY
3. ⏳ Verify config fetch from AWS DynamoDB works
4. ⏳ Ensure both config files are created correctly
5. ⏳ Verify OpenClaw and agent start successfully
6. ⏳ Test OpenClaw queries work with real LLM
7. ⏳ Verify agent registration with auth-gateway

## References

- openclaw-agent repo: `../openclaw-agent/`
- Test dockerfile: `../openclaw-agent/Dockerfile.test`
- Startup script: `../openclaw-agent/docker-start.sh`
- Test compose: `../openclaw-agent/docker-compose.test.yml`
- OpenClaw config: `../openclaw-agent/test/integration/openclaw.json`

#!/bin/bash
#
# End-to-end test entrypoint
# Starts OpenClaw, mock gateways, fetches config from DynamoDB, and starts openclaw-agent
#
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[entrypoint]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[entrypoint]${NC} $1"
}

log_error() {
    echo -e "${RED}[entrypoint]${NC} $1"
}

log_section() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
}

# ============================================================================
# 1. CONFIGURATION FETCH FROM DYNAMODB
# ============================================================================

log_section "Fetching Configuration from DynamoDB"

# Create config directories
mkdir -p /home/node/.openclaw /home/node/.voxhelm
chmod 700 /home/node/.openclaw /home/node/.voxhelm

# Validate required environment variables
if [ -z "$USER_ID" ]; then
    log_error "USER_ID environment variable is required"
    exit 1
fi

if [ -z "$CONTAINER_ID" ]; then
    log_error "CONTAINER_ID environment variable is required"
    exit 1
fi

log_info "Container ID: $CONTAINER_ID"
log_info "User ID: $USER_ID"
log_info "DynamoDB Endpoint: ${DYNAMODB_ENDPOINT:-default}"
log_info "DynamoDB Table: ${DYNAMODB_TABLE:-openclaw-containers}"

# Fetch configuration from DynamoDB
log_info "Running fetch_config.py..."
if ! python3 /opt/scripts/fetch_config.py \
    --user-id "$USER_ID" \
    --container-id "$CONTAINER_ID" \
    --openclaw-config /home/node/.openclaw/openclaw.json \
    --agent-config /home/node/.voxhelm/voxhelm.json; then
    log_error "Failed to fetch configuration from DynamoDB"
    log_error "Check that config exists in DynamoDB for user_id=$USER_ID"
    exit 1
fi

# Verify config files were created
if [ ! -f /home/node/.openclaw/openclaw.json ]; then
    log_error "OpenClaw config not found after fetch"
    exit 1
fi

if [ ! -f /home/node/.voxhelm/voxhelm.json ]; then
    log_error "Agent config not found after fetch"
    exit 1
fi

log_info "✓ Configuration files created successfully"

# Show config summary (without secrets)
if command -v jq &> /dev/null; then
    log_section "Configuration Summary"

    LLM_PROVIDER=$(jq -r '.llm_provider // "not set"' /home/node/.voxhelm/voxhelm.json)
    AUTH_GATEWAY_URL=$(jq -r '.auth_gateway_url // "not set"' /home/node/.voxhelm/voxhelm.json)
    OPENCLAW_URL=$(jq -r '.openclaw_url // "not set"' /home/node/.voxhelm/voxhelm.json)

    log_info "LLM Provider: $LLM_PROVIDER"
    log_info "Auth Gateway URL: $AUTH_GATEWAY_URL"
    log_info "OpenClaw URL: $OPENCLAW_URL"

    # Check for API keys (without showing them)
    HAS_ANTHROPIC=$(jq -r '.anthropic_api_key // empty' /home/node/.voxhelm/voxhelm.json)
    HAS_OPENAI=$(jq -r '.openai_api_key // empty' /home/node/.voxhelm/voxhelm.json)
    HAS_AUTH=$(jq -r '.auth_gateway_api_key // empty' /home/node/.voxhelm/voxhelm.json)

    if [ -n "$HAS_ANTHROPIC" ]; then
        log_info "Anthropic API key: ${HAS_ANTHROPIC:0:20}..."
    fi
    if [ -n "$HAS_OPENAI" ]; then
        log_info "OpenAI API key: ${HAS_OPENAI:0:20}..."
    fi
    if [ -n "$HAS_AUTH" ]; then
        log_info "Auth API key: ${HAS_AUTH:0:30}..."
    fi
fi

# ============================================================================
# 2. OPENCLAW GATEWAY STARTUP
# ============================================================================

log_section "Starting OpenClaw Gateway"

# OpenClaw config already created by fetch_config.py above
log_info "Using OpenClaw config from DynamoDB..."

# Start OpenClaw gateway in the background
log_info "Starting OpenClaw gateway on port 18789..."
openclaw gateway --port 18789 > /tmp/openclaw.log 2>&1 &
OPENCLAW_PID=$!

# Wait for OpenClaw to be ready
log_info "Waiting for OpenClaw to start..."
for i in {1..30}; do
    if curl -f -s http://localhost:18789/healthz > /dev/null 2>&1; then
        log_info "✓ OpenClaw is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        log_error "OpenClaw failed to start within 30 seconds"
        cat /tmp/openclaw.log
        exit 1
    fi
    sleep 1
done

# ============================================================================
# 3. MOCK SERVICES STARTUP (Test Mode Only)
# ============================================================================

RUN_MODE="${RUN_MODE:-production}"
log_section "Mock Services (Mode: $RUN_MODE)"

if [ "$RUN_MODE" = "test" ]; then
    # Start mock auth gateway
    log_info "Starting mock-auth-gateway on port 8789..."
    mock-auth-gateway > /tmp/auth-gateway.log 2>&1 &
    AUTH_GATEWAY_PID=$!

    log_info "Waiting for mock-auth-gateway to start..."
    for i in {1..15}; do
        if curl -f -s http://localhost:8789/health > /dev/null 2>&1; then
            log_info "✓ mock-auth-gateway is ready"
            break
        fi
        if [ $i -eq 15 ]; then
            log_warn "mock-auth-gateway failed to start (continuing anyway)"
            cat /tmp/auth-gateway.log
        fi
        sleep 1
    done

    # Start mock voice gateway
    log_info "Starting mock-voice-gateway on port 9090..."
    mock-voice-gateway > /tmp/voice-gateway.log 2>&1 &
    VOICE_GATEWAY_PID=$!

    log_info "Waiting for mock-voice-gateway to start..."
    for i in {1..15}; do
        if curl -f -s http://localhost:9090/health > /dev/null 2>&1; then
            log_info "✓ mock-voice-gateway is ready"
            break
        fi
        if [ $i -eq 15 ]; then
            log_error "mock-voice-gateway failed to start"
            cat /tmp/voice-gateway.log
            exit 1
        fi
        sleep 1
    done
else
    log_info "Skipping mock services (production mode)"
    AUTH_GATEWAY_PID=""
    VOICE_GATEWAY_PID=""
fi

# ============================================================================
# 4. OPENCLAW-AGENT STARTUP
# ============================================================================

log_section "Starting openclaw-agent"

# Verify binary exists
if [ ! -f /usr/local/bin/openclaw-agent ]; then
    log_error "openclaw-agent binary not found at /usr/local/bin/openclaw-agent"
    exit 1
fi

# Start openclaw-agent
log_info "Starting openclaw-agent on port 8080..."
export PORT=8080
export GIN_MODE=release

openclaw-agent > /tmp/agent.log 2>&1 &
AGENT_PID=$!

# Wait for agent to be ready
log_info "Waiting for openclaw-agent to start..."
for i in {1..30}; do
    if curl -f -s http://localhost:8080/health > /dev/null 2>&1; then
        log_info "✓ openclaw-agent is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        log_error "openclaw-agent failed to start within 30 seconds"
        cat /tmp/agent.log
        exit 1
    fi
    sleep 1
done

# ============================================================================
# 5. STARTUP COMPLETE
# ============================================================================

log_section "All Services Started Successfully"

log_info "OpenClaw gateway:    http://localhost:18789 (PID: $OPENCLAW_PID)"
log_info "openclaw-agent:      http://localhost:8080 (PID: $AGENT_PID)"

if [ "$RUN_MODE" = "test" ]; then
    log_info "mock-auth-gateway:   http://localhost:8789 (PID: $AUTH_GATEWAY_PID)"
    log_info "mock-voice-gateway:  http://localhost:9090 (PID: $VOICE_GATEWAY_PID)"
fi

log_info ""
log_info "Logs available at:"
log_info "  OpenClaw:      /tmp/openclaw.log"
log_info "  Agent:         /tmp/agent.log"
if [ "$RUN_MODE" = "test" ]; then
    log_info "  Auth Gateway:  /tmp/auth-gateway.log"
    log_info "  Voice Gateway: /tmp/voice-gateway.log"
fi

log_section "Container Ready"
log_info "You can now run tests against this container"

# ============================================================================
# 6. KEEP CONTAINER RUNNING
# ============================================================================

# Build list of PIDs to wait for
WAIT_PIDS="$OPENCLAW_PID $AGENT_PID"
if [ -n "$AUTH_GATEWAY_PID" ]; then
    WAIT_PIDS="$WAIT_PIDS $AUTH_GATEWAY_PID"
fi
if [ -n "$VOICE_GATEWAY_PID" ]; then
    WAIT_PIDS="$WAIT_PIDS $VOICE_GATEWAY_PID"
fi

# Function to tail all logs
tail_all_logs() {
    log_section "Tailing all service logs"
    tail -f /tmp/*.log &
    TAIL_PID=$!
}

# Trap SIGTERM and SIGINT to gracefully shutdown
cleanup() {
    log_info "Shutting down services..."
    kill $WAIT_PIDS $TAIL_PID 2>/dev/null || true
    wait $WAIT_PIDS 2>/dev/null || true
    log_info "All services stopped"
}

trap cleanup SIGTERM SIGINT EXIT

# Start tailing logs
tail_all_logs

# Wait for any process to exit
wait -n $WAIT_PIDS

# If one exits, trigger cleanup
exit_code=$?
log_error "A service exited unexpectedly (exit code: $exit_code)"
cleanup
exit $exit_code

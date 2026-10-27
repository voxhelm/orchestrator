#!/bin/bash
#
# Container entrypoint script for openclaw-agent containers.
#
# This script:
# 1. Fetches configuration from DynamoDB
# 2. Writes openclaw.json and voxhelm.json config files
# 3. Starts the openclaw-agent process
#
# Required environment variables:
# - USER_ID: The user ID for this container
# - CONTAINER_ID: The container ID (for logging)
#
# Optional environment variables:
# - AWS_REGION: AWS region (default: ap-southeast-2)
# - DYNAMODB_ENDPOINT: DynamoDB endpoint for local dev
# - DYNAMODB_TABLE: Table name (default: openclaw-containers)

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check required environment variables
if [ -z "$USER_ID" ]; then
    log_error "ERROR: USER_ID environment variable is required"
    exit 1
fi

if [ -z "$CONTAINER_ID" ]; then
    log_error "ERROR: CONTAINER_ID environment variable is required"
    exit 1
fi

log_info "Starting container entrypoint"
log_info "Container ID: $CONTAINER_ID"
log_info "User ID: $USER_ID"

# Set defaults
export AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export DYNAMODB_TABLE="${DYNAMODB_TABLE:-openclaw-containers}"

log_info "AWS Region: $AWS_REGION"
log_info "DynamoDB Table: $DYNAMODB_TABLE"

if [ -n "$DYNAMODB_ENDPOINT" ]; then
    log_info "DynamoDB Endpoint: $DYNAMODB_ENDPOINT (local development)"
fi

# Fetch configuration from DynamoDB
log_info "Fetching configuration from DynamoDB..."

if ! python3 /opt/scripts/fetch_config.py \
    --user-id "$USER_ID" \
    --container-id "$CONTAINER_ID"; then
    log_error "Failed to fetch configuration from DynamoDB"
    exit 1
fi

# Verify config files were created
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
AGENT_CONFIG="$HOME/.voxhelm/voxhelm.json"

if [ ! -f "$OPENCLAW_CONFIG" ]; then
    log_error "OpenClaw config not found at $OPENCLAW_CONFIG"
    exit 1
fi

if [ ! -f "$AGENT_CONFIG" ]; then
    log_error "Agent config not found at $AGENT_CONFIG"
    exit 1
fi

log_info "✓ Configuration files created successfully"

# Display config summary (without secrets)
log_info "Configuration summary:"
log_info "  OpenClaw config: $OPENCLAW_CONFIG"
log_info "  Agent config: $AGENT_CONFIG"

# Show non-secret config values for debugging
if command -v jq &> /dev/null; then
    LLM_PROVIDER=$(jq -r '.llm_provider // "not set"' "$AGENT_CONFIG")
    AUTH_GATEWAY_URL=$(jq -r '.auth_gateway_url // "not set"' "$AGENT_CONFIG")
    OPENCLAW_URL=$(jq -r '.openclaw_url // "not set"' "$AGENT_CONFIG")

    log_info "  LLM Provider: $LLM_PROVIDER"
    log_info "  Auth Gateway URL: $AUTH_GATEWAY_URL"
    log_info "  OpenClaw URL: $OPENCLAW_URL"
else
    log_warn "jq not installed, skipping config summary"
fi

# Start openclaw-agent
log_info "Starting openclaw-agent..."

# Check if openclaw-agent binary exists
if [ ! -f "/usr/local/bin/openclaw-agent" ]; then
    log_error "openclaw-agent binary not found at /usr/local/bin/openclaw-agent"
    exit 1
fi

# Execute openclaw-agent (replaces this shell process)
exec /usr/local/bin/openclaw-agent

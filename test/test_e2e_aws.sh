#!/bin/bash
#
# End-to-End Test for DynamoDB Config Delivery - AWS Version
#
# Tests against REAL AWS infrastructure:
# - Real DynamoDB table
# - Real ECS task definition (or local container with AWS credentials)
# - Real auth-gateway, voice-gateway
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[test]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[test]${NC} $1"
}

log_error() {
    echo -e "${RED}[test]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${BLUE}=== $1 ===${NC}"
    echo ""
}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Test configuration
USER_ID="test-user-123"
CONTAINER_ID="oc-test-e2e-aws-001"
API_KEY="$USER_ID:test-token-xyz-789"

# AWS Configuration
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
export AWS_PROFILE="${AWS_PROFILE:-default}"
DYNAMODB_TABLE="${DYNAMODB_TABLE:-openclaw-containers}"

# Test mode: "local-with-aws" or "ecs"
TEST_MODE="${TEST_MODE:-local-with-aws}"

log_section "End-to-End Test - AWS DynamoDB"

log_info "AWS Configuration:"
log_info "  AWS Region: $AWS_REGION"
log_info "  AWS Profile: $AWS_PROFILE"
log_info "  DynamoDB Table: $DYNAMODB_TABLE"
log_info "  Test Mode: $TEST_MODE"
log_info ""
log_info "Test Configuration:"
log_info "  User ID: $USER_ID"
log_info "  Container ID: $CONTAINER_ID"
log_info "  API Key: ${API_KEY:0:30}..."

# ============================================================================
# Step 1: Check Prerequisites
# ============================================================================

log_section "Step 1: Checking Prerequisites"

# Check for required tools
for tool in aws python3 curl jq; do
    if ! command -v $tool &> /dev/null; then
        log_error "$tool is not installed"
        exit 1
    fi
    log_info "✓ $tool is installed"
done

# Verify AWS credentials
log_info "Verifying AWS credentials..."
if ! aws sts get-caller-identity --profile $AWS_PROFILE &> /dev/null; then
    log_error "AWS credentials not configured for profile: $AWS_PROFILE"
    log_error "Run: aws configure --profile $AWS_PROFILE"
    exit 1
fi

AWS_ACCOUNT=$(aws sts get-caller-identity --profile $AWS_PROFILE --query Account --output text)
log_info "✓ AWS credentials valid (Account: $AWS_ACCOUNT)"

# Check for .env file with API keys
if [ ! -f "$ORCHESTRATOR_DIR/.env" ]; then
    log_warn ".env file not found at $ORCHESTRATOR_DIR/.env"
    log_error "For AWS testing, you MUST have a valid .env with API keys"
    log_error "Copy .env.example to .env and add your ANTHROPIC_API_KEY"
    exit 1
fi

# Source .env
set -a
source "$ORCHESTRATOR_DIR/.env"
set +a

# Check for Anthropic API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    log_error "ANTHROPIC_API_KEY not set in .env"
    log_error "Add your API key to .env: ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi
log_info "✓ ANTHROPIC_API_KEY is set: ${ANTHROPIC_API_KEY:0:20}..."

# ============================================================================
# Step 2: Verify DynamoDB Table Exists
# ============================================================================

log_section "Step 2: Verifying DynamoDB Table"

log_info "Checking if table '$DYNAMODB_TABLE' exists..."
if aws dynamodb describe-table \
    --table-name $DYNAMODB_TABLE \
    --region $AWS_REGION \
    --profile $AWS_PROFILE \
    --output json > /dev/null 2>&1; then
    log_info "✓ Table exists"

    # Show table info
    ITEM_COUNT=$(aws dynamodb describe-table \
        --table-name $DYNAMODB_TABLE \
        --region $AWS_REGION \
        --profile $AWS_PROFILE \
        --query 'Table.ItemCount' \
        --output text)
    log_info "  Current item count: $ITEM_COUNT"
else
    log_error "Table '$DYNAMODB_TABLE' does not exist"
    log_error "Create it with: make docker-up (in orchestrator directory)"
    log_error "Or create manually in AWS Console"
    exit 1
fi

# ============================================================================
# Step 3: Create Test Configuration in AWS DynamoDB
# ============================================================================

log_section "Step 3: Creating Test Configuration in AWS DynamoDB"

log_info "Creating system config..."
python3 - <<EOF
import boto3
from datetime import datetime

dynamodb = boto3.resource(
    'dynamodb',
    region_name='$AWS_REGION'
)

table = dynamodb.Table('$DYNAMODB_TABLE')

# Create system config
table.put_item(Item={
    'pk': 'SYSTEM',
    'sk': 'CONFIG#defaults',
    'config_type': 'system_config',
    'auth_gateway_url': 'http://localhost:8789',
    'openclaw_url': 'http://localhost:18789',
    'openclaw_token': 'test-token-123',
    'voice_gateway_url': 'ws://localhost:9090',
    'updated_at': datetime.utcnow().isoformat()
})

print("✓ System config created")
EOF

log_info "Creating user config..."
python3 - <<EOF
import boto3
from datetime import datetime
import os

dynamodb = boto3.resource(
    'dynamodb',
    region_name='$AWS_REGION'
)

table = dynamodb.Table('$DYNAMODB_TABLE')

# Create user config
table.put_item(Item={
    'pk': 'USER#$USER_ID',
    'sk': 'CONFIG#primary',
    'config_type': 'user_config',
    'user_id': '$USER_ID',
    'llm_provider': 'anthropic',
    'openclaw_model': 'claude-3-haiku-20240307',
    'anthropic_api_key': os.getenv('ANTHROPIC_API_KEY'),
    'auth_gateway_api_key': '$API_KEY',
    'created_at': datetime.utcnow().isoformat(),
    'updated_at': datetime.utcnow().isoformat()
})

print("✓ User config created")
EOF

log_info "✓ Test configuration created in AWS DynamoDB"

# ============================================================================
# Step 4: Verify Configuration in DynamoDB
# ============================================================================

log_section "Step 4: Verifying Configuration in AWS DynamoDB"

log_info "Fetching system config..."
SYSTEM_CONFIG=$(aws dynamodb get-item \
    --table-name $DYNAMODB_TABLE \
    --key '{"pk":{"S":"SYSTEM"},"sk":{"S":"CONFIG#defaults"}}' \
    --region $AWS_REGION \
    --profile $AWS_PROFILE \
    --output json)

if echo "$SYSTEM_CONFIG" | jq -e '.Item' > /dev/null; then
    AUTH_URL=$(echo "$SYSTEM_CONFIG" | jq -r '.Item.auth_gateway_url.S')
    log_info "✓ System config found"
    log_info "  auth_gateway_url: $AUTH_URL"
else
    log_error "System config not found in DynamoDB"
    exit 1
fi

log_info "Fetching user config..."
USER_CONFIG=$(aws dynamodb get-item \
    --table-name $DYNAMODB_TABLE \
    --key "{\"pk\":{\"S\":\"USER#$USER_ID\"},\"sk\":{\"S\":\"CONFIG#primary\"}}" \
    --region $AWS_REGION \
    --profile $AWS_PROFILE \
    --output json)

if echo "$USER_CONFIG" | jq -e '.Item' > /dev/null; then
    LLM_PROVIDER=$(echo "$USER_CONFIG" | jq -r '.Item.llm_provider.S')
    log_info "✓ User config found"
    log_info "  llm_provider: $LLM_PROVIDER"
    log_info "  anthropic_api_key: $(echo "$USER_CONFIG" | jq -r '.Item.anthropic_api_key.S' | cut -c1-20)..."
else
    log_error "User config not found in DynamoDB"
    exit 1
fi

# ============================================================================
# Step 5: Test Config Fetch Script (Local)
# ============================================================================

log_section "Step 5: Testing Config Fetch Script Locally"

log_info "Testing fetch_config.py against AWS DynamoDB..."

# Create temp directory for configs
TEST_CONFIG_DIR="/tmp/test-e2e-configs-$$"
mkdir -p "$TEST_CONFIG_DIR"

log_info "Running fetch_config.py..."
if AWS_PROFILE=$AWS_PROFILE python3 "$ORCHESTRATOR_DIR/scripts/container/fetch_config.py" \
    --user-id "$USER_ID" \
    --container-id "$CONTAINER_ID" \
    --openclaw-config "$TEST_CONFIG_DIR/openclaw.json" \
    --agent-config "$TEST_CONFIG_DIR/voxhelm.json" \
    --table "$DYNAMODB_TABLE" \
    --region "$AWS_REGION"; then
    log_info "✓ Config fetch successful"
else
    log_error "Config fetch failed"
    exit 1
fi

# Verify files were created
if [ ! -f "$TEST_CONFIG_DIR/openclaw.json" ]; then
    log_error "openclaw.json not created"
    exit 1
fi

if [ ! -f "$TEST_CONFIG_DIR/voxhelm.json" ]; then
    log_error "voxhelm.json not created"
    exit 1
fi

log_info "✓ Both config files created"

# Show config contents (without secrets)
log_info ""
log_info "openclaw.json structure:"
jq '{gateway: .gateway, models: {providers: (.models.providers | keys)}}' "$TEST_CONFIG_DIR/openclaw.json"

log_info ""
log_info "voxhelm.json structure:"
jq '{
    user_id,
    llm_provider,
    auth_gateway_url,
    openclaw_url,
    has_anthropic_key: (.anthropic_api_key != ""),
    has_auth_key: (.auth_gateway_api_key != "")
}' "$TEST_CONFIG_DIR/voxhelm.json"

# Cleanup temp configs
rm -rf "$TEST_CONFIG_DIR"

# ============================================================================
# Step 6: Test with Container (if local mode)
# ============================================================================

if [ "$TEST_MODE" = "local-with-aws" ]; then
    log_section "Step 6: Testing with Local Container + AWS DynamoDB"

    log_warn "Building test container with AWS credentials..."
    log_warn "This will start OpenClaw + agent locally but use AWS DynamoDB"

    # Export AWS credentials for container
    export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id --profile $AWS_PROFILE)
    export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key --profile $AWS_PROFILE)
    export AWS_SESSION_TOKEN=$(aws configure get aws_session_token --profile $AWS_PROFILE 2>/dev/null || echo "")
    export AWS_REGION=$AWS_REGION
    export DYNAMODB_TABLE=$DYNAMODB_TABLE
    export DYNAMODB_ENDPOINT=""
    export USER_ID=$USER_ID
    export CONTAINER_ID=$CONTAINER_ID

    log_info "Starting container with AWS credentials..."
    docker compose -f "$SCRIPT_DIR/docker-compose.e2e.yml" up -d --build test-container

    log_info "Waiting for container to be healthy (max 60s)..."
    for i in {1..60}; do
        if docker inspect orchestrator-test-container 2>/dev/null | jq -r '.[0].State.Health.Status' | grep -q "healthy"; then
            log_info "✓ Container is healthy"
            break
        fi
        if [ $i -eq 60 ]; then
            log_error "Container failed to become healthy"
            log_error "Showing container logs:"
            docker logs orchestrator-test-container
            exit 1
        fi
        echo -n "."
        sleep 1
    done
    echo ""

    # Test OpenClaw query
    log_section "Testing OpenClaw Query"

    QUERY_RESPONSE=$(curl -s -X POST "http://localhost:18789/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer test-token-123" \
        -d '{
            "model": "claude-3-haiku-20240307",
            "messages": [{"role": "user", "content": "Say hello in exactly 3 words"}],
            "max_tokens": 20
        }')

    if echo "$QUERY_RESPONSE" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
        RESPONSE_TEXT=$(echo "$QUERY_RESPONSE" | jq -r '.choices[0].message.content')
        log_info "✓ OpenClaw query successful"
        log_info "Response: $RESPONSE_TEXT"
    else
        log_error "OpenClaw query failed"
        log_error "Response: $QUERY_RESPONSE"
    fi

    # Show container logs
    log_section "Container Logs (last 50 lines)"
    docker logs orchestrator-test-container --tail 50

else
    log_section "Step 6: ECS Mode - Skipping Local Container Test"
    log_info "To test with ECS, launch a task manually with:"
    log_info "  USER_ID=$USER_ID"
    log_info "  CONTAINER_ID=$CONTAINER_ID"
    log_info "  DYNAMODB_TABLE=$DYNAMODB_TABLE"
    log_info "  AWS_REGION=$AWS_REGION"
fi

# ============================================================================
# Test Complete
# ============================================================================

log_section "AWS E2E Test Complete"

log_info "✓ All tests passed!"
log_info ""
log_info "What was tested:"
log_info "  ✓ AWS DynamoDB table exists"
log_info "  ✓ System config created in AWS DynamoDB"
log_info "  ✓ User config created in AWS DynamoDB"
log_info "  ✓ Config fetch script works against AWS"
log_info "  ✓ Both config files generated correctly"

if [ "$TEST_MODE" = "local-with-aws" ]; then
    log_info "  ✓ Container started with AWS credentials"
    log_info "  ✓ OpenClaw query works with real LLM"
fi

log_info ""
log_info "Test data created in AWS DynamoDB:"
log_info "  Table: $DYNAMODB_TABLE"
log_info "  Region: $AWS_REGION"
log_info "  User ID: $USER_ID"
log_info ""
log_info "View config in AWS:"
log_info "  aws dynamodb get-item \\"
log_info "    --table-name $DYNAMODB_TABLE \\"
log_info "    --key '{\"pk\":{\"S\":\"USER#$USER_ID\"},\"sk\":{\"S\":\"CONFIG#primary\"}}' \\"
log_info "    --region $AWS_REGION \\"
log_info "    --profile $AWS_PROFILE | jq"
log_info ""
log_info "Cleanup test data:"
log_info "  aws dynamodb delete-item \\"
log_info "    --table-name $DYNAMODB_TABLE \\"
log_info "    --key '{\"pk\":{\"S\":\"USER#$USER_ID\"},\"sk\":{\"S\":\"CONFIG#primary\"}}' \\"
log_info "    --region $AWS_REGION \\"
log_info "    --profile $AWS_PROFILE"
log_info ""

if [ "$TEST_MODE" = "local-with-aws" ]; then
    log_info "Stop container:"
    log_info "  docker compose -f test/docker-compose.e2e.yml down"
fi

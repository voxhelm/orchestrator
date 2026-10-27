# AWS End-to-End Test Setup

This guide explains how to run the end-to-end test against AWS infrastructure.

## AWS Architecture

All services are running in AWS:

```
User/Script
    ↓
Auth Gateway (Lambda)
    ↓
Orchestrator (Lambda)
    ↓
DynamoDB (openclaw-containers table)
    ↓
ECS Fargate (your-cluster cluster)
    ↓
Container (openclaw-agent)
```

## AWS Resources

| Service | Type | Endpoint/Name |
|---------|------|---------------|
| Auth Gateway | Lambda | `https://your-auth-gateway.execute-api.your-region.amazonaws.com` |
| Orchestrator | Lambda | `https://your-api-id.execute-api.your-region.amazonaws.com` |
| DynamoDB | Table | `openclaw-containers` in `ap-southeast-2` |
| ECS | Fargate | Cluster: `your-cluster`, Task Def: `openclaw-agent-dev` |

## Prerequisites

### 1. AWS Credentials

Ensure you have AWS credentials configured:

```bash
# Option 1: Use AWS CLI profile
export AWS_PROFILE=default
aws sts get-caller-identity  # Verify credentials work

# Option 2: Use explicit credentials
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=ap-southeast-2
```

### 2. Required Permissions

Your IAM user/role needs:
- `dynamodb:GetItem` - Read from DynamoDB
- `dynamodb:PutItem` - Write to DynamoDB
- `dynamodb:Query` - Query DynamoDB indexes
- `dynamodb:Scan` - Scan DynamoDB (for listing containers)
- `ecs:RunTask` - Launch ECS tasks
- `ecs:DescribeTasks` - Get task status
- `ecs:StopTask` - Stop tasks

### 3. Install Python Dependencies

```bash
pip install requests python-dotenv boto3
```

## Configuration

### Environment Variables

Create a `.env` file or export these:

```bash
# AWS Configuration (required)
export AWS_PROFILE=default
export AWS_DEFAULT_REGION=ap-southeast-2

# Service URLs (defaults to AWS endpoints)
export AUTH_GATEWAY_URL=https://your-auth-gateway.execute-api.your-region.amazonaws.com
export ORCHESTRATOR_URL=https://your-api-id.execute-api.your-region.amazonaws.com

# DynamoDB Configuration
export CONTAINERS_TABLE=openclaw-containers
export DYNAMODB_REGION=ap-southeast-2
# DO NOT set DYNAMODB_ENDPOINT - let boto3 use real AWS

# Optional: Override ECS cluster
export ECS_CLUSTER_NAME=your-cluster
export ECS_TASK_DEFINITION=openclaw-agent-dev
```

### Verify Configuration

```bash
# Check AWS credentials
aws sts get-caller-identity

# Check DynamoDB table exists
aws dynamodb describe-table --table-name openclaw-containers

# Check ECS cluster
aws ecs describe-clusters --clusters your-cluster

# Test auth-gateway
curl https://your-auth-gateway.execute-api.your-region.amazonaws.com/health

# Test orchestrator
curl https://your-api-id.execute-api.your-region.amazonaws.com/health
```

## Running the Test

### Basic Run

```bash
python scripts/test_end_to_end_flow.py
```

### With Custom Configuration

```bash
# Use specific AWS profile
AWS_PROFILE=my-profile python scripts/test_end_to_end_flow.py

# Use explicit credentials
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... python scripts/test_end_to_end_flow.py
```

## What the Test Does

### Step 1: Create User in Auth Gateway (AWS Lambda)
- Calls `POST /users` on auth-gateway Lambda
- Receives back: `user_id` (UUID) and `api_key`
- Lambda writes user to DynamoDB (auth-gateway table)

### Step 2: Validate API Key
- Calls `GET /auth` with API key
- Auth Gateway Lambda validates and returns `user_id`

### Step 3: Create Container via Orchestrator (AWS Lambda)
- Calls `POST /containers` on orchestrator Lambda
- Lambda writes config to DynamoDB (openclaw-containers table)
- Lambda triggers ECS Fargate task

### Step 4: Show DynamoDB Config
- Script queries DynamoDB directly
- Shows USER config: `pk=USER#{user_id}, sk=CONFIG#default`
- Shows SYSTEM config: `pk=SYSTEM, sk=CONFIG#defaults`

### Step 5: Show Container Environment Variables
- Shows what env vars ECS will pass to container:
  - `USER_ID` - User UUID
  - `CONFIG_NAME` - Config name (default)
  - `DYNAMODB_TABLE` - Table name
  - `DYNAMODB_REGION` - AWS region

### Step 6: Monitor Container Status
- Polls orchestrator for container status
- Waits for ECS task to reach RUNNING state
- Shows IP address, health status

## Expected Output

```
================================================================================
End-to-End Container Provisioning Test
================================================================================
ℹ Started at: 2026-04-07T12:00:00+00:00

Environment Variables:
{
  "AUTH_GATEWAY_URL": "https://your-auth-gateway.execute-api.your-region.amazonaws.com",
  "ORCHESTRATOR_URL": "https://your-api-id.execute-api.your-region.amazonaws.com",
  "DYNAMODB_ENDPOINT": "(AWS - no endpoint)",
  "DYNAMODB_TABLE": "openclaw-containers",
  "DYNAMODB_REGION": "ap-southeast-2",
  "AWS_PROFILE": "default",
  "AWS_REGION": "ap-southeast-2"
}

[Step 1] Create user in auth-gateway
→ POST https://your-auth-gateway.execute-api.your-region.amazonaws.com/users
...
✓ User created successfully
ℹ User ID (UUID): abc123-def456-...
ℹ API Key: ak_live_...

[Step 2] Validate API key with auth-gateway
...
✓ API key validated

[Step 3] Create container via orchestrator
→ POST https://your-api-id.execute-api.your-region.amazonaws.com/containers
...
✓ Container creation initiated
ℹ Container ID: oc-a1b2c3d4

[Step 4] Show configuration stored in DynamoDB
ℹ Using AWS DynamoDB in region: ap-southeast-2
✓ Found user config in DynamoDB
...

[Step 5] Show environment variables for container
Container Environment Variables:
{
  "USER_ID": "abc123-def456-...",
  "CONTAINER_ID": "oc-a1b2c3d4",
  "CONFIG_NAME": "default",
  "DYNAMODB_TABLE": "openclaw-containers",
  "DYNAMODB_REGION": "ap-southeast-2"
}

[Step 6] Monitor container status
ℹ Status: PENDING, Health: UNKNOWN, IP: pending
...
ℹ Status: RUNNING, Health: HEALTHY, IP: 10.0.1.45
✓ Container is running!

================================================================================
Test Summary
================================================================================
✓ User created: test-user-...@example.com (UUID: abc123-...)
✓ API key generated: ak_live_...
✓ Container requested: oc-a1b2c3d4
ℹ Container status: RUNNING

AWS Resources Used:
  • Auth Gateway:  https://your-auth-gateway...
  • Orchestrator:  https://your-api-id...
  • DynamoDB:      openclaw-containers (region: ap-southeast-2)
  • ECS Cluster:   your-cluster
  • Container:     oc-a1b2c3d4

✓ Test completed successfully!
```

## Troubleshooting

### "Could not query DynamoDB directly"

Check AWS credentials:
```bash
aws sts get-caller-identity
aws dynamodb list-tables
```

### "Auth service timeout" / "Auth service error"

Check auth-gateway is accessible:
```bash
curl https://your-auth-gateway.execute-api.your-region.amazonaws.com/health
```

### "Failed to create container"

1. Check orchestrator is accessible:
```bash
curl https://your-api-id.execute-api.your-region.amazonaws.com/health
```

2. Check ECS cluster exists:
```bash
aws ecs describe-clusters --clusters your-cluster
```

3. Check task definition:
```bash
aws ecs describe-task-definition --task-definition openclaw-agent-dev
```

### Container stuck in PENDING

1. Check ECS task status:
```bash
aws ecs list-tasks --cluster your-cluster
aws ecs describe-tasks --cluster your-cluster --tasks <task-arn>
```

2. Check CloudWatch logs:
```bash
aws logs tail /ecs/openclaw-agent-dev --follow
```

### "No user config found in DynamoDB"

This is expected on first run. The orchestrator creates default config when you create a container.

## Cleanup

After testing, clean up resources:

```bash
# Stop container
curl -X DELETE \
  https://your-api-id.execute-api.your-region.amazonaws.com/containers/oc-... \
  -H "Authorization: Bearer your-api-key"

# Or use cleanup script
python scripts/delete_containers.py --user-id USER_UUID --all --yes
```

## Next Steps

Once the test passes:

1. **Check container logs**:
   ```bash
   python scripts/get_logs.py oc-a1b2c3d4 --user-id USER_UUID
   ```

2. **Verify config was fetched**:
   Look for lines like:
   ```
   [config] Fetching configuration from DynamoDB...
   [config] ✓ Configuration fetched from DynamoDB
   ```

3. **Connect to container**:
   ```bash
   python scripts/exec_shell.py oc-a1b2c3d4 --user-id USER_UUID

   # Inside container:
   cat ~/.openclaw/openclaw.json
   cat ~/.voxhelm/voxhelm.json
   ```

4. **Test agent functionality**:
   The container should now be ready to accept agent registrations and handle voice calls.

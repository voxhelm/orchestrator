# Orchestrator Management Scripts

Python scripts for managing openclaw-agent containers.

## Prerequisites

```bash
# Install dependencies
make install-dev

# Or manually
pip install boto3 tabulate
```

## Scripts

### 1. Launch Container

Create a new openclaw-agent container via the orchestrator API.

```bash
# Launch with defaults (production)
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN

# Launch with custom name
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN --name my-agent

# Launch with configuration
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN \
  --config '{"memory": 512, "cpu": 256}'

# Launch and wait for container to become healthy
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN --wait

# Use local development environment
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN --local

# Use custom URL
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN \
  --url https://custom-api.example.com
```

**Options:**
- `--user-id` - User ID (required for authentication)
- `--token` - Authentication token (required)
- `--name` - Optional container name
- `--config` - Optional configuration as JSON string
- `--env` - Environment (dev/prod), default: dev
- `--local` - Use local development URL (localhost:8000)
- `--url` - Custom base URL (overrides --env and --local)
- `--wait` - Wait for container to become healthy
- `--wait-timeout` - Health check timeout in seconds, default: 300

**Note:** Token format is `user_id:token_string` (minimum 20 characters total).

### 2. List Containers (DynamoDB)

List all containers the orchestrator thinks it's managing.

```bash
# List all containers
python scripts/list_containers.py

# List containers for specific user
python scripts/list_containers.py --user-id USER_123

# Use different environment
python scripts/list_containers.py --env prod
```

**Options:**
- `--env` - Environment (dev/prod), default: dev
- `--user-id` - Filter by specific user ID
- `--profile` - AWS profile name, default: default
- `--region` - AWS region, default: ap-southeast-2

### 3. List ECS Tasks

List all actual ECS tasks running for openclaw-agent.

```bash
# List all ECS tasks
python scripts/list_ecs_tasks.py

# Use different environment
python scripts/list_ecs_tasks.py --env prod
```

**Options:**
- `--env` - Environment (dev/prod), default: dev
- `--profile` - AWS profile name, default: default
- `--region` - AWS region, default: ap-southeast-2
- `--cluster` - ECS cluster name, default: your-cluster

### 4. Get Logs

Fetch CloudWatch logs for a specific container.

```bash
# Get logs by container ID
python scripts/get_logs.py oc-abc12345 --user-id USER_123

# Get logs by task ID
python scripts/get_logs.py --task-id abc123def456

# Follow logs in real-time
python scripts/get_logs.py oc-abc12345 --user-id USER_123 --follow

# Show logs from last hour
python scripts/get_logs.py oc-abc12345 --user-id USER_123 --since 60
```

**Options:**
- `--user-id` - User ID (required with container ID)
- `--task-id` - Task ID (alternative to container ID)
- `--env` - Environment (dev/prod), default: dev
- `--follow` / `-f` - Follow logs in real-time
- `--since` - Show logs from last N minutes, default: 30
- `--profile` - AWS profile name, default: default
- `--region` - AWS region, default: ap-southeast-2

### 5. Execute Shell

Get an interactive shell on a running container.

```bash
# Connect by container ID
python scripts/exec_shell.py oc-abc12345 --user-id USER_123

# Connect by task ARN
python scripts/exec_shell.py --task-arn arn:aws:ecs:...

# Run custom command
python scripts/exec_shell.py oc-abc12345 --user-id USER_123 --command "ls -la"
```

**Options:**
- `--user-id` - User ID (required with container ID)
- `--task-arn` - Task ARN (alternative to container ID)
- `--env` - Environment (dev/prod), default: dev
- `--command` - Command to execute, default: /bin/bash
- `--profile` - AWS profile name, default: default
- `--region` - AWS region, default: ap-southeast-2
- `--cluster` - ECS cluster name, default: your-cluster
- `--container` - Container name, default: openclaw-agent

**Prerequisites:**
- ECS exec must be enabled on the task
- Session Manager plugin must be installed: [Installation Guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

### 6. Delete Containers

Stop ECS tasks and remove DynamoDB records.

```bash
# Delete single container
python scripts/delete_containers.py oc-abc12345 --user-id USER_123

# Delete multiple containers
python scripts/delete_containers.py oc-abc12345 oc-def67890 --user-id USER_123

# Delete all containers for a user
python scripts/delete_containers.py --user-id USER_123 --all

# Delete all STOPPED containers
python scripts/delete_containers.py --user-id USER_123 --status STOPPED

# Dry run (show what would be deleted)
python scripts/delete_containers.py --user-id USER_123 --all --dry-run

# Skip confirmation
python scripts/delete_containers.py oc-abc12345 --user-id USER_123 --yes
```

**Options:**
- `--user-id` - User ID (required)
- `--all` - Delete all containers for the user
- `--status` - Delete containers with specific status
- `--env` - Environment (dev/prod), default: dev
- `--dry-run` - Show what would be deleted without deleting
- `--yes` / `-y` - Skip confirmation prompt
- `--profile` - AWS profile name, default: default
- `--region` - AWS region, default: ap-southeast-2
- `--cluster` - ECS cluster name, default: your-cluster

## Common Workflows

### Launch and manage a container

```bash
# 1. Launch a new container
python scripts/launch_container.py --user-id USER_123 --token YOUR_TOKEN --wait

# 2. Get the container logs
python scripts/get_logs.py oc-abc12345 --user-id USER_123

# 3. Get a shell to debug
python scripts/exec_shell.py oc-abc12345 --user-id USER_123

# 4. When done, delete it
python scripts/delete_containers.py oc-abc12345 --user-id USER_123 --yes
```

### Check container status

```bash
# 1. List all containers in DynamoDB
python scripts/list_containers.py

# 2. List actual running tasks in ECS
python scripts/ecs_tasks.py list

# 3. Compare to find discrepancies
```

### Debug a container

```bash
# 1. Get the logs
python scripts/get_logs.py oc-abc12345 --user-id USER_123 --since 60

# 2. Get a shell if needed
python scripts/exec_shell.py oc-abc12345 --user-id USER_123

# 3. Check health status (from list output)
python scripts/list_containers.py --user-id USER_123
```

### Clean up stopped containers

```bash
# 1. Check what would be deleted
python scripts/delete_containers.py --user-id USER_123 --status STOPPED --dry-run

# 2. Delete them
python scripts/delete_containers.py --user-id USER_123 --status STOPPED
```

### Monitor logs in real-time

```bash
# Follow logs for a specific container
python scripts/get_logs.py oc-abc12345 --user-id USER_123 --follow
```

## ECS Task Management Scripts

### 7. ECS Tasks (`ecs_tasks.py`)

Single entry point for interrogating and cleaning up ECS tasks. Replaces `list_ecs_tasks.py`, `delete_all_running_tasks.py`, and `cleanup_tasks.py`.

**Common options (all subcommands):**
- `--env` - Environment (dev/prod), default: dev
- `--cluster` - ECS cluster name (default: voxhelm-{env})
- `--profile` - AWS profile, default: default
- `--region` - AWS region, default: ap-southeast-2

#### `list` — show all tasks in the cluster

```bash
python scripts/ecs_tasks.py list
python scripts/ecs_tasks.py list --env prod
```

Displays a table of task ID, status, container ID, user ID, IP address, and start time, plus a count by status.

#### `stop-all` — stop all running tasks

```bash
# Dry run — see what would be stopped
python scripts/ecs_tasks.py stop-all --dry-run

# Stop all running tasks
python scripts/ecs_tasks.py stop-all

# Stop all running tasks and remove DynamoDB records
python scripts/ecs_tasks.py stop-all --cleanup-db

# Prod environment
python scripts/ecs_tasks.py stop-all --env prod --cleanup-db
```

**Extra options:**
- `--cleanup-db` - Also delete container records from DynamoDB
- `--dry-run` - Show what would happen without making changes

#### `cleanup` — remove PENDING and FAILED tasks

Scans DynamoDB for PENDING and FAILED containers, stops any still-running ECS tasks, and deletes the DynamoDB records.

```bash
# Dry run
python scripts/ecs_tasks.py cleanup --dry-run

# Remove all pending/failed containers
python scripts/ecs_tasks.py cleanup

# Prod environment
python scripts/ecs_tasks.py cleanup --env prod
```

**Extra options:**
- `--dry-run` - Show what would happen without making changes

### 8. Inspect Agent

Diagnose a failed or stuck container by looking up its DynamoDB record, ECS task status, Lambda invocations, and CloudWatch logs in one shot. Accepts either a full UUID or an `oc-` container ID.

```bash
# Inspect by container ID
python scripts/inspect_agent.py oc-e20ac9f1

# Inspect by full ECS task UUID
python scripts/inspect_agent.py e20ac9f1-2d3a-462c-9a37-205779ac0e0a

# Include CloudWatch logs (last 60 minutes)
python scripts/inspect_agent.py oc-e20ac9f1 --logs

# Include logs with custom window
python scripts/inspect_agent.py oc-e20ac9f1 --logs --since 120

# Use prod environment
python scripts/inspect_agent.py oc-e20ac9f1 --env prod
```

**Options:**
- `--env` - Environment (dev/prod), default: dev
- `--profile` - AWS profile name
- `--region` - AWS region, default: ap-southeast-2
- `--logs` - Fetch CloudWatch logs
- `--since` - Log window in minutes, default: 60
- `--cluster` - ECS cluster name (default: voxhelm-{env})

**What it shows:**
1. DynamoDB record (status, IP, task ARN, timestamps)
2. ECS task status (last status, exit code, stopped reason)
3. Orchestrator Lambda invocations around creation time
4. CloudWatch container logs (if `--logs` passed)

## Configuration Scripts

### 10. Load Defaults

Populate DynamoDB with system-wide and user-specific default configuration to bootstrap the orchestrator.

```bash
# Load system defaults only
python scripts/load_defaults.py --system

# Load system + user defaults
python scripts/load_defaults.py --system --user-id YOUR_USER_ID \
  --auth-gateway-url https://your-auth-gateway.execute-api.your-region.amazonaws.com \
  --openclaw-url http://localhost:18789 \
  --openclaw-token test-token-123 \
  --llm-provider anthropic \
  --openclaw-model claude-3-haiku-20240307 \
  --auth-gateway-api-key YOUR_API_KEY \
  --anthropic-api-key sk-ant-...

# Verify existing configs
python scripts/load_defaults.py --verify --user-id YOUR_USER_ID
```

**Options:**
- `--system` - Load system-wide defaults
- `--user-id` - User ID for user-specific config
- `--auth-gateway-url` - Auth gateway endpoint
- `--openclaw-url` - OpenClaw gateway URL
- `--openclaw-token` - Shared OpenClaw token
- `--llm-provider` - LLM provider (anthropic/openai/openrouter)
- `--openclaw-model` - Default model
- `--auth-gateway-api-key` - User's auth gateway API key
- `--anthropic-api-key` / `--openai-api-key` / `--openrouter-api-key` - Provider keys
- `--verify` - Print existing configs without writing
- `--env` - Environment (dev/prod), default: dev
- `--profile` - AWS profile, default: default
- `--region` - AWS region, default: ap-southeast-2

### 11. Verify AWS Config

Quick pre-flight check before running tests. Verifies AWS credentials, auth-gateway accessibility, orchestrator endpoint, DynamoDB table, and ECS cluster.

```bash
python scripts/verify_aws_config.py
```

Reads configuration from `.env` in the project root.

### 12. Setup Test Config

Create test system and user configs in DynamoDB for local or AWS E2E testing.

```bash
# Local DynamoDB
python scripts/setup_test_config.py \
  --user-id test-user-123 \
  --anthropic-key sk-ant-...

# AWS DynamoDB
python scripts/setup_test_config.py \
  --user-id test-user-123 \
  --anthropic-key sk-ant-... \
  --endpoint https://dynamodb.ap-southeast-2.amazonaws.com
```

## Setup Scripts

### 13. Setup and Test (`setup_and_test.sh`)

Bootstraps a development environment: verifies the API key with auth-gateway, creates system and user configs in DynamoDB, and runs a test container creation.

```bash
bash scripts/setup_and_test.sh
```

### 14. Auth-Gateway Integration Test (`test_auth_gateway_integration.sh`)

Smoke tests the orchestrator's auth-gateway integration — creates a user, validates the API key, and creates a container.

```bash
bash scripts/test_auth_gateway_integration.sh
```

### 15. EventBridge Rule Setup (`setup_eventbridge_rule.sh`)

Creates the EventBridge rule that triggers the orchestrator Lambda on ECS task state changes (RUNNING/STOPPED), enabling DynamoDB status updates.

```bash
bash scripts/setup_eventbridge_rule.sh
bash scripts/test_eventbridge_rule.sh  # Verify the rule fires
```

## Testing Scripts

### test_end_to_end_flow.py

End-to-end test script that demonstrates the complete user-to-container provisioning flow with verbose logging.

**For detailed AWS setup instructions, see [AWS_TEST_SETUP.md](AWS_TEST_SETUP.md)**

```bash
# AWS Configuration (all services in AWS)
export AWS_PROFILE=default
export AWS_DEFAULT_REGION=ap-southeast-2

# Run with AWS Lambda endpoints
python scripts/test_end_to_end_flow.py

# Or with explicit configuration
AUTH_GATEWAY_URL=https://your-auth-gateway.execute-api.your-region.amazonaws.com \
ORCHESTRATOR_URL=https://your-api-id.execute-api.your-region.amazonaws.com \
AWS_PROFILE=default \
python scripts/test_end_to_end_flow.py
```

**What it does:**
1. Creates a user in auth-gateway with a unique email
2. Gets an API key back from user creation
3. Validates the API key with auth-gateway
4. Creates a container via the orchestrator using the API key
5. Shows DynamoDB config that gets stored for the user
6. Shows environment variables that will be passed to the container
7. Monitors container status until it reaches RUNNING state
8. Prints verbose logs of all API calls, requests, and responses

**Features:**
- ✅ Color-coded output (success, info, warnings, errors)
- ✅ Full request/response dumps with headers and bodies
- ✅ Masked sensitive values (API keys shown as `xxx...xxx`)
- ✅ Step-by-step progress tracking
- ✅ DynamoDB config inspection
- ✅ Container environment variable preview
- ✅ Explanation of what happens in the container after launch

**Environment Variables:**
- `AUTH_GATEWAY_URL` - Auth gateway Lambda URL (default: AWS Lambda endpoint)
- `ORCHESTRATOR_URL` - Orchestrator Lambda URL (default: AWS Lambda endpoint)
- `DYNAMODB_TABLE` - DynamoDB table name (default: openclaw-containers)
- `DYNAMODB_REGION` - AWS region (default: ap-southeast-2)
- `AWS_PROFILE` - AWS CLI profile to use (default: default)
- `AWS_DEFAULT_REGION` - AWS region (default: ap-southeast-2)
- `AWS_ACCESS_KEY_ID` - Explicit AWS credentials (optional, uses profile if not set)
- `AWS_SECRET_ACCESS_KEY` - Explicit AWS credentials (optional, uses profile if not set)

**Note:** Do NOT set `DYNAMODB_ENDPOINT` for AWS - it will automatically use AWS DynamoDB.

**Use Cases:**
- Testing the complete user creation → container provisioning flow
- Debugging auth-gateway and orchestrator integration
- Understanding what config gets transferred to containers
- Validating DynamoDB config storage
- Demonstrating the system to new developers

### test_config_api_only.py

Smoke tests the Config API endpoints (create, list, get) without spinning up containers or AWS ECS resources. Requires the orchestrator to be running.

```bash
python scripts/test_config_api_only.py
```

### test_integration.py / test_scripts.py

Validate the management scripts themselves — argument parsing, error handling, and basic behaviour without making real AWS API calls.

```bash
python scripts/test_integration.py
python scripts/test_scripts.py
```

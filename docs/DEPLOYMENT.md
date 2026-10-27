# Deployment Guide

> **Note for open-source readers:** this guide describes how the maintainers
> deploy the hosted service. It refers throughout to a sibling
> `infrastructure` repository which is private, because it contains
> account-specific deployment configuration. You do not need it to build, test,
> or run the orchestrator locally — see the [README](../README.md) and
> [CONTRIBUTING.md](../CONTRIBUTING.md) for that.

## Prerequisites

- AWS CLI configured with `--profile default`
- The private `infrastructure` repository checked out alongside this one
- Docker with ARM64 support (for Lambda)
- Terraform 1.5+
- Access to ECR repository: `123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator`

## Build & Deploy

### 1. Build Docker Image

```bash
# Build ARM64 Lambda image
IMAGE_TAG=$(git rev-parse --short HEAD)
docker buildx build \
  --provenance=false \
  --sbom=false \
  --load \
  --platform linux/arm64 \
  --build-arg GIT_COMMIT=${IMAGE_TAG} \
  -f Dockerfile.lambda \
  -t 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest \
  .

# Login to ECR
aws --profile default ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin \
  123456789012.dkr.ecr.ap-southeast-2.amazonaws.com

# Push image
docker push 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest
```

### 2. Deploy Infrastructure

```bash
cd ../infrastructure/infra/environments/dev

# Initialize Terraform
terraform init

# Plan deployment
terraform plan -var="orchestrator_image_tag=dev-latest"

# Apply changes
terraform apply -auto-approve -var="orchestrator_image_tag=dev-latest"
```

### 3. Verify Deployment

```bash
# Get API URL from Terraform outputs
terraform output orchestrator_url

# Test health endpoint
curl $(terraform output -raw orchestrator_url)/health

# Create test container
curl -X POST $(terraform output -raw orchestrator_url)/containers \
  -H "Authorization: Bearer {USER_ID}:{YOUR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Update Deployment

### Update Code Only

```bash
# Rebuild and push image
IMAGE_TAG=$(git rev-parse --short HEAD)
docker buildx build --provenance=false --sbom=false --load --platform linux/arm64 \
  --build-arg GIT_COMMIT=${IMAGE_TAG} -f Dockerfile.lambda \
  -t 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest .
docker push 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest

# Update Lambda function
aws --profile default lambda update-function-code \
  --function-name orchestrator-dev \
  --region ap-southeast-2 \
  --image-uri 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest
```

### Update Infrastructure

```bash
cd ../infrastructure/infra/environments/dev
terraform apply -auto-approve -var="orchestrator_image_tag=dev-latest"
```

## Monitoring & Logs

### View Lambda Logs

```bash
# Tail recent logs
aws --profile default logs tail /aws/lambda/orchestrator-dev \
  --region ap-southeast-2 \
  --since 10m \
  --follow

# Filter for errors
aws --profile default logs tail /aws/lambda/orchestrator-dev \
  --region ap-southeast-2 \
  --since 1h \
  --filter-pattern "ERROR"
```

### View Container Logs

```bash
# Get container details from API
CONTAINER_ID="{CONTAINER_ID}"
curl https://your-api-id.execute-api.your-region.amazonaws.com/containers/${CONTAINER_ID} \
  -H "Authorization: Bearer {USER_ID}:{YOUR_TOKEN}"

# Get task ARN from DynamoDB
USER_ID="{USER_ID}"
TASK_ARN=$(aws --profile default dynamodb get-item \
  --table-name openclaw-containers-dev \
  --region ap-southeast-2 \
  --key '{"pk":{"S":"USER#'${USER_ID}'"},"sk":{"S":"CONTAINER#'${CONTAINER_ID}'"}}' \
  --query 'Item.task_arn.S' \
  --output text)

# Extract task ID from ARN
TASK_ID=$(echo $TASK_ARN | rev | cut -d'/' -f1 | rev)

# View container logs (openclaw-agent)
aws --profile default logs tail /ecs/openclaw-agent-dev \
  --region ap-southeast-2 \
  --since 30m \
  --format short \
  --filter-pattern "$TASK_ID"

# Follow logs in real-time
aws --profile default logs tail /ecs/openclaw-agent-dev \
  --region ap-southeast-2 \
  --follow \
  --format short
```

### Check ECS Task Status

```bash
# List running tasks
aws --profile default ecs list-tasks \
  --cluster your-cluster \
  --region ap-southeast-2 \
  --desired-status RUNNING

# Describe specific task
aws --profile default ecs describe-tasks \
  --cluster your-cluster \
  --region ap-southeast-2 \
  --tasks $TASK_ARN \
  --query 'tasks[0].{status:lastStatus,ip:containers[0].networkInterfaces[0].privateIpv4Address}'
```

### Query DynamoDB

```bash
# Scan all containers
aws --profile default dynamodb scan \
  --table-name openclaw-containers-dev \
  --region ap-southeast-2

# Get specific container
aws --profile default dynamodb get-item \
  --table-name openclaw-containers-dev \
  --region ap-southeast-2 \
  --key '{"pk":{"S":"USER#{USER_ID}"},"sk":{"S":"CONTAINER#{CONTAINER_ID}"}}'
```

## Troubleshooting

### Lambda Permission Errors

If you see "Permission denied" errors, rebuild the Docker image with correct permissions:

```bash
# Dockerfile.lambda includes: RUN chmod -R 755 ${LAMBDA_TASK_ROOT}
docker buildx build --provenance=false --sbom=false --load --platform linux/arm64 \
  -f Dockerfile.lambda \
  -t 123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/orchestrator:dev-latest .
```

### ECS Task Won't Start

Check IAM permissions and network configuration:

```bash
# Verify subnet/security group IDs in Lambda env vars
aws --profile default lambda get-function-configuration \
  --function-name orchestrator-dev \
  --region ap-southeast-2 \
  --query 'Environment.Variables.{subnets:ECS_SUBNETS,sgs:ECS_SECURITY_GROUPS}'
```

### DynamoDB Status Not Updating

Currently, the orchestrator doesn't have EventBridge integration (Phase 2). Status updates happen:
- On container creation (PENDING)
- Manual status checks would need to query ECS

## Tailscale Setup

The orchestrator Lambda can connect outbound to a [Tailscale](https://tailscale.com) tailnet at cold-start. This is optional — if no environment variable is set, the Lambda starts without it.

### How key rotation works (no manual rotation needed)

The Lambda does **not** store a long-lived auth key. On every cold-start, `scripts/lambda-entrypoint.sh` reads a personal API key from SSM and calls the Tailscale API to mint a fresh single-use ephemeral auth key (5-minute TTL). This means:

- Auth keys never need rotation — they're generated on demand and discarded after use.
- The **personal API key** in SSM needs updating every 90 days (Tailscale's max expiry). This is a single `aws ssm put-parameter` call; no Terraform change or deployment is required.

### New environment checklist

> **Important:** Tailscale will not work in a new environment until all of these steps are completed. The Lambda starts without Tailscale if the SSM parameter is missing or invalid.

1. **Declare the tag** in your tailnet ACL (one-time per tailnet — skip if `tag:orchestrator` already exists):

   To use a different tag, set `TAILSCALE_TAG` on the Lambda (default `tag:orchestrator`). A tag missing from `tagOwners` makes key generation fail with `HTTP Error 400: Bad Request`.
   - Tailscale Admin Console → **Access Controls** → add to `tagOwners` (see below)
2. **Generate a personal API key** (Settings → Keys, set max 90-day expiry)
3. **Store it in SSM:**
   ```bash
   aws --profile <profile-for-your-account> ssm put-parameter \
     --region ap-southeast-2 \
     --name "/voxhelm/orchestrator/<env>/tailscale/api-key" \
     --type SecureString \
     --value "tskey-api-..." \
     --overwrite
   ```
4. **Apply Terraform** in `../infrastructure/infra/environments/<env>` — this creates the SSM slot (with a placeholder) and wires IAM + the env var to the Lambda
5. **Verify** — invoke the Lambda and check CloudWatch Logs (see Verify section below)

### Step 1 — Declare the tag in your ACL (one-time per tailnet)

In the [Tailscale Admin Console](https://login.tailscale.com/admin/acls) → **Access Controls**, add the tag and its owner to `tagOwners`. Tailscale requires the tag to exist before auth keys referencing it can be created.

```jsonc
{
  "tagOwners": {
    // existing tags...
    "tag:orchestrator": ["autogroup:admin"]
  },
  "acls": [
    // existing rules...
    {
      // Allow Lambda nodes to initiate connections to any tailnet host.
      // Tighten the dst list once you know the exact services to reach.
      "action": "accept",
      "src":    ["tag:orchestrator"],
      "dst":    ["*:*"]
    }
  ]
}
```

### Step 2 — Generate a personal API key

In the [Tailscale Admin Console](https://login.tailscale.com/admin/settings/keys) → **Settings → Keys**, generate a **personal API key** with the maximum 90-day expiry. This key is stored in SSM and used by the Lambda to mint ephemeral auth keys at runtime.

> **Note:** Do not use an OAuth client for this — OAuth clients do not have the `auth_keys` scope needed to generate auth keys via the API.

### Step 3 — Store the key in SSM

```bash
aws --profile <profile-for-your-account> ssm put-parameter \
  --region ap-southeast-2 \
  --name "/voxhelm/orchestrator/dev/tailscale/api-key" \
  --type SecureString \
  --value "tskey-api-..." \
  --overwrite
```

This step is **manual and not managed by Terraform** — Terraform creates the SSM slot with a placeholder but never overwrites a value that has already been set (`lifecycle { ignore_changes = [value] }`).

### Step 4 — Apply Terraform

The Tailscale SSM slot and IAM policy are declared in the `orchestrator`
Terraform module, which lives in the private `infrastructure` repository.
Run `terraform apply` from there:

```bash
cd ../infrastructure/infra/environments/dev
terraform init
terraform apply -var="orchestrator_image_tag=dev-latest"
```

This creates or confirms:
- The SSM parameter slot `/voxhelm/orchestrator/dev/tailscale/api-key`
- An IAM policy granting the Lambda `ssm:GetParameter` on that path
- The `TAILSCALE_API_KEY_SSM_PATH` environment variable on the Lambda function

After `apply`, Terraform outputs:

| Output | Description |
|---|---|
| `tailscale_api_key_ssm_path` | SSM path — the Lambda reads this parameter to generate auth keys |
| `lambda_tailscale_policy_arn` | IAM policy ARN — already attached to the Lambda execution role by the orchestrator module |

### Rotating the personal API key (every 90 days)

When your personal API key approaches expiry, generate a new one in the Tailscale Admin Console and update SSM:

```bash
aws --profile <profile-for-your-account> ssm put-parameter \
  --region ap-southeast-2 \
  --name "/voxhelm/orchestrator/dev/tailscale/api-key" \
  --type SecureString \
  --value "tskey-api-<new-key>" \
  --overwrite
```

No Terraform apply or Lambda redeployment required. The next cold-start picks up the new key automatically.

### Verify Tailscale connectivity

After deploying, invoke the Lambda and check CloudWatch Logs for the Tailscale startup messages:

```bash
aws --profile default logs tail /aws/lambda/orchestrator-dev \
  --region ap-southeast-2 \
  --since 5m \
  --filter-pattern "[tailscale]"
```

Expected output on a successful cold-start:
```
[tailscale] generating ephemeral auth key via Tailscale API
[tailscale] starting tailscaled (userspace networking)
[tailscale] connecting as orchestrator-lambda-orchestrator-dev
[tailscale] connected to tailnet
```

The node will appear in your Tailscale Admin Console device list tagged as `tag:orchestrator`. It disappears automatically when the Lambda execution environment is recycled.

---

## Clean Up

```bash
# Delete all containers (via API)
for id in $(curl -s https://your-api-id.execute-api.your-region.amazonaws.com/containers \
  -H "Authorization: Bearer {USER_ID}:{YOUR_TOKEN}" | jq -r '.[].container_id'); do
  curl -X DELETE https://your-api-id.execute-api.your-region.amazonaws.com/containers/$id \
    -H "Authorization: Bearer {USER_ID}:{YOUR_TOKEN}"
done

# Destroy infrastructure (from ../infrastructure/infra/environments/dev)
terraform destroy -auto-approve
```

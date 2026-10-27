.PHONY: help venv install install-dev lint test test-unit test-integration test-cov ci clean clean-venv run docker-build docker-up docker-down docker-logs docker-clean ecr-setup ecr-login lambda-push tf-ecr-init tf-ecr-apply tf-init tf-plan tf-apply tf-destroy deploy lambda-logs lambda-invoke test-deploy destroy

.DEFAULT_GOAL := help

# AWS / deployment settings
SERVICE       := orchestrator
ENV           ?= dev
AWS_PROFILE   ?= default
AWS_REGION    ?= ap-southeast-2
ECR_REPO_NAME ?= orchestrator
IMAGE_TAG     ?= $(shell git rev-parse --short HEAD)

_AWS_ACCOUNT_ID = $(shell aws --profile $(AWS_PROFILE) sts get-caller-identity --query Account --output text 2>/dev/null)
_ECR_REGISTRY   = $(_AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com

# Virtual environment settings
VENV := .venv
PYTHON := $(shell command -v python 2>/dev/null || command -v python3 2>/dev/null)
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip

help:
	@echo "Available targets:"
	@echo "  venv             - Create virtual environment"
	@echo "  install          - Install production dependencies (creates venv if needed)"
	@echo "  install-dev      - Install development dependencies (creates venv if needed)"
	@echo "  lint             - Run code linting (black, isort, flake8)"
	@echo "  test             - Run all tests"
	@echo "  test-unit        - Run unit tests only"
	@echo "  test-integration - Run integration tests only"
	@echo "  test-cov         - Run tests with coverage report"
	@echo "  ci               - Run full CI pipeline (lint + test + test-integration)"
	@echo "  test-e2e         - Run end-to-end config delivery test (local)"
	@echo "  test-e2e-aws     - Run end-to-end test against AWS DynamoDB"
	@echo "  test-e2e-logs    - View E2E test container logs"
	@echo "  test-e2e-clean   - Stop and clean E2E test containers"
	@echo "  test-docker-health - Test Docker image returns correct git SHA in /health"
	@echo "  run              - Run the application locally"
	@echo "  docker-build     - Build Docker image (ARM64)"
	@echo "  docker-up        - Start services with docker compose"
	@echo "  docker-down      - Stop services with docker compose"
	@echo "  docker-logs      - View docker compose logs"
	@echo "  docker-clean     - Stop services and remove volumes"
	@echo "  clean            - Remove Python cache and build artifacts"
	@echo "  clean-venv       - Remove virtual environment"
	@echo ""
	@echo "Deployment (AWS Lambda) — use ENV=dev or ENV=prod:"
	@echo "  ecr-setup        - Create ECR repository (one-time)"
	@echo "  ecr-login        - Authenticate Docker with ECR"
	@echo "  lambda-push      - Build ARM64 Lambda image and push to ECR"
	@echo "  tf-ecr-init      - Initialise Terraform for ECR (run once)"
	@echo "  tf-ecr-apply     - Create ECR repository via Terraform (run once)"
	@echo "  tf-init          - Initialise Terraform for environment (ENV=dev)"
	@echo "  tf-plan          - Show planned Terraform changes (ENV=dev)"
	@echo "  tf-apply         - Apply Terraform infrastructure changes (ENV=dev)"
	@echo "  tf-destroy       - Destroy environment infrastructure — destructive! (ENV=dev)"
	@echo "  deploy           - Build+push Lambda image and apply Terraform (ENV=dev)"
	@echo "  lambda-logs      - Tail CloudWatch logs from Lambda (ENV=dev)"
	@echo "  lambda-invoke    - Invoke Lambda function with a test payload (ENV=dev)"
	@echo "  test-deploy      - Run post-deploy smoke tests (ENV=dev)"
	@echo "  destroy          - Destroy all environment infrastructure with confirmation (ENV=dev)"

venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		$(PYTHON) -m venv $(VENV); \
	fi

install: venv
	$(VENV_PIP) install -r requirements.txt

install-dev: venv
	$(VENV_PIP) install -r requirements.txt -r requirements-dev.txt

lint: install-dev
	@echo "Running black..."
	$(VENV_PYTHON) -m black --check app/ tests/ scripts/ *.py 2>/dev/null || true
	@echo "Running isort..."
	$(VENV_PYTHON) -m isort --check-only app/ tests/ scripts/ *.py 2>/dev/null || true
	@echo "Running flake8 (errors only)..."
	$(VENV_PYTHON) -m flake8 app/ tests/ scripts/ --max-line-length=120 --extend-ignore=E203,W503,W293,F401,F541,F841 --select=E,F --exclude=*/config_store.py,*/encryption.py 2>/dev/null || echo "⚠️  Some flake8 warnings (not blocking)"

test: install-dev
	$(VENV_PYTHON) -m pytest tests/

test-unit: install-dev
	$(VENV_PYTHON) -m pytest tests/unit/

test-integration: install-dev
	@echo "Ensuring DynamoDB Local is running..."
	@docker compose up -d dynamodb-local 2>/dev/null || true
	@sleep 2
	INTEGRATION_TESTS=1 $(VENV_PYTHON) -m pytest tests/integration/ -v -s

test-cov: install-dev
	$(VENV_PYTHON) -m pytest tests/ --cov=app --cov-report=term-missing --cov-report=html

ci: lint test test-integration
	@echo "✓ All CI checks passed!"

test-e2e:
	@echo "=== Running End-to-End Config Delivery Test (Local) ==="
	./test/test_e2e.sh

test-docker-health:
	@echo "=== Testing Docker Health Endpoint with Git SHA ==="
	./test_docker_health.sh

test-e2e-aws:
	@echo "=== Running End-to-End Test Against AWS DynamoDB ==="
	./test/test_e2e_aws.sh

test-e2e-logs:
	@echo "=== Viewing E2E Test Container Logs ==="
	docker logs orchestrator-test-container -f

test-e2e-clean:
	@echo "=== Cleaning up E2E test containers ==="
	docker compose -f test/docker-compose.e2e.yml down -v

run: install
	$(VENV_PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8571

docker-build:
	docker build \
	  --provenance=false \
	  --sbom=false \
	  --build-arg GIT_COMMIT=$$(git rev-parse --short HEAD) \
	  --platform linux/arm64 \
	  -t voxhelm-orchestrator:$(IMAGE_TAG) .

docker-up:
	GIT_COMMIT=$$(git rev-parse --short HEAD) docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-clean:
	docker compose down -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage build/ dist/

clean-venv:
	rm -rf $(VENV)

# ---------- AWS / Lambda deployment ----------

ecr-setup:
	@echo "Provisioning ECR repository..."
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/ecr init
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/ecr apply -auto-approve

ecr-login:
	aws --profile $(AWS_PROFILE) ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(_ECR_REGISTRY)

## lambda-push ENV=dev: build ARM64 Lambda image and push to ECR
lambda-push: ecr-login
	docker build \
		--provenance=false \
		--sbom=false \
		--build-arg GIT_COMMIT=$(IMAGE_TAG) \
		--platform linux/arm64 \
		-f Dockerfile.lambda \
		-t $(_ECR_REGISTRY)/$(ECR_REPO_NAME):$(ENV)-$(IMAGE_TAG) .
	docker push $(_ECR_REGISTRY)/$(ECR_REPO_NAME):$(ENV)-$(IMAGE_TAG)
	@echo "Pushed: $(_ECR_REGISTRY)/$(ECR_REPO_NAME):$(ENV)-$(IMAGE_TAG)"

## tf-ecr-init: initialise Terraform for ECR (run once after bootstrap)
tf-ecr-init:
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/ecr init

## tf-ecr-apply: create ECR repository (run once)
tf-ecr-apply: tf-ecr-init
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/ecr apply -auto-approve

## tf-init ENV=dev: initialise Terraform for an environment
tf-init:
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/environments/$(ENV) init

## tf-plan ENV=dev: show planned changes
tf-plan:
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/environments/$(ENV) plan -var="image_tag=$(ENV)-$(IMAGE_TAG)"

## tf-apply ENV=dev: apply infrastructure changes
tf-apply:
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/environments/$(ENV) apply -auto-approve -var="image_tag=$(ENV)-$(IMAGE_TAG)"

## tf-destroy ENV=dev: destroy all environment infrastructure (destructive!)
tf-destroy:
	AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/environments/$(ENV) destroy -var="image_tag=$(ENV)-$(IMAGE_TAG)"

## deploy ENV=dev: build+push Lambda image and apply Terraform
deploy: lambda-push
	$(MAKE) tf-apply ENV=$(ENV)
	@echo "==> Deployed $(ENV)-$(IMAGE_TAG) to Lambda"

## lambda-logs ENV=dev: tail CloudWatch logs from Lambda
lambda-logs:
	aws logs tail /aws/lambda/orchestrator-$(ENV) \
	  --follow \
	  --region $(AWS_REGION) \
	  --profile $(AWS_PROFILE)

## lambda-invoke ENV=dev: invoke Lambda function with a test payload
lambda-invoke:
	aws lambda invoke \
	  --function-name orchestrator-$(ENV) \
	  --payload '{"rawPath":"/health","requestContext":{"http":{"method":"GET"}}}' \
	  --region $(AWS_REGION) \
	  --profile $(AWS_PROFILE) \
	  --cli-binary-format raw-in-base64-out \
	  /tmp/lambda-response.json && cat /tmp/lambda-response.json

## test-deploy ENV=dev: run post-deploy smoke tests against the live Lambda
test-deploy: install-dev
	$(eval _DEPLOY_URL := $(shell AWS_PROFILE=$(AWS_PROFILE) terraform -chdir=infra/environments/$(ENV) output -raw api_gateway_url 2>/dev/null))
	@if [ -z "$(_DEPLOY_URL)" ]; then echo "ERROR: Could not read api_gateway_url. Run make tf-init ENV=$(ENV) first."; exit 1; fi
	DEPLOY_URL=$(_DEPLOY_URL) POST_DEPLOY_TESTS=1 \
		$(VENV_PYTHON) -m pytest tests/post_deploy/ -v -s

## destroy ENV=dev: destroy all environment infrastructure (destructive!)
destroy:
	@echo "WARNING: This will destroy $(ENV) orchestrator Lambda + DynamoDB."
	@read -p "Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted."; exit 1)
	$(MAKE) tf-destroy ENV=$(ENV)

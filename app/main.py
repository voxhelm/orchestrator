import logging
import os
import time
from contextlib import asynccontextmanager

from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.middleware.auth import APIKeyMiddleware
from app.routes import clusters, config, containers, health
from app.services.dynamodb import ensure_table_exists

# force=True overrides Lambda's pre-installed root handler so our messages
# actually appear in CloudWatch instead of being silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.middleware.auth import close_auth_client

    settings = get_settings()
    if settings.dynamodb_endpoint:
        # Local mode only — in AWS the table is provisioned by Terraform
        logger.info("Starting orchestrator — ensuring DynamoDB table exists...")
        try:
            ensure_table_exists()
        except (ClientError, EndpointConnectionError) as exc:
            logger.warning("Could not reach DynamoDB at startup: %s", exc)
    yield
    logger.info("Shutting down orchestrator")
    await close_auth_client()


settings = get_settings()

app = FastAPI(
    title="Voxhelm Orchestrator API",
    description="""
## Container Orchestrator for OpenClaw Agents

This API manages containerized agent deployments on AWS ECS or Kubernetes, providing:

* **Container Management**: Create, list, monitor, and delete agent containers
* **Health Monitoring**: Track container health and performance metrics
* **User Isolation**: Each user gets their own isolated container environment
* **Multi-backend**: Supports AWS ECS Fargate and Kubernetes (k8s) compute backends

### Authentication

All endpoints (except `/health`) require API key authentication via the `Authorization` header with Bearer token format.

### Container Lifecycle

1. **Create** - Request a new container via `POST /containers` (optionally specify `backend`)
2. **Monitor** - Check status and health via `GET /containers/{container_id}`
3. **Use** - Connect to your running container via its IP address
4. **Delete** - Stop and remove via `DELETE /containers/{container_id}`

### Compute Backends

Containers can run on either **ECS Fargate** or **Kubernetes**. The active backend is controlled
by the `DEFAULT_BACKEND` environment variable on the orchestrator (`ecs` or `k8s`). Individual
requests can override the default by including `"backend": "k8s"` or `"backend": "ecs"` in the
`POST /containers` body.
    """,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "health",
            "description": "Service health check endpoint",
        },
        {
            "name": "containers",
            "description": "Operations for managing agent containers",
        },
        {
            "name": "config",
            "description": "Configuration management for users and system defaults",
        },
        {
            "name": "clusters",
            "description": "Cluster registry — admin-only view of compute clusters the orchestrator has dispatched to",
        },
    ],
)


@app.exception_handler(ClientError)
async def aws_client_error_handler(request: Request, exc: ClientError):
    """Handle AWS SDK ClientError exceptions."""
    error_code = exc.response.get("Error", {}).get("Code", "Unknown")
    error_message = exc.response.get("Error", {}).get("Message", str(exc))
    logger.error(f"AWS ClientError: {error_code} - {error_message}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"AWS service error: {error_message}"},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    user_id = getattr(request.state, "user_id", "-")
    logger.info(
        "%s %s %s user=%s %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        user_id,
        duration_ms,
    )
    return response


app.add_middleware(APIKeyMiddleware)

app.include_router(health.router)
app.include_router(containers.router)
app.include_router(config.router)
app.include_router(clusters.router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Add Bearer token security scheme
    openapi_schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "API Key",
            "description": "Enter your API key (without 'Bearer ' prefix)",
        }
    }
    # Apply security globally to all endpoints except public ones
    valid_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}
    for path in openapi_schema["paths"]:
        for method in openapi_schema["paths"][path]:
            if method in valid_methods:
                # Don't apply security to /health endpoint
                if path == "/health":
                    continue
                openapi_schema["paths"][path][method]["security"] = [
                    {"BearerAuth": []}
                ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/", include_in_schema=False)
def root():
    return {"message": "orchestrator", "docs": "/docs"}

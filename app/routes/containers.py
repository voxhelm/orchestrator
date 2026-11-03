import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.models.container import (ContainerHealthResponse, ContainerRequest,
                                  ContainerResponse)
from app.services import dynamodb, ecs, kubernetes as k8s

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/containers", tags=["containers"])


@router.post(
    "",
    response_model=ContainerResponse,
    summary="Create a new container",
    response_description="Container created successfully",
)
async def create_container(request: Request, req: ContainerRequest):
    """
    Create a new container for the authenticated user.

    The container will be deployed as an ECS task and will start in PENDING status.
    Once the task is running and health checks pass, the status will change to RUNNING
    and an IP address will be assigned.

    The API key from the Authorization header will be stored in DynamoDB for the container
    to use for authentication with other services. If a named config does not exist, it will
    be created with default values.

    Optional environment variables can be passed in the request body to customize container
    behavior (e.g. DEBUG=true for verbose logging).
    """
    settings = get_settings()
    user_id = request.state.user_id
    api_key = request.state.api_key
    config_name = req.config_name or "default"
    backend = req.backend or settings.default_backend

    logger.info("create_container: user=%s config=%s agent_id=%s backend=%s", user_id, config_name, req.agent_id, backend)

    if backend == "k8s":
        container = k8s.create_container(
            user_id=user_id,
            api_key=api_key,
            config_name=config_name,
            agent_id=req.agent_id,
            env_vars=req.env_vars,
        )
    else:
        container = ecs.create_container(
            user_id=user_id,
            api_key=api_key,
            config_name=config_name,
            agent_id=req.agent_id,
            env_vars=req.env_vars,
        )

    logger.info(
        "create_container done: user=%s container=%s status=%s backend=%s",
        user_id,
        container.container_id,
        container.status,
        container.backend,
    )
    return container.to_response()


@router.get(
    "",
    response_model=List[ContainerResponse],
    summary="List all containers",
    response_description="List of containers for the authenticated user",
)
async def list_containers(
    request: Request,
    status: Optional[str] = None,
):
    """
    List all containers for the authenticated user.

    Optionally filter by status:
    - **PENDING**: Container is being created
    - **RUNNING**: Container is active
    - **STOPPED**: Container has been stopped
    - **FAILED**: Container failed to start or crashed
    """
    user_id = request.state.user_id

    containers = dynamodb.get_user_containers(user_id=user_id, status=status)
    return [c.to_response() for c in containers]


@router.get(
    "/{container_id}",
    response_model=ContainerResponse,
    summary="Get container details",
    response_description="Container details",
    responses={
        404: {"description": "Container not found"},
    },
)
async def get_container(request: Request, container_id: str):
    """
    Get details of a specific container.

    Returns the current status, IP address, health status, and timestamps
    for the specified container.
    """
    user_id = request.state.user_id

    container = dynamodb.get_container(user_id=user_id, container_id=container_id)
    if not container:
        logger.warning("get_container: not found user=%s container=%s", user_id, container_id)
        raise HTTPException(status_code=404, detail="Container not found")

    # For k8s containers, sync live pod status on every GET.
    # sync_pod_status does blocking network I/O via the k8s SDK, so run it in a
    # thread to avoid blocking the event loop.
    if container.backend == "k8s" and container.status in ("PENDING", "RUNNING"):
        container = await run_in_threadpool(k8s.sync_pod_status, user_id=user_id, container_id=container_id) or container

    return container.to_response()


@router.delete(
    "/{container_id}",
    status_code=204,
    summary="Delete a container",
    response_description="Container deleted successfully",
    responses={
        404: {"description": "Container not found"},
    },
)
async def delete_container(request: Request, container_id: str, delete_storage: bool = False):
    """
    Delete and stop a container.

    This will stop the ECS task or k8s pod and mark the container as STOPPED.
    The container record will remain in the database for audit purposes.

    For k8s agents, the agent's persistent volume (OpenClaw config, workspace,
    MEMORY.md, sessions) is kept so a relaunch keeps its memory. Pass
    `delete_storage=true` when the agent itself is being deleted to remove it.
    """
    user_id = request.state.user_id

    container = dynamodb.get_container(user_id=user_id, container_id=container_id)
    if not container:
        logger.warning("delete_container: not found user=%s container=%s", user_id, container_id)
        raise HTTPException(status_code=404, detail="Container not found")

    logger.info(
        "delete_container: user=%s container=%s backend=%s delete_storage=%s",
        user_id, container_id, container.backend, delete_storage,
    )
    if container.backend == "k8s":
        await run_in_threadpool(
            k8s.stop_container, user_id=user_id, container_id=container_id, delete_storage=delete_storage
        )
    else:
        ecs.stop_container(user_id=user_id, container_id=container_id)


@router.get(
    "/{container_id}/health",
    response_model=ContainerHealthResponse,
    summary="Get container health",
    response_description="Container health status and metrics",
    responses={
        404: {"description": "Container not found"},
    },
)
async def get_container_health(request: Request, container_id: str):
    """
    Get detailed health status of a specific container.

    Returns health check status along with detailed metrics including:
    - Number of running agents
    - Container uptime
    - Memory and CPU usage
    - Agent details
    """
    user_id = request.state.user_id

    container = dynamodb.get_container(user_id=user_id, container_id=container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")

    return ContainerHealthResponse(
        container_id=container.container_id,
        health_status=container.health_status,
        last_health_check=container.last_health_check,
        health_data=container.health_data,
    )

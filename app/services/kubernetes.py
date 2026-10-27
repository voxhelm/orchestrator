"""Kubernetes backend for container orchestration.

Mirrors the interface of app.services.ecs so the route layer can swap
between ECS and k8s with a single conditional.
"""

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException

from app.config import get_settings
from app.models.container import Container
from app.services import dynamodb
from app.services.user_config import UserConfigService

logger = logging.getLogger(__name__)

# Event routing constants used by lambda_handler.py
PROVISION_EVENT_SOURCE = "clawtalk.orchestrator"
PROVISION_EVENT_TYPE = "provision_k8s_pod"

# Module-level cached client — config is loaded once on first use.
_k8s_core_v1: Optional[k8s_client.CoreV1Api] = None


def _load_kubeconfig_from_ssm(ssm_path: str, context: Optional[str]) -> bool:
    """Fetch kubeconfig YAML from SSM and load it. Returns True on success."""
    try:
        import os

        import boto3
        import yaml

        region = os.environ.get("AWS_REGION") or os.environ.get("DYNAMODB_REGION", "us-east-1")
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=ssm_path, WithDecryption=True)
        kubeconfig_yaml = response["Parameter"]["Value"]
        config_dict = yaml.safe_load(kubeconfig_yaml)
        k8s_config.load_kube_config_from_dict(config_dict, context=context)
        logger.info("k8s kubeconfig loaded from SSM: %s", ssm_path)
        return True
    except Exception as exc:
        logger.warning("k8s kubeconfig SSM load failed (%s): %s", ssm_path, exc)
        return False


def _get_k8s_client() -> k8s_client.CoreV1Api:
    """Return a cached CoreV1Api client, loading kubeconfig on first call.

    One urllib3 retry is configured to handle transient WireGuard path setup
    on Lambda cold starts; _wait_for_k8s_api() in provision_pod acts as the
    readiness gate so the retry count stays bounded.
    """
    global _k8s_core_v1
    if _k8s_core_v1 is not None:
        return _k8s_core_v1

    settings = get_settings()
    try:
        if settings.k8s_kubeconfig_ssm_path:
            _load_kubeconfig_from_ssm(settings.k8s_kubeconfig_ssm_path, settings.k8s_context)
        elif settings.k8s_kubeconfig:
            k8s_config.load_kube_config(
                config_file=settings.k8s_kubeconfig,
                context=settings.k8s_context,
            )
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config(context=settings.k8s_context)
    except Exception as exc:
        logger.warning("k8s config load warning: %s", exc)

    # Allow one urllib3 retry after the initial attempt — covers a transient
    # hiccup on the first connection once Tailscale's WireGuard path is fresh.
    # _wait_for_k8s_api() in provision_pod acts as the readiness gate; with
    # that guard in place, one retry is safe and covers transient path setup.
    configuration = k8s_client.Configuration.get_default_copy()
    configuration.retries = 1

    api_client = k8s_client.ApiClient(configuration=configuration)

    # In Lambda, Tailscale runs with --tun=userspace-networking which does NOT
    # install kernel routes for 100.64.0.0/10. Python socket calls go through
    # the kernel and never reach the Tailscale daemon. Route k8s API traffic
    # through the local SOCKS5 proxy that tailscaled exposes on port 1055.
    #
    # urllib3's ProxyManager only supports http/https schemes, so we must use
    # SOCKSProxyManager (from urllib3.contrib.socks, requires pysocks) and
    # patch it directly onto the REST client's pool_manager.
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        from urllib3.contrib.socks import SOCKSProxyManager

        # Replace the default pool manager with one that routes through the
        # Tailscale SOCKS5 proxy. We must carry forward the client cert/key
        # (mTLS) from the kubeconfig so the k8s API accepts the connection.
        # SSL chain verification is disabled because k3s uses a self-signed CA
        # whose SANs typically don't include the Tailscale IP — the
        # Tailscale/WireGuard overlay already provides encryption and mutual
        # authentication.
        proxy_kwargs: dict = {"cert_reqs": "CERT_NONE"}
        if configuration.cert_file:
            proxy_kwargs["cert_file"] = configuration.cert_file
        if configuration.key_file:
            proxy_kwargs["key_file"] = configuration.key_file

        api_client.rest_client.pool_manager = SOCKSProxyManager(
            "socks5://localhost:1055", **proxy_kwargs
        )
        logger.info(
            "k8s: using Tailscale SOCKS5 proxy at localhost:1055 "
            "(SSL verification disabled, client_cert=%s)",
            bool(configuration.cert_file),
        )

    _k8s_core_v1 = k8s_client.CoreV1Api(api_client=api_client)
    return _k8s_core_v1


def _generate_container_id() -> str:
    return f"oc-{uuid.uuid4().hex[:8]}"


def _update_agent_container(user_id: str, agent_id: str, container_id: str, api_key: str) -> None:
    """Notify auth-gateway of the container assigned to an agent."""
    settings = get_settings()
    url = f"{settings.auth_gateway_url}/users/{user_id}/agents/{agent_id}"
    try:
        response = httpx.put(
            url,
            json={"container_id": container_id},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.auth_gateway_timeout,
        )
        if response.status_code == 200:
            logger.info("agent container updated: agent=%s container=%s", agent_id, container_id)
        else:
            logger.warning(
                "agent container update failed: agent=%s container=%s status=%s body=%s",
                agent_id, container_id, response.status_code, response.text,
            )
    except httpx.RequestError as exc:
        logger.error("agent container update error: agent=%s container=%s error=%s", agent_id, container_id, exc)


def _build_plain_env(
    user_id: str,
    api_key: str,
    container_id: str,
    config_name: str,
    agent_id: Optional[str],
    env_vars: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Build the flat env-var dict for a pod, reading user config from DynamoDB."""
    settings = get_settings()
    config_service = UserConfigService()
    user_config = config_service.get_user_config(user_id, config_name) or {}

    llm_provider_env_keys = {
        "openrouter": ("OPENROUTER_API_KEY", user_config.get("openrouter_api_key", "")),
        "anthropic": ("ANTHROPIC_API_KEY", user_config.get("anthropic_api_key", "")),
        "openai": ("OPENAI_API_KEY", user_config.get("openai_api_key", "")),
    }

    protected_keys = {
        "API_KEY", "CONTAINER_ID", "CONFIG_NAME", "ORCHESTRATOR_URL", "AGENT_ID", "OPENCLAW_DISABLE_BONJOUR"
    }
    plain_env: Dict[str, str] = {
        "API_KEY": api_key,
        "CONTAINER_ID": container_id,
        "CONFIG_NAME": config_name,
        "ORCHESTRATOR_URL": settings.orchestrator_url,
        "OPENCLAW_DISABLE_BONJOUR": "1",
        "LLM_STREAMING": "true",
    }
    if agent_id:
        plain_env["AGENT_ID"] = agent_id

    for env_name, value in llm_provider_env_keys.values():
        if value:
            plain_env[env_name] = value

    if env_vars:
        filtered = {k: v for k, v in env_vars.items() if k not in protected_keys}
        plain_env.update(filtered)
        if filtered:
            logger.info("Added %d custom env vars to container %s", len(filtered), container_id)

    return plain_env


def _wait_for_k8s_api(timeout: float = 20.0) -> bool:
    """Poll the k8s API until it responds or timeout elapses.

    Uses the k8s client (which is proxy-configured for Lambda) so that the
    health check travels through the same Tailscale SOCKS5 path as the actual
    API calls. Any HTTP response — including 401/403 — means the server is up.
    Returns True on success, False on timeout.
    """
    api = _get_k8s_client()
    deadline = time.monotonic() + timeout
    logger.info(
        "k8s: waiting for API reachability at %s (timeout=%.0fs)",
        api.api_client.configuration.host, timeout,
    )
    while time.monotonic() < deadline:
        try:
            api.list_namespace(_request_timeout=(2.0, 2.0))
            logger.info("k8s: API reachable")
            return True
        except ApiException:
            # Got an HTTP response (auth error, forbidden, etc.) — server is up.
            logger.info("k8s: API reachable (auth response received)")
            return True
        except Exception as exc:
            logger.debug("k8s: API not yet reachable: %s", exc)
        time.sleep(1.0)

    logger.warning("k8s: API unreachable after %.0fs — proceeding anyway", timeout)
    return False


def provision_pod(detail: Dict[str, Any]) -> None:
    """Create the k8s pod and update the container record.

    Called either inline (local dev) or via an async Lambda self-invocation so
    that POST /containers can return the PENDING record immediately without
    blocking on the Tailscale → k8s API round-trip.
    """
    container_id: str = detail["container_id"]
    user_id: str = detail["user_id"]
    agent_id: Optional[str] = detail.get("agent_id")
    api_key: str = detail["api_key"]
    namespace: str = detail["namespace"]
    pod_image: str = detail["pod_image"]
    image_pull_policy: str = detail["image_pull_policy"]
    image_pull_secret: Optional[str] = detail.get("image_pull_secret")
    raw_env: List[Dict[str, str]] = detail["env_vars"]

    logger.info("k8s provision_pod start: container=%s namespace=%s", container_id, namespace)

    # Wait for the k8s API to be reachable before issuing the create call.
    # On Lambda cold starts the WireGuard peer path may not be established yet;
    # polling /healthz is the simplest proxy for "Tailscale + k8s are ready".
    _wait_for_k8s_api()

    k8s_env = [k8s_client.V1EnvVar(name=e["name"], value=e["value"]) for e in raw_env]

    pod = k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=container_id,
            namespace=namespace,
            labels={
                "app": "openclaw-agent",
                "user_id": user_id,
                "container_id": container_id,
            },
        ),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            containers=[
                k8s_client.V1Container(
                    name="openclaw-agent",
                    image=pod_image,
                    image_pull_policy=image_pull_policy,
                    ports=[k8s_client.V1ContainerPort(container_port=8080)],
                    env=k8s_env,
                )
            ],
            image_pull_secrets=(
                [k8s_client.V1LocalObjectReference(name=image_pull_secret)]
                if image_pull_secret else None
            ),
        ),
    )

    settings = get_settings()
    api = _get_k8s_client()
    container = dynamodb.get_container(user_id, container_id)
    if not container:
        logger.error("provision_pod: container record not found: container=%s", container_id)
        return

    try:
        result = api.create_namespaced_pod(
            namespace=namespace, body=pod, _request_timeout=(settings.k8s_api_timeout, settings.k8s_api_timeout)
        )
        pod_name = result.metadata.name
        container.task_arn = pod_name
        container.updated_at = datetime.now(timezone.utc)
        dynamodb.update_container(container)
        logger.info("k8s pod created: container=%s pod=%s namespace=%s", container_id, pod_name, namespace)

        if agent_id:
            _update_agent_container(user_id=user_id, agent_id=agent_id, container_id=container_id, api_key=api_key)
    except Exception as exc:
        logger.exception("k8s pod creation failed: container=%s error=%s", container_id, exc)
        container.status = "FAILED"
        container.updated_at = datetime.now(timezone.utc)
        dynamodb.update_container(container)


def _dispatch_provision(detail: Dict[str, Any]) -> None:
    """Fire pod provisioning: async Lambda self-invocation in production, thread in local dev."""
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if function_name:
        # Production (Lambda): invoke self asynchronously so the HTTP response
        # is returned immediately and pod creation happens in a separate invocation.
        try:
            import boto3
            region = os.environ.get("AWS_REGION") or os.environ.get("DYNAMODB_REGION", "us-east-1")
            lambda_client = boto3.client("lambda", region_name=region)
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",  # fire-and-forget
                Payload=json.dumps({
                    "source": PROVISION_EVENT_SOURCE,
                    "detail-type": PROVISION_EVENT_TYPE,
                    "detail": detail,
                }).encode(),
            )
            logger.info(
                "k8s provision dispatched async: container=%s function=%s",
                detail["container_id"], function_name,
            )
        except Exception as exc:
            # If we can't invoke the Lambda, fall back to blocking inline.
            logger.error(
                "k8s async dispatch failed, falling back to inline: container=%s error=%s",
                detail["container_id"], exc,
            )
            provision_pod(detail)
    else:
        # Local dev: run in a daemon thread so the HTTP response returns immediately.
        t = threading.Thread(target=provision_pod, args=(detail,), daemon=True)
        t.start()
        logger.info("k8s provision dispatched in thread: container=%s", detail["container_id"])


def create_container(
    user_id: str,
    api_key: str,
    config_name: str = "default",
    agent_id: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
) -> Container:
    """Create a new Kubernetes Pod for a user.

    Returns a Container record in PENDING status immediately; pod provisioning
    (the slow Tailscale → k8s API call) is dispatched asynchronously so the
    HTTP response is never blocked by cluster latency.

    Poll GET /containers/{id} to observe the transition PENDING → RUNNING.
    """
    settings = get_settings()
    container_id = _generate_container_id()
    now = datetime.now(timezone.utc)

    cluster_name = settings.k8s_cluster_name or settings.k8s_context or "default"
    logger.info("k8s create_container: container=%s user=%s config=%s cluster=%s", container_id, user_id, config_name, cluster_name)

    # 1. Ensure user config exists with defaults and current API key
    config_service = UserConfigService()
    config_service.ensure_container_defaults(user_id=user_id, config_name=config_name, api_key=api_key)

    # 2. Build env vars (reads DynamoDB user config — fast)
    plain_env = _build_plain_env(
        user_id=user_id,
        api_key=api_key,
        container_id=container_id,
        config_name=config_name,
        agent_id=agent_id,
        env_vars=env_vars,
    )

    # 3. Persist PENDING record so callers can poll immediately
    container = Container(
        container_id=container_id,
        user_id=user_id,
        task_arn="",
        status="PENDING",
        agent_id=agent_id,
        health_status="UNKNOWN",
        backend="k8s",
        cluster=cluster_name,
        created_at=now,
        updated_at=now,
    )
    dynamodb.create_container(container)
    try:
        dynamodb.register_cluster(name=cluster_name, backend="k8s", namespace=settings.k8s_namespace)
    except Exception as exc:
        logger.warning("cluster registry update failed, container creation unaffected: cluster=%s error=%s", cluster_name, exc)
    logger.info("k8s create_container db record created: container=%s", container_id)

    # 4. Dispatch pod creation asynchronously — does not block this response.
    _dispatch_provision({
        "container_id": container_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "api_key": api_key,
        "namespace": settings.k8s_namespace,
        "pod_image": settings.k8s_image,
        "image_pull_policy": settings.k8s_image_pull_policy,
        "image_pull_secret": settings.k8s_image_pull_secret,
        "env_vars": [{"name": k, "value": v} for k, v in plain_env.items()],
    })

    return container


def stop_container(user_id: str, container_id: str) -> bool:
    """Delete the Kubernetes Pod backing a container."""
    container = dynamodb.get_container(user_id, container_id)
    if not container:
        return False

    pod_name = container.task_arn or container_id
    settings = get_settings()
    namespace = settings.k8s_namespace

    api = _get_k8s_client()
    try:
        api.delete_namespaced_pod(
            name=pod_name, namespace=namespace, _request_timeout=(settings.k8s_api_timeout, settings.k8s_api_timeout)
        )
        logger.info("k8s pod deleted: container=%s pod=%s", container_id, pod_name)
    except ApiException as exc:
        if exc.status != 404:
            logger.exception("k8s pod deletion failed: container=%s error=%s", container_id, exc)
            return False
        logger.info("k8s pod already gone: container=%s pod=%s", container_id, pod_name)

    container.status = "STOPPED"
    container.updated_at = datetime.now(timezone.utc)
    dynamodb.update_container(container)
    return True


def get_container_details(user_id: str, container_id: str) -> Optional[Container]:
    """Get container details from DynamoDB."""
    return dynamodb.get_container(user_id, container_id)


def extract_pod_endpoint(pod: Any) -> Optional[Dict[str, str]]:
    """Extract connection details from a running Kubernetes Pod object.

    Accepts both a kubernetes SDK V1Pod object and a plain dict (for testing).
    """
    ip: Optional[str] = None
    if hasattr(pod, "status") and pod.status:
        ip = getattr(pod.status, "pod_ip", None)
    elif isinstance(pod, dict):
        ip = pod.get("status", {}).get("podIP")

    if not ip:
        return None

    return {
        "ip_address": ip,
        "port": 8080,
        "health_endpoint": f"http://{ip}:8080/health",
        "api_endpoint": f"http://{ip}:8080",
    }


def sync_pod_status(user_id: str, container_id: str) -> Optional[Container]:
    """Poll the Kubernetes API and sync the container's status in DynamoDB.

    Called on-demand (e.g. GET /containers/{id}) to keep status fresh without
    requiring a cluster-side event webhook.
    """
    container = dynamodb.get_container(user_id, container_id)
    if not container or not container.task_arn:
        return container

    settings = get_settings()
    try:
        api = _get_k8s_client()
        pod = api.read_namespaced_pod(
            name=container.task_arn, namespace=settings.k8s_namespace,
            _request_timeout=(settings.k8s_api_timeout, settings.k8s_api_timeout),
        )
        phase = pod.status.phase if pod.status else None
        logger.info("k8s sync_pod_status: container=%s pod=%s phase=%s", container_id, container.task_arn, phase)

        if phase == "Running":
            endpoints = extract_pod_endpoint(pod)
            if endpoints:
                container.ip_address = endpoints["ip_address"]
                container.health_endpoint = endpoints["health_endpoint"]
                container.api_endpoint = endpoints["api_endpoint"]
            container.status = "RUNNING"
            container.health_status = "STARTING"
            container.updated_at = datetime.now(timezone.utc)
            dynamodb.update_container(container)

        elif phase in ("Succeeded", "Failed", "Unknown"):
            container.status = "STOPPED"
            container.updated_at = datetime.now(timezone.utc)
            dynamodb.update_container(container)

    except ApiException as exc:
        if exc.status == 404:
            logger.info("k8s pod not found, marking STOPPED: container=%s", container_id)
            container.status = "STOPPED"
            container.updated_at = datetime.now(timezone.utc)
            dynamodb.update_container(container)
        else:
            logger.warning("k8s sync_pod_status API error: container=%s error=%s", container_id, exc)

    return container


def poll_k8s_container_statuses(max_workers: int = 10) -> Dict[str, int]:
    """Scan DynamoDB for non-terminal k8s containers and sync each pod's status.

    Intended to be called by a scheduled EventBridge rule (e.g. every 60 s) so
    that container status stays current without requiring a client GET.

    Syncs are executed in parallel (up to max_workers threads) to avoid
    sequential blocking k8s API calls exhausting the Lambda timeout when there
    are many non-terminal containers.

    Returns a summary dict with counts of containers processed / errored.
    """
    containers = dynamodb.get_non_terminal_k8s_containers()
    logger.info("k8s poller: found %d non-terminal k8s containers to sync", len(containers))

    processed = 0
    errors = 0

    def _sync(container):
        sync_pod_status(container.user_id, container.container_id)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_sync, c): c for c in containers}
        for future in as_completed(futures):
            container = futures[future]
            exc = future.exception()
            if exc:
                logger.error(
                    "k8s poller: unhandled error syncing container=%s: %s",
                    container.container_id,
                    exc,
                    exc_info=exc,
                )
                errors += 1
            else:
                processed += 1

    logger.info("k8s poller: done. processed=%d errors=%d", processed, errors)
    return {"processed": processed, "errors": errors}

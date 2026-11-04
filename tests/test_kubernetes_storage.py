"""Tests for per-agent persistent storage (PVC) on the Kubernetes backend.

Agent pods mount a PVC at /home/node/.openclaw so OpenClaw config, workspaces,
MEMORY.md and sessions survive pod recreation and relaunches.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from kubernetes.client.exceptions import ApiException

from app.models.container import Container
from app.services import kubernetes as k8s_service


def _pvc(user_id):
    pvc = MagicMock()
    pvc.metadata.labels = {"user_id": user_id, "app": "openclaw-agent"}
    return pvc


def _create(mock_api, agent_id="agent-abc", user_id="user-123"):
    mock_result = MagicMock()
    mock_result.metadata.name = "oc-pod"
    mock_api.create_namespaced_pod.return_value = mock_result
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api), \
         patch("app.services.kubernetes._dispatch_provision",
               side_effect=lambda detail: k8s_service.provision_pod(detail)):
        return k8s_service.create_container(
            user_id=user_id, api_key="tok", config_name="default", agent_id=agent_id,
        )


def _k8s_container(container_id, user_id="user-123", agent_id=None):
    now = datetime.now(timezone.utc)
    return Container(
        container_id=container_id,
        user_id=user_id,
        task_arn=container_id,
        status="RUNNING",
        health_status="HEALTHY",
        agent_id=agent_id,
        backend="k8s",
        created_at=now,
        updated_at=now,
    )


# ── PVC naming ────────────────────────────────────────────────────────────────

def test_pvc_name_is_keyed_by_agent_id():
    """Relaunches of the same agent (new container id) map to the same PVC."""
    assert k8s_service.agent_pvc_name("36ee439b-d2a2", "oc-11111111") == "oc-data-36ee439b-d2a2"
    assert k8s_service.agent_pvc_name("36ee439b-d2a2", "oc-22222222") == "oc-data-36ee439b-d2a2"


def test_pvc_name_falls_back_to_container_id():
    assert k8s_service.agent_pvc_name(None, "oc-abcdef12") == "oc-data-oc-abcdef12"


def test_pvc_name_is_dns1123_safe():
    name = k8s_service.agent_pvc_name("Agent_With.Weird/Chars" + "x" * 80, "oc-1")
    assert len(name) <= 63
    assert name == name.lower()
    assert all(c.isalnum() or c == "-" for c in name)
    assert not name.endswith("-")


# ── Provisioning ──────────────────────────────────────────────────────────────

def test_provision_creates_pvc_and_mounts_it(aws_mocks):
    mock_api = MagicMock()
    _create(mock_api)

    mock_api.create_namespaced_persistent_volume_claim.assert_called_once()
    pvc_body = mock_api.create_namespaced_persistent_volume_claim.call_args[1]["body"]
    # kubernetes-client >= 37 validates PVC resources; it must be V1VolumeResourceRequirements.
    from kubernetes import client as k8s_client
    assert isinstance(pvc_body.spec.resources, k8s_client.V1VolumeResourceRequirements)
    assert pvc_body.metadata.name == "oc-data-agent-abc"
    assert pvc_body.metadata.labels["user_id"] == "user-123"
    assert pvc_body.metadata.labels["agent_id"] == "agent-abc"
    assert pvc_body.spec.access_modes == ["ReadWriteOnce"]
    assert pvc_body.spec.storage_class_name is None  # cluster default (k3s local-path)
    assert pvc_body.spec.resources.requests["storage"] == "2Gi"

    pod = mock_api.create_namespaced_pod.call_args[1]["body"]
    vols = {v.name: v for v in pod.spec.volumes}
    assert vols["openclaw-data"].persistent_volume_claim.claim_name == "oc-data-agent-abc"
    mounts = pod.spec.containers[0].volume_mounts
    assert [(m.name, m.mount_path) for m in mounts] == [("openclaw-data", "/home/node/.openclaw")]

    # Init container hands the root-owned local-path volume to the node user.
    (init,) = pod.spec.init_containers
    assert init.security_context.run_as_user == 0
    assert "chown 1000:1000 /data" in init.command[-1]
    assert "chmod 700 /data" in init.command[-1]
    assert init.volume_mounts[0].name == "openclaw-data"


def test_provision_reuses_existing_pvc_for_same_user(aws_mocks):
    """A relaunched agent reattaches to its existing PVC instead of failing."""
    mock_api = MagicMock()
    mock_api.create_namespaced_persistent_volume_claim.side_effect = ApiException(status=409)
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("user-123")

    container = _create(mock_api)

    mock_api.create_namespaced_pod.assert_called_once()
    from app.services.dynamodb import get_container
    assert get_container("user-123", container.container_id).status == "PENDING"


def test_provision_refuses_another_users_pvc(aws_mocks):
    """Passing someone else's agent_id must not mount their agent's data."""
    mock_api = MagicMock()
    mock_api.create_namespaced_persistent_volume_claim.side_effect = ApiException(status=409)
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("someone-else")

    container = _create(mock_api)

    mock_api.create_namespaced_pod.assert_not_called()
    from app.services.dynamodb import get_container
    assert get_container("user-123", container.container_id).status == "FAILED"


def test_provision_pvc_error_marks_failed(aws_mocks):
    mock_api = MagicMock()
    mock_api.create_namespaced_persistent_volume_claim.side_effect = ApiException(status=403)

    container = _create(mock_api)

    mock_api.create_namespaced_pod.assert_not_called()
    from app.services.dynamodb import get_container
    assert get_container("user-123", container.container_id).status == "FAILED"


def test_provision_without_storage(aws_mocks, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("K8S_AGENT_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        mock_api = MagicMock()
        _create(mock_api)
    finally:
        monkeypatch.delenv("K8S_AGENT_STORAGE_ENABLED")
        get_settings.cache_clear()

    mock_api.create_namespaced_persistent_volume_claim.assert_not_called()
    pod = mock_api.create_namespaced_pod.call_args[1]["body"]
    assert not pod.spec.volumes
    assert not pod.spec.init_containers


# ── Stop / delete ─────────────────────────────────────────────────────────────

def test_stop_agent_container_keeps_pvc_by_default(aws_mocks):
    """Stopping (e.g. for a relaunch) must not wipe the agent's memory."""
    from app.services import dynamodb
    dynamodb.create_container(_k8s_container("oc-keep01", agent_id="agent-abc"))
    mock_api = MagicMock()
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        assert k8s_service.stop_container("user-123", "oc-keep01") is True

    mock_api.delete_namespaced_pod.assert_called_once()
    mock_api.delete_namespaced_persistent_volume_claim.assert_not_called()


def test_stop_agent_container_with_delete_storage_removes_pvc(aws_mocks):
    from app.services import dynamodb
    dynamodb.create_container(_k8s_container("oc-del01", agent_id="agent-abc"))
    mock_api = MagicMock()
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("user-123")
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        assert k8s_service.stop_container("user-123", "oc-del01", delete_storage=True) is True

    mock_api.delete_namespaced_persistent_volume_claim.assert_called_once()
    assert mock_api.delete_namespaced_persistent_volume_claim.call_args[1]["name"] == "oc-data-agent-abc"


def test_stop_container_without_agent_always_removes_its_pvc(aws_mocks):
    """Container-scoped PVCs (no agent, e.g. onboarding) would otherwise leak."""
    from app.services import dynamodb
    dynamodb.create_container(_k8s_container("oc-onb01"))
    mock_api = MagicMock()
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("user-123")
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        k8s_service.stop_container("user-123", "oc-onb01")

    assert mock_api.delete_namespaced_persistent_volume_claim.call_args[1]["name"] == "oc-data-oc-onb01"


def test_delete_storage_refuses_other_users_pvc(aws_mocks):
    mock_api = MagicMock()
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("someone-else")
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        assert k8s_service.delete_agent_storage("user-123", "agent-abc", "oc-x") is False
    mock_api.delete_namespaced_persistent_volume_claim.assert_not_called()


def test_delete_storage_missing_pvc_is_success(aws_mocks):
    mock_api = MagicMock()
    mock_api.read_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        assert k8s_service.delete_agent_storage("user-123", "agent-abc", "oc-x") is True


def _mock_auth():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"user_id": "test-user"}
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    return patch("app.middleware.auth.get_auth_client", return_value=mock_client)


@_mock_auth()
def test_delete_route_delete_storage_flag(aws_mocks):
    """DELETE /containers/{id}?delete_storage=true removes the agent's PVC."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import dynamodb

    dynamodb.create_container(_k8s_container("oc-rt-keep", user_id="test-user", agent_id="agent-rt"))
    dynamodb.create_container(_k8s_container("oc-rt-del", user_id="test-user", agent_id="agent-rt2"))
    mock_api = MagicMock()
    mock_api.read_namespaced_persistent_volume_claim.return_value = _pvc("test-user")
    with patch("app.services.kubernetes._get_k8s_client", return_value=mock_api):
        client = TestClient(app)
        client.headers.update({"Authorization": "Bearer test-user:test-token-value"})
        assert client.delete("/containers/oc-rt-keep").status_code == 204
        mock_api.delete_namespaced_persistent_volume_claim.assert_not_called()
        assert client.delete("/containers/oc-rt-del?delete_storage=true").status_code == 204

    mock_api.delete_namespaced_persistent_volume_claim.assert_called_once()
    assert mock_api.delete_namespaced_persistent_volume_claim.call_args[1]["name"] == "oc-data-agent-rt2"

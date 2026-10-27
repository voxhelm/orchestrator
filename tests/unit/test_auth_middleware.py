"""Unit tests for authentication middleware."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth import APIKeyMiddleware


@pytest.fixture
def app():
    """Create test FastAPI app."""
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

    @app.get("/test")
    async def test_endpoint(request: Request):
        return {"user_id": request.state.user_id}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


def test_public_endpoints_no_auth(client):
    """Test public endpoints don't require authentication."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protected_endpoint_no_auth(client):
    """Test protected endpoints require authentication."""
    response = client.get("/test")
    assert response.status_code == 401
    assert "Missing or invalid Authorization header" in response.json()["detail"]


def test_protected_endpoint_invalid_format(client):
    """Test protected endpoints reject invalid auth format."""
    response = client.get("/test", headers={"Authorization": "InvalidFormat"})
    assert response.status_code == 401


def test_protected_endpoint_with_master_key(client, monkeypatch):
    """Test master API key access."""
    monkeypatch.setenv("MASTER_API_KEY", "test-master-key")

    # Clear settings cache
    from app.config import get_settings

    get_settings.cache_clear()

    response = client.get("/test", headers={"Authorization": "Bearer test-master-key"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "master"

    get_settings.cache_clear()


@patch("app.middleware.auth.get_auth_client")
def test_protected_endpoint_with_valid_api_key(mock_get_auth_client, client, monkeypatch):
    """Test valid API key validated by auth-gateway."""
    monkeypatch.setenv("AUTH_GATEWAY_URL", "http://auth-gateway:8001")

    # Clear settings cache
    from app.config import get_settings

    get_settings.cache_clear()

    # Mock auth-gateway response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"user_id": "user-123"}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_get_auth_client.return_value = mock_client

    response = client.get(
        "/test", headers={"Authorization": "Bearer sk-voxhelm-test-key"}
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == "user-123"

    get_settings.cache_clear()


@patch("app.middleware.auth.get_auth_client")
def test_protected_endpoint_invalid_api_key(mock_get_auth_client, client):
    """Test invalid API key rejected by auth-gateway."""
    # Mock auth-gateway response
    mock_response = MagicMock()
    mock_response.status_code = 401

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_get_auth_client.return_value = mock_client

    response = client.get("/test", headers={"Authorization": "Bearer invalid-key"})
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


@patch("app.middleware.auth.get_auth_client")
def test_protected_endpoint_auth_service_error(mock_get_auth_client, client):
    """Test auth service error handling."""
    # Mock auth-gateway connection error
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
    mock_get_auth_client.return_value = mock_client

    response = client.get(
        "/test", headers={"Authorization": "Bearer sk-voxhelm-test-key"}
    )
    assert response.status_code == 503
    assert "Auth service error" in response.json()["detail"]

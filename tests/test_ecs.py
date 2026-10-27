"""Tests for ECS service."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.container import Container
from app.services import ecs


def test_generate_container_id():
    """Test container ID generation."""
    container_id = ecs._generate_container_id()

    assert container_id.startswith("oc-")
    assert len(container_id) == 11  # "oc-" + 8 hex chars


def test_extract_container_endpoint():
    """Test extracting IP address from ECS task."""
    task = {
        "attachments": [
            {
                "type": "ElasticNetworkInterface",
                "details": [{"name": "privateIPv4Address", "value": "10.0.1.45"}],
            }
        ]
    }

    result = ecs.extract_container_endpoint(task)

    assert result is not None
    assert result["ip_address"] == "10.0.1.45"
    assert result["port"] == 8080
    assert result["health_endpoint"] == "http://10.0.1.45:8080/health"
    assert result["api_endpoint"] == "http://10.0.1.45:8080"


def test_extract_container_endpoint_missing_attachments():
    """Test extracting endpoint when attachments are missing."""
    task = {}

    result = ecs.extract_container_endpoint(task)

    assert result is None


def test_extract_container_endpoint_wrong_type():
    """Test extracting endpoint with wrong attachment type."""
    task = {
        "attachments": [
            {
                "type": "SomeOtherType",
                "details": [{"name": "privateIPv4Address", "value": "10.0.1.45"}],
            }
        ]
    }

    result = ecs.extract_container_endpoint(task)

    assert result is None


def test_handle_task_event_running(aws_mocks):
    """Test handling ECS task RUNNING event."""
    from app.services import dynamodb

    # Create container in PENDING status
    now = datetime.utcnow()
    container = Container(
        container_id="oc-test",
        user_id="user-123",
        task_arn="arn:aws:ecs:us-east-1:123456789:task/test",
        status="PENDING",
        health_status="UNKNOWN",
        created_at=now,
        updated_at=now,
    )
    dynamodb.create_container(container)

    # Handle RUNNING event
    event = {
        "detail": {
            "taskArn": "arn:aws:ecs:us-east-1:123456789:task/test",
            "lastStatus": "RUNNING",
            "tags": [
                {"key": "user_id", "value": "user-123"},
                {"key": "container_id", "value": "oc-test"},
            ],
            "attachments": [
                {
                    "type": "ElasticNetworkInterface",
                    "details": [{"name": "privateIPv4Address", "value": "10.0.1.45"}],
                }
            ],
        }
    }

    ecs.handle_task_event(event)

    # Verify container was updated
    updated = dynamodb.get_container("user-123", "oc-test")
    assert updated.status == "RUNNING"
    assert updated.health_status == "STARTING"
    assert updated.ip_address == "10.0.1.45"
    assert updated.health_endpoint == "http://10.0.1.45:8080/health"


def test_handle_task_event_stopped(aws_mocks):
    """Test handling ECS task STOPPED event."""
    from app.services import dynamodb

    # Create running container
    now = datetime.utcnow()
    container = Container(
        container_id="oc-test",
        user_id="user-123",
        task_arn="arn:aws:ecs:us-east-1:123456789:task/test",
        status="RUNNING",
        health_status="HEALTHY",
        created_at=now,
        updated_at=now,
    )
    dynamodb.create_container(container)

    # Handle STOPPED event
    event = {
        "detail": {
            "taskArn": "arn:aws:ecs:us-east-1:123456789:task/test",
            "lastStatus": "STOPPED",
            "tags": [
                {"key": "user_id", "value": "user-123"},
                {"key": "container_id", "value": "oc-test"},
            ],
        }
    }

    ecs.handle_task_event(event)

    # Verify container was updated
    updated = dynamodb.get_container("user-123", "oc-test")
    assert updated.status == "STOPPED"


def test_handle_task_event_missing_tags(aws_mocks):
    """Test handling ECS task event with missing tags."""
    event = {
        "detail": {
            "taskArn": "arn:aws:ecs:us-east-1:123456789:task/test",
            "lastStatus": "RUNNING",
            "tags": [],
        }
    }

    # Should not raise error
    ecs.handle_task_event(event)


def test_store_config_in_ssm():
    """Test storing config in SSM Parameter Store."""
    from app.services import config_store

    with patch("app.services.config_store._get_ssm_client") as mock_ssm:
        mock_ssm.return_value.put_parameter.return_value = {}

        config = {"key": "value"}
        result = config_store.store_config("user-123", "oc-test", config)

        assert result == "/voxhelm/orchestrator/user-123/oc-test"
        mock_ssm.return_value.put_parameter.assert_called_once()

"""
User configuration service for managing per-user configs in DynamoDB.

Stores two types of configs:
1. OpenClaw config (openclaw.json) - for OpenClaw gateway
2. Agent config (voxhelm.json) - for openclaw-agent

Both stored in the same DynamoDB table using single-table design:
- pk: USER#{user_id}, sk: CONFIG#{config_name} - Named user config
- pk: USER#{user_id}, sk: CONFIG#primary - Backward compatibility
- pk: SYSTEM, sk: CONFIG#defaults - System-wide defaults

Note: Encryption is skipped in initial implementation for simplicity.
Secrets are stored in plaintext for now.
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from app.services.dynamodb import _get_table


def _convert_decimals(obj: Any) -> Any:
    """
    Recursively convert Decimal objects to int or float.

    DynamoDB returns numbers as Decimal objects, but we want to return
    them as int/float for JSON serialization.
    """
    if isinstance(obj, Decimal):
        # Convert to int if it's a whole number, otherwise float
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    elif isinstance(obj, dict):
        return {k: _convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_decimals(item) for item in obj]
    else:
        return obj


class UserConfigService:
    """Service for managing user configurations."""

    def __init__(self):
        self.table = _get_table()

    def _process_raw_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a raw DynamoDB item into a configuration dictionary.

        Args:
            item: Raw DynamoDB item

        Returns:
            Dict containing processed config data with converted decimals
        """
        config = {}

        # Copy all fields except DynamoDB internal fields
        excluded_fields = {"pk", "sk", "config_type"}
        for key, value in item.items():
            if key not in excluded_fields and value is not None:
                # Convert Decimal objects to int/float for JSON serialization
                config[key] = _convert_decimals(value)

        return config

    def get_user_config(
        self, user_id: str, config_name: str = "default"
    ) -> Optional[Dict[str, Any]]:
        """
        Get user's configuration (plaintext, no encryption).

        Args:
            user_id: The user ID
            config_name: Named configuration (default: "default")

        Returns:
            Dict containing user config, or None if not found
        """
        # Try named config first
        response = self.table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": f"CONFIG#{config_name}"}
        )

        # Fallback to CONFIG#primary for backward compatibility
        if "Item" not in response and config_name == "default":
            response = self.table.get_item(
                Key={"pk": f"USER#{user_id}", "sk": "CONFIG#primary"}
            )

        if "Item" not in response:
            return None

        return self._process_raw_item(response["Item"])

    def save_user_config(
        self,
        user_id: str,
        config: Dict[str, Any],
        config_name: str = "default",
        overwrite: bool = False,
    ) -> Dict[str, str]:
        """
        Save user configuration (plaintext, no encryption).

        Args:
            user_id: The user ID
            config: Configuration dict with secrets in plaintext
            config_name: Named configuration (default: "default")
            overwrite: If False, merge with existing config; if True, replace completely
        """
        now = datetime.now(timezone.utc)

        # Get existing item data to preserve created_at
        raw_item = self.table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": f"CONFIG#{config_name}"}
        )
        existing_item_data = raw_item.get("Item")

        # If not overwriting, merge with existing config
        if not overwrite and existing_item_data:
            # Process existing item to get config data (avoids redundant get_item call)
            existing = self._process_raw_item(existing_item_data)
            # Merge: new values override old ones
            merged = {**existing, **config}
            config = merged
        # Note: When overwriting, put_item replaces the item entirely, no delete needed

        item = {
            "pk": f"USER#{user_id}",
            "sk": f"CONFIG#{config_name}",
            "config_type": "user_config",
            "user_id": user_id,
            "updated_at": now.isoformat(),
        }

        # Store all fields from config (supports arbitrary JSON fields)
        # Exclude any fields that shouldn't be stored or would conflict with DynamoDB keys
        excluded_from_config = {"pk", "sk", "config_type", "created_at", "updated_at"}
        for key, value in config.items():
            if key not in excluded_from_config and value is not None:
                item[key] = value

        # Preserve created_at from existing item (fetched earlier if not overwrite)
        if existing_item_data:
            item["created_at"] = existing_item_data.get("created_at", now.isoformat())
        else:
            item["created_at"] = now.isoformat()

        self.table.put_item(Item=item)

        # Return timestamps so caller doesn't need another database call
        return {
            "created_at": item["created_at"],
            "updated_at": item["updated_at"]
        }

    def get_system_config(self) -> Dict[str, Any]:
        """
        Get system-wide configuration.

        Values are resolved in priority order:
          1. Application settings / environment variables (deployment-time config is authoritative)
          2. DynamoDB system config record (SYSTEM / CONFIG#defaults)

        Env vars win over DynamoDB so that stale database values from local testing
        cannot override correctly-configured deployment environment variables.

        Returns:
            Dict containing system config (URLs, defaults, etc.)
        """
        from app.config import get_settings

        settings = get_settings()

        response = self.table.get_item(Key={"pk": "SYSTEM", "sk": "CONFIG#defaults"})
        item = response.get("Item", {})

        config = {
            # Explicit env var wins over DynamoDB; DynamoDB wins over settings default.
            # This prevents stale DynamoDB values (e.g. from local testing) from overriding
            # the correctly-configured env vars in a production deployment.
            "auth_gateway_url": os.environ.get("AUTH_GATEWAY_URL") or item.get("auth_gateway_url") or settings.auth_gateway_url,
            "openclaw_url": os.environ.get("OPENCLAW_URL") or item.get("openclaw_url") or settings.openclaw_url,
            "openclaw_token": os.environ.get("OPENCLAW_GATEWAY_TOKEN") or item.get("openclaw_token"),
            "voice_gateway_url": os.environ.get("VOICE_GATEWAY_URL") or item.get("voice_gateway_url") or settings.voice_gateway_url,
            "updated_at": item.get("updated_at"),
        }
        # Strip keys that are still None so callers get a clean dict
        config = {k: v for k, v in config.items() if v is not None}
        return _convert_decimals(config)

    def save_system_config(self, config: Dict[str, Any]) -> None:
        """
        Save system-wide configuration.

        Args:
            config: System configuration (URLs, tokens, etc.)
        """
        now = datetime.now(timezone.utc)

        item = {
            "pk": "SYSTEM",
            "sk": "CONFIG#defaults",
            "config_type": "system_config",
            "updated_at": now.isoformat(),
        }

        # Store system fields
        system_fields = [
            "auth_gateway_url",
            "openclaw_url",
            "openclaw_token",
            "voice_gateway_url",
        ]

        for field in system_fields:
            if field in config:
                item[field] = config[field]

        self.table.put_item(Item=item)

    def ensure_container_defaults(
        self,
        user_id: str,
        config_name: str,
        api_key: str,
    ) -> None:
        """Ensure a user config record exists with default LLM settings and the
        current API key.  Called by both ECS and k8s backends before launching a
        container so the container can fetch its config from the orchestrator.
        """
        from app.constants import DEFAULT_LLM_PROVIDER, DEFAULT_OPENCLAW_MODEL

        user_config = self.get_user_config(user_id, config_name) or {}
        if "llm_provider" not in user_config:
            user_config["llm_provider"] = DEFAULT_LLM_PROVIDER
        if "openclaw_model" not in user_config:
            user_config["openclaw_model"] = DEFAULT_OPENCLAW_MODEL
        user_config["auth_gateway_api_key"] = api_key
        self.save_user_config(user_id=user_id, config_name=config_name, config=user_config, overwrite=False)

    def build_openclaw_config(
        self, user_id: str, config_name: str = "default"
    ) -> Dict[str, Any]:
        """
        Build OpenClaw gateway config (openclaw.json).

        This is the config for the OpenClaw gateway service, which handles
        LLM inference. It needs provider API keys (stored in plaintext for now).

        Args:
            user_id: The user ID
            config_name: Named configuration (default: "default")

        Returns:
            Dict suitable for openclaw.json
        """
        user_config = self.get_user_config(user_id, config_name) or {}
        system_config = self.get_system_config()

        llm_provider = user_config.get("llm_provider", "anthropic")

        # Build provider config based on LLM provider
        providers = {}

        if llm_provider == "openrouter" and user_config.get("openrouter_api_key"):
            providers["openrouter"] = {
                "baseUrl": "https://openrouter.ai/api/v1",
                "apiKey": user_config["openrouter_api_key"],
                "api": "openai-completions",
                "models": [
                    {
                        "id": "anthropic/claude-haiku-4.5",
                        "name": "Claude Haiku 4.5 (OpenRouter)",
                        "api": "openai-completions",
                        "input": ["text"],
                        "cost": {
                            "input": 0.0008,
                            "output": 0.004,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "contextWindow": 200000,
                        "maxTokens": 8192,
                    }
                ],
            }
        elif llm_provider == "anthropic" and user_config.get("anthropic_api_key"):
            providers["anthropic"] = {
                "apiKey": user_config["anthropic_api_key"],
                "models": [
                    {
                        "id": "claude-3-haiku-20240307",
                        "name": "Claude 3 Haiku",
                    }
                ],
            }
        elif llm_provider == "openai" and user_config.get("openai_api_key"):
            providers["openai"] = {
                "apiKey": user_config["openai_api_key"],
                "models": [{"id": "gpt-4", "name": "GPT-4"}],
            }

        openclaw_token = system_config.get("openclaw_token") or os.environ.get(
            "OPENCLAW_GATEWAY_TOKEN"
        )
        if not openclaw_token:
            raise ValueError(
                "openclaw_token must be set in system config or "
                "OPENCLAW_GATEWAY_TOKEN environment variable"
            )

        return {
            "gateway": {
                "port": 18789,
                "mode": "local",
                "bind": "lan",
                "auth": {"mode": "token", "token": openclaw_token},
                "http": {"endpoints": {"chatCompletions": {"enabled": True}}},
            },
            "models": {"providers": providers},
            "agents": {
                "defaults": {
                    "model": {
                        "primary": f"{llm_provider}/{user_config.get('openclaw_model', 'claude-3-haiku-20240307')}"
                    }
                }
            },
        }

    def build_agent_config(
        self, user_id: str, config_name: str = "default"
    ) -> Dict[str, Any]:
        """
        Build openclaw-agent config (voxhelm.json).

        This is the config for the openclaw-agent service, which manages
        voice agents. It needs auth gateway credentials (from user config).

        Args:
            user_id: The user ID
            config_name: Named configuration (default: "default")

        Returns:
            Dict suitable for voxhelm.json
        """
        user_config = self.get_user_config(user_id, config_name) or {}
        system_config = self.get_system_config()

        openclaw_token = system_config.get("openclaw_token") or os.environ.get(
            "OPENCLAW_GATEWAY_TOKEN"
        )
        if not openclaw_token:
            raise ValueError(
                "openclaw_token must be set in system config or "
                "OPENCLAW_GATEWAY_TOKEN environment variable"
            )

        return {
            "agents": [],  # Will be populated after registration
            "user_id": user_id,
            "auth_gateway_url": system_config.get("auth_gateway_url"),
            "auth_gateway_api_key": user_config.get("auth_gateway_api_key", ""),
            "openclaw_url": system_config.get("openclaw_url"),
            "openclaw_token": openclaw_token,
            "openclaw_model": user_config.get(
                "openclaw_model", "claude-3-haiku-20240307"
            ),
            "llm_provider": user_config.get("llm_provider", "anthropic"),
            "anthropic_api_key": user_config.get("anthropic_api_key", ""),
            "openai_api_key": user_config.get("openai_api_key", ""),
        }

    def build_container_configs(
        self, user_id: str, config_name: str = "default"
    ) -> Dict[str, Dict[str, Any]]:
        """
        Build both configs needed for a container.

        Args:
            user_id: The user ID
            config_name: Named configuration (default: "default")

        Returns:
            Dict with keys "openclaw" and "agent" containing respective configs
        """
        return {
            "openclaw": self.build_openclaw_config(user_id, config_name),
            "agent": self.build_agent_config(user_id, config_name),
        }

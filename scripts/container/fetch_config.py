#!/usr/bin/env python3
"""
Fetch configuration from DynamoDB and write config files for container startup.

This script runs inside the container at startup to fetch user-specific configuration
from DynamoDB and write it to the appropriate config files:
- ~/.openclaw/openclaw.json - OpenClaw gateway config
- ~/.voxhelm/voxhelm.json - openclaw-agent config

Environment variables required:
- USER_ID: The user ID to fetch config for
- CONTAINER_ID: The container ID (for logging)
- CONFIG_NAME: Named configuration to use (default: "default")
- AWS_REGION: AWS region for DynamoDB (default: ap-southeast-2)
- DYNAMODB_ENDPOINT: Optional DynamoDB endpoint (for local dev)
- DYNAMODB_TABLE: DynamoDB table name (default: openclaw-containers)
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("ERROR: boto3 not installed. Install with: pip install boto3")
    sys.exit(1)


class ConfigFetcher:
    """Fetches configuration from DynamoDB."""

    def __init__(
        self,
        table_name: str,
        region: str = "ap-southeast-2",
        endpoint_url: Optional[str] = None,
    ):
        """
        Initialize the config fetcher.

        Args:
            table_name: DynamoDB table name
            region: AWS region
            endpoint_url: Optional DynamoDB endpoint for local development
        """
        self.table_name = table_name

        # Create DynamoDB resource
        kwargs = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
            # For local DynamoDB, use dummy credentials
            kwargs["aws_access_key_id"] = "local"
            kwargs["aws_secret_access_key"] = "local"

        self.dynamodb = boto3.resource("dynamodb", **kwargs)
        self.table = self.dynamodb.Table(table_name)

    def get_user_config(
        self, user_id: str, config_name: str = "default"
    ) -> Dict[str, Any]:
        """
        Fetch user configuration from DynamoDB.

        Args:
            user_id: The user ID
            config_name: Named configuration (default: "default")

        Returns:
            User config dict (secrets in plaintext)
        """
        try:
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
                print(
                    f"WARNING: No user config found for user_id={user_id}, config_name={config_name}"
                )
                return {}

            return dict(response["Item"])

        except ClientError as e:
            print(f"ERROR: Failed to fetch user config: {e}")
            return {}

    def get_system_config(self) -> Dict[str, Any]:
        """
        Fetch system-wide configuration from DynamoDB.

        Returns:
            System config dict
        """
        try:
            response = self.table.get_item(
                Key={"pk": "SYSTEM", "sk": "CONFIG#defaults"}
            )

            if "Item" not in response:
                print("WARNING: No system config found, using defaults")
                return {
                    "auth_gateway_url": "http://localhost:8001",
                    "openclaw_url": "http://localhost:18789",
                    "voice_gateway_url": "ws://localhost:9090",
                }

            return dict(response["Item"])

        except ClientError as e:
            print(f"ERROR: Failed to fetch system config: {e}")
            return {}

    def build_openclaw_config(
        self, user_config: Dict[str, Any], system_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build OpenClaw gateway configuration.

        Args:
            user_config: User-specific config (plaintext, no encryption)
            system_config: System-wide config

        Returns:
            OpenClaw config dict
        """
        llm_provider = user_config.get("llm_provider", "anthropic")

        # Read API keys directly (stored in plaintext for now)
        anthropic_key = user_config.get("anthropic_api_key", "")
        openrouter_key = user_config.get("openrouter_api_key", "")
        openai_key = user_config.get("openai_api_key", "")

        # Build providers config
        providers = {}

        if llm_provider == "openrouter" and openrouter_key:
            providers["openrouter"] = {
                "baseUrl": "https://openrouter.ai/api/v1",
                "apiKey": openrouter_key,
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
        elif llm_provider == "anthropic" and anthropic_key:
            providers["anthropic"] = {
                "baseUrl": "https://api.anthropic.com/v1",
                "apiKey": anthropic_key,
                "api": "anthropic-messages",
                "models": [
                    {
                        "id": "claude-3-haiku-20240307",
                        "name": "Claude 3 Haiku",
                        "api": "anthropic-messages",
                        "input": ["text"],
                        "contextWindow": 200000,
                        "maxTokens": 4096,
                    }
                ],
            }
        elif llm_provider == "openai" and openai_key:
            providers["openai"] = {
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": openai_key,
                "api": "openai-completions",
                "models": [
                    {
                        "id": "gpt-4",
                        "name": "GPT-4",
                        "api": "openai-completions",
                        "input": ["text"],
                        "contextWindow": 128000,
                        "maxTokens": 4096,
                    }
                ],
            }

        openclaw_model = user_config.get("openclaw_model", "claude-3-haiku-20240307")
        openclaw_token = system_config.get("openclaw_token")
        if not openclaw_token:
            raise ValueError("openclaw_token is required in system config")

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
                "defaults": {"model": {"primary": f"{llm_provider}/{openclaw_model}"}}
            },
        }

    def build_agent_config(
        self, user_id: str, user_config: Dict[str, Any], system_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build openclaw-agent configuration.

        Args:
            user_id: The user ID
            user_config: User-specific config (plaintext, no encryption)
            system_config: System-wide config

        Returns:
            Agent config dict
        """
        # Read API keys directly (stored in plaintext for now)
        auth_api_key = user_config.get("auth_gateway_api_key", "")
        anthropic_key = user_config.get("anthropic_api_key", "")
        openai_key = user_config.get("openai_api_key", "")
        openclaw_token = system_config.get("openclaw_token", "test-token-123")

        return {
            "agents": [],  # Will be populated after agent registration
            "user_id": user_id,
            "auth_gateway_url": system_config.get(
                "auth_gateway_url", "http://localhost:8001"
            ),
            "auth_gateway_api_key": auth_api_key,
            "openclaw_url": system_config.get("openclaw_url", "http://localhost:18789"),
            "openclaw_token": openclaw_token,
            "openclaw_model": user_config.get(
                "openclaw_model", "claude-3-haiku-20240307"
            ),
            "llm_provider": user_config.get("llm_provider", "anthropic"),
            "anthropic_api_key": anthropic_key,
            "openai_api_key": openai_key,
        }


def write_config_file(config: Dict[str, Any], path: Path) -> None:
    """
    Write configuration to a JSON file.

    Args:
        config: Configuration dict
        path: Path to write to
    """
    # Create parent directory
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write JSON
    with open(path, "w") as f:
        json.dump(config, f, indent=2)

    # Secure permissions (owner read/write only)
    path.chmod(0o600)

    print(f"✓ Config written to {path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch config from DynamoDB and write config files"
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("USER_ID"),
        help="User ID (default: from USER_ID env var)",
    )
    parser.add_argument(
        "--container-id",
        default=os.getenv("CONTAINER_ID"),
        help="Container ID for logging (default: from CONTAINER_ID env var)",
    )
    parser.add_argument(
        "--config-name",
        default=os.getenv("CONFIG_NAME", "default"),
        help="Named configuration to use (default: 'default')",
    )
    parser.add_argument(
        "--openclaw-config",
        type=Path,
        default=Path.home() / ".openclaw" / "openclaw.json",
        help="Path to write OpenClaw config (default: ~/.openclaw/openclaw.json)",
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=Path.home() / ".voxhelm" / "voxhelm.json",
        help="Path to write agent config (default: ~/.voxhelm/voxhelm.json)",
    )
    parser.add_argument(
        "--table",
        default=os.getenv("DYNAMODB_TABLE", "openclaw-containers"),
        help="DynamoDB table name (default: openclaw-containers)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "ap-southeast-2"),
        help="AWS region (default: ap-southeast-2)",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("DYNAMODB_ENDPOINT"),
        help="DynamoDB endpoint (for local dev)",
    )

    args = parser.parse_args()

    if not args.user_id:
        print("ERROR: --user-id is required (or set USER_ID env var)")
        sys.exit(1)

    print(
        f"=== Fetching config for user_id={args.user_id}, config_name={args.config_name} ==="
    )
    if args.container_id:
        print(f"Container ID: {args.container_id}")

    # Create fetcher
    fetcher = ConfigFetcher(
        table_name=args.table, region=args.region, endpoint_url=args.endpoint
    )

    # Fetch configs from DynamoDB
    print("\n[1/4] Fetching user config from DynamoDB...")
    user_config = fetcher.get_user_config(args.user_id, args.config_name)

    print("[2/4] Fetching system config from DynamoDB...")
    system_config = fetcher.get_system_config()

    # Build OpenClaw config
    print("[3/4] Building OpenClaw config...")
    openclaw_config = fetcher.build_openclaw_config(user_config, system_config)
    write_config_file(openclaw_config, args.openclaw_config)

    # Build agent config
    print("[4/4] Building openclaw-agent config...")
    agent_config = fetcher.build_agent_config(args.user_id, user_config, system_config)
    write_config_file(agent_config, args.agent_config)

    print("\n=== Config fetch completed successfully ===")
    print(f"OpenClaw config: {args.openclaw_config}")
    print(f"Agent config: {args.agent_config}")


if __name__ == "__main__":
    main()

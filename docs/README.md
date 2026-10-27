# Orchestrator Documentation

This directory contains the documentation for the orchestrator service.

## Quick Links

### Getting Started

- **[../README.md](../README.md)** - Main project README
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Deployment guide for AWS infrastructure
- **[CONFIG_API.md](CONFIG_API.md)** - Configuration API reference and parameter documentation

### Testing

- **[E2E_TEST_GUIDE.md](E2E_TEST_GUIDE.md)** - Complete guide for running end-to-end tests

### Implementation Details

- **[CONFIG_API.md](CONFIG_API.md)** - Configuration API reference with complete parameter documentation
- **[CONTAINER_REQUIREMENTS.md](CONTAINER_REQUIREMENTS.md)** - Requirements for container configuration based on openclaw-agent

## Documentation Structure

```
orchestrator/
├── README.md                          # Main README
├── docs/
│   ├── README.md                      # This file
│   ├── DEPLOYMENT.md                  # Deployment instructions
│   ├── CONFIG_API.md                  # Configuration API reference
│   ├── E2E_TEST_GUIDE.md             # Testing guide
│   └── CONTAINER_REQUIREMENTS.md      # Container requirements
├── scripts/
│   ├── README.md                      # Scripts documentation
│   └── load_defaults.py               # Load default configs into DynamoDB
└── test/
    └── README.md                      # Test documentation
```

## Key Features

### DynamoDB Configuration Storage

The orchestrator uses DynamoDB to store:
- **System Configuration** - Default settings for all containers
- **User Configuration** - User-specific API keys and preferences

Containers fetch their configuration on startup using `fetch_config.py`.

### Container Lifecycle

1. Container starts with minimal environment variables (USER_ID, CONTAINER_ID, AWS credentials)
2. Container runs `fetch_config.py` to retrieve config from DynamoDB
3. Two config files generated:
   - `openclaw.json` - OpenClaw gateway configuration
   - `voxhelm.json` - openclaw-agent configuration
4. Services start: OpenClaw → openclaw-agent
5. Agent registers with auth-gateway

### Testing

- **Local E2E Test** - `make test-e2e` (uses DynamoDB Local)
- **AWS E2E Test** - `make test-e2e-aws` (uses real AWS DynamoDB)

See [E2E_TEST_GUIDE.md](E2E_TEST_GUIDE.md) for details.

## Architecture

```
┌─────────────────────────────────────┐
│ AWS DynamoDB                         │
│  - System Config (SYSTEM#CONFIG)    │
│  - User Config (USER#{id}#CONFIG)   │
└────────────┬────────────────────────┘
             │ fetch_config.py
             ▼
┌─────────────────────────────────────┐
│ Container                            │
│  ┌──────────────────────────────┐   │
│  │ 1. Fetch Config from DynamoDB │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │ 2. Generate Config Files     │   │
│  │    - openclaw.json           │   │
│  │    - voxhelm.json           │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │ 3. Start Services            │   │
│  │    - OpenClaw (port 18789)   │   │
│  │    - openclaw-agent (8080)   │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │ 4. Register with Auth Gateway│   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

## Development Workflow

1. **Make changes** to code/config
2. **Run tests** with `make test` or `make test-e2e`
3. **Deploy** using `make deploy ENV=dev`
4. **Verify** with `make test-deploy ENV=dev`

See [DEPLOYMENT.md](DEPLOYMENT.md) for full deployment instructions.

## Contributing

When adding new documentation:
- Keep operational guides in `docs/`
- Keep code-specific docs with the code (e.g., `scripts/README.md`)
- Update this index when adding new documentation

## Related Documentation

- **[openclaw-agent](../../openclaw-agent/README.md)** - The agent that runs inside containers
- **[e2e tests](../../e2e/README.md)** - End-to-end integration tests

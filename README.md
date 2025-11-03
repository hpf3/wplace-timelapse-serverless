# WPlace Timelapse Serverless

This repository provides the Python runtime, capture worker, and storage abstractions for running the WPlace timelapse collectors on serverless infrastructure. The goal is to reuse the domain logic from the legacy project while making it trivial to deploy scheduled workers that push captures directly to S3-compatible storage (AWS S3, Cloudflare R2, etc.).

## Project layout

- `src/wplace_timelapse_serverless/` – core package containing configuration models, manifest types, storage adapters, and the capture runner.
- `tests/` – unit and smoke tests (placeholders today).
- `pyproject.toml` – project metadata and dependency list.

## Getting started

```bash
uv sync  # or python -m venv .venv && source .venv/bin/activate && pip install -e .
```

Run a local capture against a config file:

```bash
export WPLACE_CONFIG_PATH=/path/to/config.json
export WPLACE_BUCKET_NAME=timelapse-captures
export WPLACE_REGION=us-east-1

wplace-capture --slug example_region
```

The CLI uses environment variables and command-line overrides for credentials and bucket naming. See `wplace_timelapse_serverless/cli.py` for the full list of options.

## Design highlights

- **Delta manifests:** each capture writes a manifest that only lists tiles that changed, plus references to the previous manifest, keeping storage and Class A requests low.
- **Content-addressed tiles:** tiles are stored under keys derived from their SHA-256 digest, so duplicates across regions or captures are automatically deduplicated.
- **Pluggable storage:** the storage backend is abstracted. The default implementation targets S3-compatible APIs; you can swap in DynamoDB/KV-backed metadata stores by implementing the `StorageBackend` protocol.
- **Stateless workers:** the capture runner accepts explicit capture timestamps and previous-manifest handles so it can be deployed behind queues, schedulers, or cron triggers without shared state.

## Next steps

1. Flesh out storage adapters for the target platform (Cloudflare R2, AWS Lambda + S3, etc.).
2. Port the rendering pipeline to read manifests directly from S3 or mirror them locally before rendering.
3. Wire the CLI into your worker scheduler (EventBridge, Cloudflare Cron Triggers, etc.).

## Test secrets

If you run integration tests against a self-hosted S3-compatible server, copy `tests/.env.secrets.example` to `tests/.env.secrets` and fill in your endpoint, bucket, and credentials. The upcoming test suite will read those values to authenticate.

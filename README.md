# WPlace Timelapse Serverless

This repository provides the Python runtime, capture worker, and storage abstractions for running the WPlace timelapse collectors on serverless infrastructure. The goal is to reuse the domain logic from the legacy project while making it trivial to deploy scheduled workers that push captures directly to S3-compatible storage (AWS S3, Cloudflare R2, etc.).

## Project layout

- `src/wplace_timelapse_serverless/` – core package containing configuration models, manifest types, storage adapters, and the capture runner.
- `src/wplace_timelapse_serverless/web_gallery/` – static gallery generator for browsing stored tiles.
- `src/wplace_timelapse_serverless/video/` – timelapse video renderer that converts manifest history into frames and mp4 output.
- `cloudflare_worker/src/wplace_cloudflare_worker/` – Cloudflare Worker entrypoint that signs and proxies S3/R2 requests.
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

### Build the live gallery shell

Generate the HTML scaffold that streams manifests and tiles via your Cloudflare Worker (or any SigV4 proxy):

```bash
wplace-gallery build --slug example_region --asset-base-url https://worker.example.com --output ./site/index.html
# tweak --max-captures for longer timelines (default: 50)
```

No secrets are embedded in the output. At runtime, the page fetches `latest.json`, delta manifests, and tile PNGs through the worker, so new captures appear without rebuilding.

### Render a timelapse video

Compose frames from manifests and encode a video (requires `ffmpeg` in PATH):

```bash
wplace-video render --slug example_region --bucket your-bucket --output-dir ./renders
# set --frames-only to skip ffmpeg or --discard-frames to clean up PNGs afterwards
```

Frames and a `frames.json` manifest are written to `./renders/frames/` before encoding an H.264 mp4.

### Deploy the Cloudflare S3 proxy worker

The worker signs requests with AWS SigV4 so browsers (and the gallery) can fetch manifests/tiles without exposing credentials:

```bash
# wrangler.toml snippet
[vars]
S3_ENDPOINT = "https://s3.amazonaws.com"
S3_BUCKET = "your-bucket"
AWS_REGION = "us-east-1"
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
ALLOWED_ORIGINS = "https://example.com"
CACHE_TTL = "300"
STRIP_PREFIX = "static" # optional
VIRTUAL_HOSTED_STYLE = "1" # optional; set for AWS-hosted buckets
```

When deploying via Cloudflare Pages, run `python3 cloudflare_worker/build_worker.py` as the build
command and `python3 cloudflare_worker/deploy_worker.py` as the deploy command so only the lightweight
worker package (and `workers-py`) are bundled.

Expose the worker at a public hostname (e.g. `https://worker.example.com`) and use that URL as the `--asset-base-url` when building the gallery. The HTML shell then calls the worker for `manifests/<slug>/latest.json` and tile PNGs on demand. The route `/static/...` would be rewritten to the bucket object key after removing the prefix. Responses automatically add permissive CORS headers when `ALLOWED_ORIGINS` matches the request origin.

If you reuse the provided `gallery-deploy` workflow, store this worker URL in the `GALLERY_ASSET_BASE_URL` repository secret so the build step can inject it during publishing.

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

Run the capture integration test (optional, requires network access and a live tile source):

```bash
export RUN_CAPTURE_TEST=1
pytest tests/test_capture_integration.py
```

import os
import uuid
from datetime import datetime, timezone
from typing import Iterator, List

import boto3
import pytest
from botocore.exceptions import ClientError

from wplace_timelapse_serverless.capture import run_capture
from wplace_timelapse_serverless.config import Coordinates, TimelapseConfig
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend
from wplace_timelapse_serverless.tile_fetcher import TileFetcher


REQUIRED_ENV_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "WPLACE_TEST_S3_BUCKET",
]


def _should_run_integration() -> bool:
    if os.getenv("RUN_CAPTURE_TEST") not in {"1", "true", "TRUE"}:
        return False
    return all(os.getenv(var) for var in REQUIRED_ENV_VARS)


pytestmark = pytest.mark.skipif(
    not _should_run_integration(),
    reason="Provide S3 credentials in tests/.env.secrets (or set RUN_CAPTURE_TEST=1) to run capture integration.",
)


def _iter_objects(client, bucket: str, prefix: str) -> Iterator[List[dict]]:
    continuation_token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        contents = response.get("Contents") or []
        if contents:
            yield contents
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")


def _cleanup_prefix(client, bucket: str, prefix: str) -> None:
    for batch in _iter_objects(client, bucket, prefix):
        objects = [{"Key": entry["Key"]} for entry in batch]
        client.delete_objects(Bucket=bucket, Delete={"Objects": objects})


def _ensure_bucket(client, bucket: str, region: str | None) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        params = {"Bucket": bucket}
        if region and region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**params)


def test_capture_to_s3_backend():
    bucket = os.environ["WPLACE_TEST_S3_BUCKET"]
    region = os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    endpoint = os.getenv("WPLACE_TEST_ENDPOINT_URL")

    slug = f"integration-{uuid.uuid4().hex}"
    prefix = f"integration-tests/{uuid.uuid4().hex}"

    storage = S3StorageBackend(
        bucket=bucket,
        region=region,
        endpoint_url=endpoint,
        tile_prefix=f"{prefix}/tiles",
        manifest_prefix=f"{prefix}/manifests",
    )

    client = boto3.client("s3", region_name=region, endpoint_url=endpoint)
    _ensure_bucket(client, bucket, region)

    config = TimelapseConfig(
        slug=slug,
        name="Integration Test Region",
        coordinates=Coordinates(xmin=1000, xmax=1000, ymin=800, ymax=800),
    )

    fetcher = TileFetcher(
        base_url="https://backend.wplace.live/files/s0/tiles",
        request_delay=0.0,
    )

    try:
        outcome = run_capture(
            slug=slug,
            config=config,
            storage=storage,
            fetcher=fetcher,
            capture_time=datetime.now(timezone.utc),
            extra_metadata={"test_run": "true"},
        )

        manifest = storage.load_manifest(outcome.manifest)
        assert manifest.slug == slug
        assert manifest.metadata.get("test_run") == "true"
        assert manifest.tiles or manifest.failed_tiles
    finally:
        _cleanup_prefix(client, bucket, prefix)

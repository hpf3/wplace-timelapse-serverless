from datetime import datetime, timezone

from wplace_timelapse_serverless.manifest import (
    DeltaManifest,
    ManifestFailure,
    ManifestTile,
)


def test_manifest_roundtrip():
    manifest = DeltaManifest(
        slug="example",
        capture_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        previous_manifest=None,
        tiles=[
            ManifestTile(coordinate=(1, 2), object_key="tiles/example/a1/a1.png", checksum="abc", size=1024)
        ],
        failed_tiles=[
            ManifestFailure(coordinate=(3, 4), reason="network")
        ],
        metadata={"total_tiles": "4"},
    )

    payload = manifest.to_json()
    restored = DeltaManifest.from_json(payload)

    assert restored.slug == manifest.slug
    assert restored.capture_time == manifest.capture_time
    assert restored.tiles[0].coordinate == (1, 2)
    assert restored.tiles[0].checksum == "abc"
    assert restored.failed_tiles[0].reason == "network"

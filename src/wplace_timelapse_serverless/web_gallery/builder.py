"""Generate a dynamic HTML gallery that streams manifests and tiles at runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TypedDict

from wplace_timelapse_serverless.config import GlobalSettings, TimelapseConfig


@dataclass(slots=True)
class GalleryBuildOptions:
    """Options that influence gallery generation."""

    slug: str
    output_path: Path
    asset_base_url: str
    manifest_prefix: str
    latest_suffix: str = "latest.json"
    max_manifests: Optional[int] = None


class RuntimeCoordinates(TypedDict):
    xmin: int
    xmax: int
    ymin: int
    ymax: int
    width: int
    height: int


class RuntimePayload(TypedDict):
    slug: str
    name: str
    description: str
    assetBaseUrl: str
    manifestPrefix: str
    latestKey: str
    maxManifests: int | None
    coordinates: RuntimeCoordinates
    backgroundColor: list[int]


def build_gallery(
    *,
    timelapse: TimelapseConfig,
    global_settings: GlobalSettings,
    options: GalleryBuildOptions,
) -> Path:
    """Generate the gallery HTML shell and return the output path."""
    payload = _build_runtime_payload(timelapse=timelapse, global_settings=global_settings, options=options)
    html = _render_shell(payload)
    options.output_path.parent.mkdir(parents=True, exist_ok=True)
    options.output_path.write_text(html, encoding="utf-8")
    return options.output_path


def _build_runtime_payload(
    *,
    timelapse: TimelapseConfig,
    global_settings: GlobalSettings,
    options: GalleryBuildOptions,
) -> RuntimePayload:
    coordinates = timelapse.coordinates
    width = coordinates.width
    height = coordinates.height

    latest_key = f"{options.manifest_prefix.rstrip('/')}/{options.slug}/{options.latest_suffix}"

    return {
        "slug": options.slug,
        "name": timelapse.name,
        "description": timelapse.description or timelapse.slug,
        "assetBaseUrl": options.asset_base_url,
        "manifestPrefix": options.manifest_prefix.rstrip("/"),
        "latestKey": latest_key,
        "maxManifests": options.max_manifests,
        "coordinates": {
            "xmin": coordinates.xmin,
            "xmax": coordinates.xmax,
            "ymin": coordinates.ymin,
            "ymax": coordinates.ymax,
            "width": width,
            "height": height,
        },
        "backgroundColor": list(global_settings.background_color),
    }


def _render_shell(payload: RuntimePayload) -> str:
    config_json = json.dumps(payload, separators=(",", ":"))
    title = f"{payload['name']} – Live Tile Gallery"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{_escape_html(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      color-scheme: dark;
      --accent: #60a5fa;
      --bg-main: #020617;
      --bg-card: rgba(15, 23, 42, 0.75);
      --bg-header: rgba(7, 12, 22, 0.55);
      --border-soft: rgba(148, 163, 184, 0.25);
      --text-main: #e5e7eb;
      --text-muted: #94a3b8;
      --tile-shadow: rgba(96, 165, 250, 0.35);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at top, #1f2937, var(--bg-main));
      color: var(--text-main);
      display: flex;
      justify-content: center;
      padding: clamp(1rem, 3vw, 2.5rem);
    }}
    main {{
      width: min(1280px, 100%);
      background: var(--bg-card);
      backdrop-filter: blur(18px);
      border-radius: 24px;
      padding: clamp(1.5rem, 2vw + 1rem, 3rem);
      box-shadow: 0 30px 70px rgba(2, 6, 23, 0.6);
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }}
    header {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    header h1 {{
      margin: 0;
      font-size: clamp(2rem, 3.5vw, 3rem);
    }}
    header p {{
      margin: 0;
      color: var(--text-muted);
      max-width: 60ch;
    }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      align-items: center;
    }}
    select {{
      background: rgba(148, 163, 184, 0.12);
      color: inherit;
      border: 1px solid var(--border-soft);
      border-radius: 12px;
      padding: 0.75rem 1rem;
      font-size: 1rem;
      flex: 1 1 280px;
    }}
    .status {{
      color: var(--text-muted);
      font-size: 0.95rem;
    }}
    .card {{
      background: var(--bg-header);
      border-radius: 18px;
      padding: clamp(1rem, 1.5vw + 1rem, 2.25rem);
      box-shadow: inset 0 1px 0 rgba(148, 163, 184, 0.12);
    }}
    .tile-grid {{
      margin-top: 1.25rem;
      display: grid;
      grid-template-columns: repeat(var(--column-count), minmax(0, 1fr));
      gap: 4px;
      background: rgba(15, 23, 42, 0.6);
      padding: 6px;
      border-radius: 16px;
      overflow: hidden;
    }}
    .tile-grid img {{
      width: 100%;
      aspect-ratio: 1;
      object-fit: cover;
      border-radius: 12px;
      background: rgba(15, 23, 42, 0.8);
      transition: transform 140ms ease, box-shadow 140ms ease, border 140ms ease;
      border: 1px solid transparent;
    }}
    .tile-grid img.updated {{
      border-color: var(--accent);
      box-shadow: 0 10px 30px var(--tile-shadow);
    }}
    .tile-grid img:hover {{
      transform: translateY(-3px);
      box-shadow: 0 12px 32px rgba(2, 132, 199, 0.3);
    }}
    .tile-grid img.missing {{
      opacity: 0.25;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-top: 1rem;
      color: var(--text-muted);
      font-size: 0.95rem;
    }}
    .meta-item {{
      display: flex;
      flex-direction: column;
      min-width: 160px;
      gap: 0.25rem;
    }}
    .meta-item span:first-child {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.74rem;
      color: rgba(148, 163, 184, 0.8);
    }}
    details.failures {{
      margin-top: 1rem;
      background: rgba(185, 28, 28, 0.12);
      border: 1px solid rgba(248, 113, 113, 0.3);
      border-radius: 12px;
      padding: 1rem 1.25rem;
    }}
    details.failures summary {{
      cursor: pointer;
      font-weight: 600;
      color: #f87171;
    }}
    details.failures ul {{
      margin: 0.75rem 0 0;
      padding-left: 1.25rem;
    }}
    .hidden {{
      display: none !important;
    }}
    @media (max-width: 720px) {{
      .tile-grid {{
        gap: 2px;
        padding: 4px;
      }}
      .tile-grid img {{
        border-radius: 6px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{_escape_html(payload['name'])}</h1>
      <p>{_escape_html(payload['description'])}</p>
    </header>
    <section class="controls">
      <select data-role="capture-select" disabled>
        <option>Loading captures…</option>
      </select>
      <div class="status" data-role="status">Fetching manifest history…</div>
    </section>
    <section class="card">
      <div class="meta">
        <div class="meta-item">
          <span>Capture Time</span>
          <span data-role="capture-time">—</span>
        </div>
        <div class="meta-item">
          <span>Manifest Key</span>
          <span data-role="manifest-key">—</span>
        </div>
        <div class="meta-item">
          <span>Updated Tiles</span>
          <span data-role="changed-count">—</span>
        </div>
        <div class="meta-item">
          <span>Failed Tiles</span>
          <span data-role="failed-count">—</span>
        </div>
      </div>
      <div class="tile-grid" data-role="tile-grid"></div>
      <details class="failures hidden" data-role="failures">
        <summary>Failed tiles</summary>
        <ul data-role="failures-list"></ul>
      </details>
    </section>
  </main>

  <script type="application/json" id="gallery-config">{config_json}</script>
  <script>
    const CONFIG = JSON.parse(document.getElementById('gallery-config').textContent);

    const selectors = {{
      select: document.querySelector('[data-role="capture-select"]'),
      status: document.querySelector('[data-role="status"]'),
      grid: document.querySelector('[data-role="tile-grid"]'),
      captureTime: document.querySelector('[data-role="capture-time"]'),
      manifestKey: document.querySelector('[data-role="manifest-key"]'),
      changedCount: document.querySelector('[data-role="changed-count"]'),
      failedCount: document.querySelector('[data-role="failed-count"]'),
      failures: document.querySelector('[data-role="failures"]'),
      failuresList: document.querySelector('[data-role="failures-list"]'),
    }};

    const coords = CONFIG.coordinates;
    const galleryState = {{
      snapshots: [],
      newestFirst: [],
      assetBase: stripTrailingSlash(CONFIG.assetBaseUrl),
    }};

    applyThemeColor(CONFIG.backgroundColor);

    buildGrid();
    hydrateGallery().catch((error) => {{
      console.error('Failed to hydrate gallery', error);
      selectors.status.textContent = `Failed to load gallery: ${{error.message || error}}`;
    }});

    function buildGrid() {{
      const width = coords.width;
      const height = coords.height;
      selectors.grid.style.setProperty('--column-count', width);
      const fragment = document.createDocumentFragment();

      for (let y = coords.ymin; y <= coords.ymax; y += 1) {{
        for (let x = coords.xmin; x <= coords.xmax; x += 1) {{
          const img = document.createElement('img');
          img.dataset.coord = `${{x}},${{y}}`;
          img.alt = `Tile (${{x}}, ${{y}})`;
          img.decoding = 'async';
          img.loading = 'lazy';
          fragment.appendChild(img);
        }}
      }}

      selectors.grid.textContent = '';
      selectors.grid.appendChild(fragment);
    }}

    async function hydrateGallery() {{
      const pointer = await fetchJson(joinPath(galleryState.assetBase, CONFIG.latestKey));
      if (!pointer || !pointer.manifest_key) {{
        selectors.status.textContent = 'No manifests found for this slug.';
        return;
      }}

      const manifests = await fetchManifestChain(pointer.manifest_key);
      if (manifests.length === 0) {{
        selectors.status.textContent = 'No manifests found for this slug.';
        return;
      }}

      galleryState.snapshots = computeSnapshots(manifests);
      galleryState.newestFirst = [...galleryState.snapshots].sort((a, b) => new Date(b.captureTime) - new Date(a.captureTime));

      populateSelect(galleryState.newestFirst);
      selectors.status.textContent = `Loaded ${{galleryState.snapshots.length}} captures`;
      selectors.select.disabled = false;
      const initial = galleryState.newestFirst[0];
      applySnapshot(initial);
      selectors.select.value = String(initial.index);
    }}

    function populateSelect(items) {{
      selectors.select.textContent = '';
      for (const snapshot of items) {{
        const option = document.createElement('option');
        option.value = String(snapshot.index);
        option.textContent = `${{formatCapture(snapshot.captureTime)}}  •  ${{snapshot.changed}} changes`;
        selectors.select.appendChild(option);
      }}

      selectors.select.addEventListener('change', (event) => {{
        const index = Number(event.target.value);
        const snapshot = galleryState.snapshots.find((entry) => entry.index === index);
        if (snapshot) {{
          applySnapshot(snapshot);
        }}
      }});
    }}

    function applySnapshot(snapshot) {{
      const highlight = new Set(snapshot.changedTiles);
      for (const img of selectors.grid.querySelectorAll('img')) {{
        const key = img.dataset.coord;
        const tile = snapshot.board[key];
        if (tile) {{
          img.src = joinPath(galleryState.assetBase, tile.objectKey);
          img.classList.remove('missing');
        }} else {{
          img.removeAttribute('src');
          img.classList.add('missing');
        }}

        if (highlight.has(key)) {{
          img.classList.add('updated');
        }} else {{
          img.classList.remove('updated');
        }}
      }}

      selectors.captureTime.textContent = formatCapture(snapshot.captureTime);
      selectors.manifestKey.textContent = snapshot.manifestKey;
      selectors.changedCount.textContent = snapshot.changed.toString();
      selectors.failedCount.textContent = snapshot.failed.toString();

      if (snapshot.failed && snapshot.failed > 0) {{
        selectors.failures.classList.remove('hidden');
        selectors.failuresList.innerHTML = snapshot.failedTiles
          .map((coord) => `<li>(${coord[0]}, ${coord[1]}) — ${escapeHtml(coord[2] || 'failed')}</li>`)
          .join('');
      }} else {{
        selectors.failures.classList.add('hidden');
        selectors.failuresList.textContent = '';
      }}
    }}

    async function fetchManifestChain(initialKey) {{
      const manifests = [];
      let key = initialKey;
      const limit = Number.isFinite(CONFIG.maxManifests) && CONFIG.maxManifests > 0 ? CONFIG.maxManifests : Number.POSITIVE_INFINITY;

      while (key && manifests.length < limit) {{
        const manifest = await fetchJson(joinPath(galleryState.assetBase, key));
        manifests.push({{ key, manifest }});
        key = manifest.previous_manifest || null;
      }}

      manifests.reverse(); // oldest -> newest
      return manifests;
    }}

    function computeSnapshots(records) {{
      const board = new Map();
      const snapshots = [];

      records.forEach(({ key, manifest }, index) => {{
        const changedTiles = [];
        (manifest.tiles || []).forEach((tile) => {{
          const coordKey = tile.coordinate.join(',');
          board.set(coordKey, {{
            objectKey: tile.object_key,
            checksum: tile.checksum,
            size: tile.size,
          }});
          changedTiles.push(coordKey);
        }});

        const boardSnapshot = Object.fromEntries(board.entries());

        snapshots.push({{
          index,
          manifestKey: key,
          captureTime: manifest.capture_time,
          changed: changedTiles.length,
          failed: (manifest.failed_tiles || []).length,
          failedTiles: (manifest.failed_tiles || []).map((entry) => [entry.coordinate[0], entry.coordinate[1], entry.reason]),
          changedTiles,
          board: boardSnapshot,
          metadata: manifest.metadata || {{}},
        }});
      }});

      return snapshots;
    }}

    async function fetchJson(url) {{
      const response = await fetch(url, {{ mode: 'cors' }});
      if (!response.ok) {{
        throw new Error(`Request failed with status ${{response.status}}`);
      }}
      return response.json();
    }}

    function formatCapture(value) {{
      try {{
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) {{
          return value;
        }}
        return date.toLocaleString();
      }} catch (_) {{
        return value;
      }}
    }}

    function joinPath(base, path) {{
      if (!path) {{
        return base;
      }}
      const cleanBase = base.replace(/\\/+$/, '');
      const cleanPath = path.replace(/^\\/+/, '');
      return `${{cleanBase}}/${{cleanPath}}`;
    }}

    function stripTrailingSlash(value) {{
      return value.replace(/\\/+$/, '');
    }}

    function escapeHtml(value) {{
      return value.replace(/[&<>'"]/g, (char) => {{
        switch (char) {{
          case '&': return '&amp;';
          case '<': return '&lt;';
          case '>': return '&gt;';
          case '"': return '&quot;';
          case \"'\": return '&#39;';
          default: return char;
        }}
      }});
    }}

    function applyThemeColor(rgb) {{
      if (!Array.isArray(rgb) || rgb.length !== 3) {{
        return;
      }}
      const [r, g, b] = rgb.map((value) => Math.max(0, Math.min(255, Number(value))));
      const color = `rgb(${{r}}, ${{g}}, ${{b}})`;
      document.documentElement.style.setProperty('--bg-main', color);
    }}
  </script>
</body>
</html>
"""


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


__all__ = ["GalleryBuildOptions", "build_gallery"]

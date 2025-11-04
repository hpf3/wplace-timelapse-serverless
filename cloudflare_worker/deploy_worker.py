#!/usr/bin/env python3
"""Invoke pywrangler deploy from the worker package directory."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_DIR = ROOT / "cloudflare_worker"
VENV_DIR = ROOT / ".cloudflare-worker-venv"
BIN_DIR = VENV_DIR / ("Scripts" if os.name == "nt" else "bin")
PYWRANGLER = BIN_DIR / "pywrangler"


def main() -> int:
    if not PYWRANGLER.exists():
        sys.stderr.write(
            "pywrangler executable not found. Run build_worker.py before deploying.\n"
        )
        return 1

    env = os.environ.copy()
    # Ensure the venv is the active environment for pywrangler and wrangler subprocesses.
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    env["PATH"] = f"{BIN_DIR}{os.pathsep}{env.get('PATH', '')}"

    config_path = WORKER_DIR / "wrangler.toml"
    args = [str(PYWRANGLER), "deploy"]
    if config_path.exists():
        args.extend(["--config", str(config_path)])
    try:
        subprocess.check_call(args, cwd=WORKER_DIR, env=env)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

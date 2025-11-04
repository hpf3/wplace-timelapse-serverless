#!/usr/bin/env python3
"""Prepare a slim Cloudflare Python worker bundle."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_PACKAGE = ROOT / "cloudflare_worker"
PYTHON_MODULES = ROOT / "python_modules"
VENV_DIR = ROOT / ".cloudflare-worker-venv"


def run(cmd):
    subprocess.check_call(cmd, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default="python3")
    args = parser.parse_args()

    if PYTHON_MODULES.exists():
        if PYTHON_MODULES.is_dir():
            shutil.rmtree(PYTHON_MODULES)
        else:
            PYTHON_MODULES.unlink()
    PYTHON_MODULES.mkdir(parents=True)

    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)

    run([args.python, "-m", "venv", str(VENV_DIR)])

    bin_dir = "Scripts" if os.name == "nt" else "bin"
    pip_exe = VENV_DIR / bin_dir / "pip"
    python_exe = VENV_DIR / bin_dir / "python"

    run([str(pip_exe), "install", "--upgrade", "pip"])
    run([str(pip_exe), "install", "--target", str(PYTHON_MODULES), "workers-py"])

    run([str(pip_exe), "install", "-e", str(WORKER_PACKAGE)])

    # Remove CLI-only dependencies to keep the worker bundle slim.
    prune_paths = [
        "bin",
        "pywrangler",
        "click",
        "click-8.3.0.dist-info",
        "markdown_it",
        "markdown_it_py-4.0.0.dist-info",
        "mdurl",
        "mdurl-0.1.2.dist-info",
        "pygments",
        "pygments-2.19.2.dist-info",
        "pyjson5",
        "pyjson5-2.0.0.dist-info",
        "pyodide_cli",
        "pyodide_cli-0.4.0.dist-info",
        "rich",
        "rich-14.2.0.dist-info",
        "shellingham",
        "shellingham-1.5.4.dist-info",
        "typer",
        "typer-0.20.0.dist-info",
        "typing_extensions-4.15.0.dist-info",
        "typing_extensions.py",
        "__pycache__",
    ]
    for relative in prune_paths:
        target = PYTHON_MODULES / relative
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()

    # Ensure directories exist for consistency if removed.
    (PYTHON_MODULES / "__init__.py").touch()

    print(f"Prepared worker modules in {PYTHON_MODULES}")
    print(f"Installed editable worker package using virtualenv at {VENV_DIR}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys

import pytest

from wplace_timelapse_serverless.web_gallery import cli


def test_main_accepts_build_subcommand_alias(monkeypatch, capsys) -> None:
    # Simulate CLI invocation `wplace-gallery build --help`.
    monkeypatch.setattr(sys, "argv", ["wplace-gallery", "build", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    captured = capsys.readouterr()
    assert "Usage: wplace-gallery [OPTIONS]" in captured.out

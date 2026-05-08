from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofrepair.cli import main


def test_cli_compile_ok(passes_file: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["compile", str(passes_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[ok]" in out


def test_cli_compile_failure(tactic_error_file: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["compile", str(tactic_error_file)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out


def test_cli_verify_ok(passes_file: Path) -> None:
    assert main(["verify", str(passes_file)]) == 0


def test_cli_verify_fail(parse_error_file: Path) -> None:
    assert main(["verify", str(parse_error_file)]) == 1


def test_cli_compile_json(passes_file: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["compile", "--json", str(passes_file)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert "errors" in payload
    assert "compile_time_s" in payload


def test_cli_corrupt_is_stub(passes_file: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["corrupt", str(passes_file), "--rho", "0.1", "--op", "token_mask"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Phase 2" in err


def test_cli_sweep_is_stub(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    cfg = tmp_path / "sweep.yaml"
    cfg.write_text("models: []\n")
    rc = main(["sweep", str(cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Phase 6" in err

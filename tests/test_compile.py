from __future__ import annotations

from pathlib import Path

from proofrepair.lean import (
    CompileResult,
    LeanError,
    compile,
    compile_in_pool,
    toolchain_fingerprint,
)
from proofrepair.lean.cache import CompileCache


# ---------- single-file classification ---------------------------------------

def test_passes_file_compiles(passes_file: Path) -> None:
    result = compile(passes_file)
    assert isinstance(result, CompileResult)
    assert result.ok, f"expected ok=True, got errors={result.errors!r}"
    # No `error`-severity diagnostics.
    assert not [e for e in result.errors if e.severity == "error"]
    assert result.exit_code == 0
    assert result.compile_time_s > 0


def test_tactic_error_is_classified_as_failure(tactic_error_file: Path) -> None:
    result = compile(tactic_error_file)
    assert not result.ok
    errs = [e for e in result.errors if e.severity == "error"]
    assert errs, f"expected at least one error diagnostic, got {result.errors!r}"
    e = errs[0]
    assert e.line >= 1
    assert e.column >= 1
    # Lean's tactic-failure messages mention "unsolved goals" or similar.
    assert any("goal" in d.message.lower() or "rfl" in d.message.lower() for d in errs)


def test_parse_error_is_classified_as_failure(parse_error_file: Path) -> None:
    result = compile(parse_error_file)
    assert not result.ok
    errs = [e for e in result.errors if e.severity == "error"]
    assert errs, f"expected at least one error diagnostic, got {result.errors!r}"
    # Parse errors come from the elaborator with location info.
    assert errs[0].line >= 1


# ---------- timeout & missing-file paths -------------------------------------

def test_missing_file_returns_error_result(tmp_path: Path) -> None:
    result = compile(tmp_path / "does_not_exist.lean")
    assert not result.ok
    assert result.errors
    assert "not found" in result.errors[0].message


# ---------- pool -------------------------------------------------------------

def test_compile_in_pool_classifies_all_three(
    passes_file: Path, tactic_error_file: Path, parse_error_file: Path
) -> None:
    paths = [passes_file, tactic_error_file, parse_error_file]
    results = compile_in_pool(paths, max_workers=2)
    assert len(results) == 3
    by_name = {Path(p).name: r for p, r in results.items()}
    assert by_name["passes.lean"].ok
    assert not by_name["tactic_error.lean"].ok
    assert not by_name["parse_error.lean"].ok


# ---------- cache ------------------------------------------------------------

def test_cache_round_trip(tmp_path: Path, passes_file: Path) -> None:
    cache = CompileCache(db_path=tmp_path / "compile.sqlite")
    assert cache.lookup_path(passes_file) is None
    fresh = compile(passes_file)
    cache.store_path(passes_file, fresh)
    assert len(cache) == 1
    cached = cache.lookup_path(passes_file)
    assert cached is not None
    assert cached.ok == fresh.ok
    assert len(cached.errors) == len(fresh.errors)


def test_toolchain_fingerprint_is_stable() -> None:
    a = toolchain_fingerprint()
    b = toolchain_fingerprint()
    assert a == b
    assert len(a) == 64  # sha256 hex


# ---------- internal parser smoke test ---------------------------------------

def test_diagnostic_parser_handles_multiline_message() -> None:
    from proofrepair.lean.compile import _parse_diagnostics

    sample = (
        "/foo/bar.lean:3:7: error: unsolved goals\n"
        "⊢ 1 + 1 = 3\n"
        "/foo/bar.lean:5:1: warning: unused variable `x`\n"
    )
    diags = _parse_diagnostics(sample)
    assert len(diags) == 2
    assert diags[0].severity == "error"
    assert diags[0].line == 3 and diags[0].column == 7
    assert "1 + 1 = 3" in diags[0].message
    assert diags[1].severity == "warning"
    assert "unused variable" in diags[1].message

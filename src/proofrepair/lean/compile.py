from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

# A Lean diagnostic header looks like:
#   <file>:<line>:<col>: error: <first line of message>
# Continuation lines (the body of the message, often a goal print)
# follow until the next header or EOF. We use a non-greedy match on the
# file path so paths that themselves contain colons still parse.
_HEADER_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s+(?P<sev>error|warning|info):\s*(?P<msg>.*)$"
)


@dataclass
class LeanError:
    """A single diagnostic emitted by `lean`."""

    file: str
    line: int
    column: int
    severity: str  # "error" | "warning" | "info"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompileResult:
    """Outcome of running `lean` on a single file."""

    ok: bool
    errors: list[LeanError] = field(default_factory=list)
    compile_time_s: float = 0.0
    timed_out: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
            "compile_time_s": self.compile_time_s,
            "timed_out": self.timed_out,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompileResult":
        return cls(
            ok=d["ok"],
            errors=[LeanError(**e) for e in d.get("errors", [])],
            compile_time_s=d.get("compile_time_s", 0.0),
            timed_out=d.get("timed_out", False),
            exit_code=d.get("exit_code"),
            stdout=d.get("stdout", ""),
            stderr=d.get("stderr", ""),
        )


def _parse_diagnostics(text: str) -> list[LeanError]:
    """Parse Lean's plaintext error output into structured diagnostics."""
    errors: list[LeanError] = []
    current: LeanError | None = None
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            if current is not None:
                current.message = current.message.rstrip()
                errors.append(current)
            current = LeanError(
                file=m["file"],
                line=int(m["line"]),
                column=int(m["col"]),
                severity=m["sev"],
                message=m["msg"],
            )
        elif current is not None:
            current.message += "\n" + line
    if current is not None:
        current.message = current.message.rstrip()
        errors.append(current)
    return errors


@lru_cache(maxsize=1)
def toolchain_fingerprint() -> str:
    """SHA256 of `lean --version` output. Cached for a process lifetime."""
    out = subprocess.run(
        ["lean", "--version"], capture_output=True, text=True, check=False
    )
    blob = (out.stdout + out.stderr).encode()
    return hashlib.sha256(blob).hexdigest()


def file_fingerprint(path: Path) -> str:
    """SHA256 of the file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compile(
    path: str | os.PathLike,
    timeout: float = 30.0,
    cwd: str | os.PathLike | None = None,
    use_lake: bool = False,
) -> CompileResult:
    """Run `lean` (or `lake env lean`) on a single file and return a structured result.

    Parameters
    ----------
    path:
        Path to the .lean file to compile.
    timeout:
        Seconds before the subprocess is killed and a timeout result returned.
    cwd:
        Working directory for the subprocess. Defaults to the parent of `path`,
        which lets elan pick up a sibling `lean-toolchain` file if present.
    use_lake:
        If True, invokes `lake env lean` so the project's manifest (and any
        Mathlib dependency) is on the search path. If False, runs `lean`
        directly — faster for files with no external imports.
    """
    path = Path(path).resolve()
    if not path.exists():
        return CompileResult(
            ok=False,
            errors=[
                LeanError(
                    file=str(path),
                    line=0,
                    column=0,
                    severity="error",
                    message=f"file not found: {path}",
                )
            ],
            compile_time_s=0.0,
            exit_code=None,
        )

    workdir = Path(cwd).resolve() if cwd is not None else path.parent
    cmd = (
        ["lake", "env", "lean", str(path)]
        if use_lake
        else ["lean", str(path)]
    )

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        return CompileResult(
            ok=False,
            errors=[
                LeanError(
                    file=str(path),
                    line=0,
                    column=0,
                    severity="error",
                    message=f"timeout after {timeout:.1f}s",
                )
            ],
            compile_time_s=elapsed,
            timed_out=True,
            exit_code=None,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )

    elapsed = time.monotonic() - started
    combined = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
    diagnostics = _parse_diagnostics(combined)
    has_error = any(d.severity == "error" for d in diagnostics) or proc.returncode != 0
    return CompileResult(
        ok=not has_error,
        errors=diagnostics,
        compile_time_s=elapsed,
        timed_out=False,
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _compile_one_for_pool(args: tuple) -> tuple[str, dict]:
    """Picklable worker for ProcessPoolExecutor: takes (path, timeout, cwd, use_lake)
    and returns (path, result_dict). We avoid passing dataclasses across the pool
    boundary directly because dataclasses defined in this module are picklable
    only if the worker can re-import them — which it can, since it imports the
    same module to call this function. We still serialize to dict for safety
    against future refactors."""
    path, timeout, cwd, use_lake = args
    result = compile(path, timeout=timeout, cwd=cwd, use_lake=use_lake)
    return path, result.to_dict()


def compile_in_pool(
    paths: Sequence[str | os.PathLike],
    timeout: float = 30.0,
    cwd: str | os.PathLike | None = None,
    use_lake: bool = False,
    max_workers: int | None = None,
) -> dict[str, CompileResult]:
    """Compile many files in parallel via a process pool.

    Returns a dict keyed by the resolved-path string of each input file.
    Default worker count is `min(len(paths), os.cpu_count() - 1)` (>= 1).
    """
    paths = [str(Path(p).resolve()) for p in paths]
    if not paths:
        return {}

    if max_workers is None:
        cpu = os.cpu_count() or 2
        max_workers = max(1, min(len(paths), cpu - 1))

    args_list = [(p, timeout, cwd, use_lake) for p in paths]
    out: dict[str, CompileResult] = {}
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_compile_one_for_pool, a): a[0] for a in args_list}
        for fut in as_completed(futures):
            p, result_dict = fut.result()
            out[p] = CompileResult.from_dict(result_dict)
    return out

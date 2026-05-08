from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from proofrepair.lean.compile import (
    CompileResult,
    file_fingerprint,
    toolchain_fingerprint,
)


_DEFAULT_CACHE_DIR = Path(".proofrepair_cache")
_DEFAULT_DB_NAME = "compile.sqlite"


def default_cache_path() -> Path:
    """Default cache location: <repo>/.proofrepair_cache/compile.sqlite, where
    <repo> is the current working directory at first call."""
    base = Path(os.environ.get("PROOFREPAIR_CACHE_DIR", _DEFAULT_CACHE_DIR))
    base.mkdir(parents=True, exist_ok=True)
    return base / _DEFAULT_DB_NAME


_SCHEMA = """
CREATE TABLE IF NOT EXISTS compile_cache (
    file_sha       TEXT NOT NULL,
    toolchain_sha  TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    cached_at      REAL NOT NULL,
    PRIMARY KEY (file_sha, toolchain_sha)
);
"""


class CompileCache:
    """SQLite-backed cache keyed by (file_sha, toolchain_sha).

    Threads can share an instance; each call opens a short-lived connection
    so we don't have to manage thread-locals. Process-level concurrency is
    handled by SQLite's default locking.
    """

    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path) if db_path is not None else default_cache_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, file_sha: str, toolchain_sha: str) -> CompileResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM compile_cache WHERE file_sha = ? AND toolchain_sha = ?",
                (file_sha, toolchain_sha),
            ).fetchone()
        if row is None:
            return None
        return CompileResult.from_dict(json.loads(row[0]))

    def put(self, file_sha: str, toolchain_sha: str, result: CompileResult) -> None:
        import time

        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO compile_cache "
                "(file_sha, toolchain_sha, result_json, cached_at) VALUES (?, ?, ?, ?)",
                (file_sha, toolchain_sha, json.dumps(result.to_dict()), time.time()),
            )
            conn.commit()

    def lookup_path(self, path: Path) -> CompileResult | None:
        """Convenience: look up a result by hashing the file at `path` against
        the current toolchain fingerprint."""
        return self.get(file_fingerprint(path), toolchain_fingerprint())

    def store_path(self, path: Path, result: CompileResult) -> None:
        self.put(file_fingerprint(path), toolchain_fingerprint(), result)

    def __len__(self) -> int:
        with self._connect() as conn:
            (n,) = conn.execute("SELECT COUNT(*) FROM compile_cache").fetchone()
        return int(n)

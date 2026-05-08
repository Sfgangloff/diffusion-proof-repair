from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "lean"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def passes_file() -> Path:
    return FIXTURES / "passes.lean"


@pytest.fixture
def tactic_error_file() -> Path:
    return FIXTURES / "tactic_error.lean"


@pytest.fixture
def parse_error_file() -> Path:
    return FIXTURES / "parse_error.lean"

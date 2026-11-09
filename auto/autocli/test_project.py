"""Rules about the code base itself that no linter checks"""

import re
from pathlib import Path

import pytest
import toml

from autocli import commands

ROOT = Path(__file__).resolve().parents[2]

# pylint's default limit, kept when pylint was replaced by ruff, which has no
# such rule. A module this long wants splitting.
MAX_MODULE_LINES = 1000


@pytest.mark.parametrize(
    "module", sorted(ROOT.joinpath("auto").rglob("*.py")), ids=lambda p: p.name
)
def test_modules_stay_under_the_line_limit(module):
    """Split a module rather than raise the limit"""
    lines = len(module.read_text(encoding="utf-8").splitlines())
    assert lines <= MAX_MODULE_LINES, f"{module.name} has {lines} lines"


def test_pyproject_version_matches_the_cli():
    """uv needs a version in pyproject.toml; auto --version reads commands.VERSION"""
    version = toml.load(ROOT / "pyproject.toml")["project"]["version"]
    assert version == commands.VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)

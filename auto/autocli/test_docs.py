"""Tests that keep the documentation site in step with the CLI."""

import re
from pathlib import Path

import click

from autocli.commands import auto

DOCS = Path(__file__).resolve().parents[2] / "docs"
COMMANDS_PAGE = DOCS / "reference" / "commands.md"


def _command_sections():
    """Map each `### `auto <name> ...`` heading on the commands page to its text."""
    sections = {}
    current = None
    for line in COMMANDS_PAGE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{2,4} `auto ([a-z-]+)", line)
        if match:
            current = match.group(1)
            sections[current] = ""
        elif line.startswith("#"):
            current = None
        elif current:
            sections[current] += line + "\n"
    return sections


def test_every_command_is_documented():
    """A new command needs a section on docs/reference/commands.md."""
    missing = set(auto.commands) - set(_command_sections())
    assert not missing, f"add these to {COMMANDS_PAGE.name}: {sorted(missing)}"


def test_every_option_is_documented():
    """Each option a command takes appears in that command's section."""
    sections = _command_sections()
    for name, command in auto.commands.items():
        for param in command.params:
            if not isinstance(param, click.Option):
                continue
            long_flags = [opt for opt in param.opts if opt.startswith("--")]
            assert any(f"`{flag}`" in sections.get(name, "") for flag in long_flags), (
                f"auto {name}: document {long_flags} in {COMMANDS_PAGE.name}"
            )


def test_no_documented_command_is_gone():
    """A removed command's section goes too."""
    stale = set(_command_sections()) - set(auto.commands)
    assert not stale, (
        f"{COMMANDS_PAGE.name} documents removed commands: {sorted(stale)}"
    )

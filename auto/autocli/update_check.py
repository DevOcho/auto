"""Once-a-day check for a newer release of auto

Runs when a command finishes (wired up in commands.auto) and asks GitHub for
the latest release at most once every CHECK_INTERVAL. The notice only shows on
the run that did the check, so someone who isn't ready to update hears about
it once a day rather than after every command.
"""

import json
import os
import sys
import time

import requests
from rich.console import Console

LATEST_URL = "https://api.github.com/repos/devocho/auto/releases/latest"

# When we last asked GitHub, so the check costs one request a day
CACHE_PATH = os.path.expanduser("~/.auto/cache/update_check.json")
CHECK_INTERVAL = 24 * 60 * 60

# Short enough that a slow or offline network barely delays getting the prompt back
TIMEOUT = 2

# Set this to anything to turn the check off entirely
OPT_OUT_ENV = "AUTO_NO_UPDATE_CHECK"

# Commands where a "new version" notice would be redundant
SKIP_COMMANDS = {"update"}


def parse_version(raw):
    """Turn "v0.8.1" or "0.8.1" into (0, 8, 1), or None if it isn't X.Y.Z

    Versions compare as tuples so 0.8.10 sorts after 0.8.9, which comparing
    the strings gets wrong.
    """

    text = str(raw or "")
    if text.startswith("v"):
        text = text[1:]

    parts = text.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def should_check(subcommand) -> bool:
    """Decide whether this run should look for a newer release at all

    Only interactive runs check. A script or pipe (`auto images > registry.yaml`)
    shouldn't get a notice mixed into what it captures, and click's CliRunner
    isn't a terminal either, so the test suite never touches the network.
    """

    if subcommand in SKIP_COMMANDS:
        return False
    if os.environ.get(OPT_OUT_ENV) or "_AUTO_COMPLETE" in os.environ:
        return False
    return sys.stdout.isatty() and sys.stderr.isatty()


def _load_cache() -> dict:
    """Read when we last checked, tolerating a missing or corrupt cache file"""

    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(data) -> None:
    """Remember this check. Failures are ignored -- worst case we check again"""

    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError:
        pass


def fetch_latest():
    """Ask GitHub for the newest release's version tuple, or None if we can't tell"""

    try:
        response = requests.get(LATEST_URL, timeout=TIMEOUT)
        response.raise_for_status()
        return parse_version(response.json().get("tag_name"))
    except (requests.RequestException, ValueError, AttributeError):
        return None


def newer_release(current):
    """Return the newer release as "X.Y.Z" if today's check found one, else None

    Hits the network at most once per CHECK_INTERVAL. A failed check still
    counts as the day's attempt, otherwise an offline laptop would wait out
    the timeout after every single command.
    """

    now = time.time()
    checked_at = _load_cache().get("checked_at")

    # A timestamp in the future means the clock moved, so treat it as stale
    if isinstance(checked_at, (int, float)) and 0 <= now - checked_at < CHECK_INTERVAL:
        return None

    latest = fetch_latest()
    _save_cache({"checked_at": now})

    running = parse_version(current)
    if latest is None or running is None or latest <= running:
        return None
    return ".".join(str(part) for part in latest)


def notify_if_outdated(current) -> None:
    """Print a one-line notice when a newer release is out (see newer_release)"""

    # Ctrl+C means get me out now, not after a network round trip.  This runs
    # while click unwinds the command, so the interrupt is still in flight.
    if isinstance(sys.exc_info()[1], KeyboardInterrupt):
        return

    latest = newer_release(current)
    if not latest:
        return

    # The notice goes to stderr, so flush the command's own output first to
    # make sure the notice lands after it
    sys.stdout.flush()
    Console(stderr=True).print(
        f"\n[yellow]A new version of auto is available: {current} → {latest}. "
        "Run 'auto update' to upgrade.[/]",
        # rich's number highlighting would bold "0.8" and "1" separately
        highlight=False,
    )

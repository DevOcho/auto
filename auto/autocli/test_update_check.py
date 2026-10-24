"""Tests for auto.autocli.update_check"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from autocli import update_check


@pytest.fixture(name="cache_path")
def fixture_cache_path(tmp_path):
    """Point the check's cache at a temp file so tests never touch ~/.auto"""
    path = tmp_path / "cache" / "update_check.json"
    with patch.object(update_check, "CACHE_PATH", str(path)):
        yield path


def _write_cache(path, checked_at):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"checked_at": checked_at}), encoding="utf-8")


def test_parse_version():
    """Tags and plain versions parse, compare numerically, and junk is rejected"""
    assert update_check.parse_version("v0.8.1") == (0, 8, 1)
    assert update_check.parse_version("0.8.1") == (0, 8, 1)
    assert update_check.parse_version("0.8.10") > update_check.parse_version("0.8.9")
    for junk in (None, "", "latest", "0.8", "0.8.1-rc1", "v0.x.1"):
        assert update_check.parse_version(junk) is None


@patch("autocli.update_check.sys")
def test_should_check_only_interactive_runs(mock_sys, monkeypatch):
    """Pipes, auto update itself, completion and the opt-out all skip the check"""
    monkeypatch.delenv(update_check.OPT_OUT_ENV, raising=False)
    monkeypatch.delenv("_AUTO_COMPLETE", raising=False)
    mock_sys.stdout.isatty.return_value = True
    mock_sys.stderr.isatty.return_value = True

    assert update_check.should_check("status") is True
    assert update_check.should_check("update") is False

    monkeypatch.setenv(update_check.OPT_OUT_ENV, "1")
    assert update_check.should_check("status") is False
    monkeypatch.delenv(update_check.OPT_OUT_ENV)

    mock_sys.stdout.isatty.return_value = False
    assert update_check.should_check("status") is False


@patch("autocli.update_check.fetch_latest")
def test_recent_check_skips_the_network(mock_fetch, cache_path):
    """Within a day of the last check we don't ask GitHub again"""
    _write_cache(cache_path, time.time() - 60)

    assert update_check.newer_release("0.8.1") is None
    mock_fetch.assert_not_called()


@patch("autocli.update_check.fetch_latest", return_value=(0, 9, 0))
def test_stale_check_reports_newer_release(mock_fetch, cache_path):
    """A day-old check asks again, reports the newer release, and resets the clock"""
    _write_cache(cache_path, time.time() - update_check.CHECK_INTERVAL - 1)

    assert update_check.newer_release("0.8.1") == "0.9.0"
    mock_fetch.assert_called_once()

    # The notice only shows on the run that checked
    assert update_check.newer_release("0.8.1") is None
    mock_fetch.assert_called_once()


@patch("autocli.update_check.fetch_latest", return_value=(0, 8, 1))
def test_first_run_with_no_cache_checks(mock_fetch, cache_path):
    """No cache file means we've never checked, and being current says nothing"""
    assert update_check.newer_release("0.8.1") is None
    mock_fetch.assert_called_once()
    assert cache_path.exists()


@pytest.mark.usefixtures("cache_path")
@patch("autocli.update_check.fetch_latest", return_value=(0, 8, 1))
def test_source_build_ahead_of_release_is_quiet(_mock_fetch):
    """Running something newer than the latest release isn't 'behind'"""
    assert update_check.newer_release("0.9.0") is None


@pytest.mark.usefixtures("cache_path")
@patch("autocli.update_check.fetch_latest", return_value=None)
def test_failed_check_still_counts_for_the_day(mock_fetch):
    """Offline: one failed attempt, not a timeout after every command"""
    assert update_check.newer_release("0.8.1") is None
    assert update_check.newer_release("0.8.1") is None
    mock_fetch.assert_called_once()


@patch("autocli.update_check.fetch_latest", return_value=(0, 9, 0))
def test_future_timestamp_is_treated_as_stale(mock_fetch, cache_path):
    """If the clock went backwards we check rather than wait it out"""
    _write_cache(cache_path, time.time() + update_check.CHECK_INTERVAL)

    assert update_check.newer_release("0.8.1") == "0.9.0"
    mock_fetch.assert_called_once()


def test_corrupt_cache_is_treated_as_no_cache(cache_path):
    """A garbled cache file doesn't break the command it's attached to"""
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("{not json", encoding="utf-8")

    with patch("autocli.update_check.fetch_latest", return_value=None) as mock_fetch:
        update_check.newer_release("0.8.1")
    mock_fetch.assert_called_once()


@patch("autocli.update_check.requests.get")
def test_fetch_latest(mock_get):
    """A good payload parses; network errors and odd payloads come back as None"""
    mock_get.return_value = MagicMock(json=lambda: {"tag_name": "v0.9.0"})
    assert update_check.fetch_latest() == (0, 9, 0)
    assert mock_get.call_args.kwargs["timeout"] == update_check.TIMEOUT

    mock_get.return_value = MagicMock(json=lambda: ["not", "a", "dict"])
    assert update_check.fetch_latest() is None

    mock_get.side_effect = requests.ConnectionError
    assert update_check.fetch_latest() is None


@patch("autocli.update_check.Console")
@patch("autocli.update_check.newer_release", return_value="0.9.0")
def test_notify_prints_to_stderr(_mock_newer, mock_console):
    """The notice names both versions and how to update"""
    update_check.notify_if_outdated("0.8.1")

    mock_console.assert_called_once_with(stderr=True)
    message = mock_console.return_value.print.call_args.args[0]
    assert "0.8.1 → 0.9.0" in message
    assert "auto update" in message


@patch("autocli.update_check.newer_release")
def test_notify_skips_after_ctrl_c(mock_newer):
    """Ctrl+C out of auto logs shouldn't then wait on GitHub"""
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        update_check.notify_if_outdated("0.8.1")
    mock_newer.assert_not_called()

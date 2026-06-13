"""Tests for auto.autocli.checks (dependency/host preflight checks)."""

from unittest.mock import patch

from autocli import checks


@patch("autocli.checks.run_and_wait")
@patch("autocli.checks.declare_error")
def test_check_docker(mock_declare_error, mock_run):
    """Test docker dependency check"""
    mock_run.side_effect = [True, True, True]
    errors = checks.check_docker()
    assert errors == 0

    mock_run.side_effect = [False, False, False]
    errors = checks.check_docker()
    assert errors == 3
    mock_declare_error.assert_called()


@patch("autocli.checks.declare_error")
@patch("autocli.checks.run_and_wait")
def test_check_helm_present_is_no_error(mock_run, mock_declare_error):
    """Helm installed: no warning, no error."""
    mock_run.return_value = 1  # `helm version` matched "clean"
    assert checks.check_helm() == 0
    mock_declare_error.assert_not_called()


@patch("autocli.checks.rprint")
@patch("autocli.checks.declare_error")
@patch("autocli.checks.run_and_wait")
def test_check_helm_missing_is_optional(mock_run, mock_declare_error, mock_rprint):
    """Helm is optional: a missing helm warns but never blocks startup.

    Regression test for #15 — `auto start` must not abort just because helm
    isn't installed, since pods can deploy via raw kubectl manifests.
    """
    mock_run.return_value = 0  # `helm version` failed / not found
    # Returns 0 (no fatal error) and does NOT route through declare_error.
    assert checks.check_helm() == 0
    mock_declare_error.assert_not_called()
    mock_rprint.assert_called_once()  # a yellow warning was shown instead

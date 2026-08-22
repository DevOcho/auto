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


@patch("autocli.checks.socket.gethostbyname", return_value="127.0.0.1")
def test_insecure_registry_check_silent_for_loopback(_mock_resolve):
    """Docker trusts 127.0.0.0/8 by default, so a loopback registry needs no config."""
    with patch("autocli.checks.rprint") as mock_print:
        assert checks.check_docker_insecure_registry() == 0
    mock_print.assert_not_called()


@patch("os.path.isfile", return_value=False)
@patch("autocli.checks.socket.gethostbyname", return_value="192.168.1.50")
def test_insecure_registry_check_warns_but_never_blocks(_mock_resolve, _mock_isfile):
    """A non-loopback registry with no daemon.json entry warns without failing the start."""
    with patch("autocli.checks.rprint") as mock_print:
        assert checks.check_docker_insecure_registry() == 0
    assert any(
        "insecure registry" in str(call.args[0]) for call in mock_print.call_args_list
    )

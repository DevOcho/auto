"""Tests for auto.autocli.https (local HTTPS cert + nginx-ingress wiring)."""

# pylint: disable=protected-access

from unittest.mock import patch

from autocli import https


@patch("autocli.utils.run_and_return")
def test_discover_ingress_hosts_dedupes_and_sorts(mock_return):
    """All ingress hosts are collected, de-duplicated, and returned sorted."""
    # A single pod exposing two hosts, with the same host repeated in tls.
    mock_return.return_value = (
        "portal.new-d8.local\nnew-d8.local\nportal.new-d8.local\n"
    )
    assert https.discover_ingress_hosts() == ["new-d8.local", "portal.new-d8.local"]
    assert "kubectl get ingress --all-namespaces" in mock_return.call_args[0][0]


@patch("autocli.utils.run_and_return")
def test_discover_ingress_hosts_empty(mock_return):
    """No ingresses (or a failed query) yields an empty list, not a crash."""
    mock_return.return_value = ""
    assert https.discover_ingress_hosts() == []


@patch("autocli.https.warn_missing_host_entries")
@patch("autocli.https._update_tls_secrets")
@patch("autocli.https.create_local_certs")
@patch("autocli.https.discover_ingress_hosts")
def test_refresh_https_reissues_cert_for_all_hosts(
    mock_discover, mock_certs, mock_update, mock_warn
):
    """The cert is re-issued with every discovered host in the SAN list."""
    mock_discover.return_value = ["new-d8.local", "portal.new-d8.local"]
    mock_certs.return_value = ("key.pem", "cert.pem")

    https.refresh_https_for_ingresses()

    assert mock_certs.call_args.kwargs["additional_domains"] == [
        "new-d8.local",
        "portal.new-d8.local",
    ]
    mock_update.assert_called_once_with("key.pem", "cert.pem")
    mock_warn.assert_called_once_with(["new-d8.local", "portal.new-d8.local"])


@patch("autocli.https._update_tls_secrets")
@patch("autocli.https.create_local_certs")
@patch("autocli.https.discover_ingress_hosts")
def test_refresh_https_noop_without_hosts(mock_discover, mock_certs, mock_update):
    """With no ingresses found, nothing is re-issued or applied."""
    mock_discover.return_value = []

    https.refresh_https_for_ingresses()

    mock_certs.assert_not_called()
    mock_update.assert_not_called()


@patch("autocli.utils.run_and_wait")
@patch("autocli.https.rprint")
@patch("shutil.which", return_value=None)
def test_install_nginx_ingress_skips_when_helm_absent(
    _mock_which, mock_rprint, mock_run
):
    """Without helm on PATH, the install is skipped with a yellow advisory.

    Nothing should reach a subprocess -- helm is optional, so a missing helm
    warns rather than erroring out mid-install.
    """
    https.install_nginx_ingress(False, None, None)

    mock_run.assert_not_called()
    advisory_calls = [str(call.args[0]) for call in mock_rprint.call_args_list]
    assert any("helm not found" in msg for msg in advisory_calls)
    assert any("[yellow]" in msg for msg in advisory_calls)


@patch("autocli.utils.run_and_return")
def test_warn_missing_host_entries_reports_only_missing(mock_return):
    """Only hosts absent from /etc/hosts are flagged."""
    mock_return.return_value = "127.0.0.1 new-d8.local\n"
    with patch("autocli.https.rprint") as mock_print:
        https.warn_missing_host_entries(["new-d8.local", "portal.new-d8.local"])

    # Only the missing host gets its own "127.0.0.1 <host>" suggestion line.
    host_lines = [
        str(call.args[0])
        for call in mock_print.call_args_list
        if "127.0.0.1" in str(call.args[0])
    ]
    assert len(host_lines) == 1
    assert host_lines[0].endswith("portal.new-d8.local")

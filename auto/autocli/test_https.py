"""Tests for auto.autocli.https (local HTTPS cert + Envoy Gateway wiring)."""

# pylint: disable=protected-access

import os
from unittest.mock import MagicMock, patch

import pytest
import yaml
from autocli import https

# The manifests as shipped (auto/k3s/envoy-gateway), not the installed copy
REPO_MANIFESTS = os.path.join(os.path.dirname(__file__), "..", "k3s", "envoy-gateway")


def _docs(use_https):
    """base.yaml plus the Gateway file auto would apply, keyed by kind"""
    gateway = "gateway-https.yaml" if use_https else "gateway-http.yaml"
    docs = {}
    for name in ("base.yaml", gateway):
        with open(os.path.join(REPO_MANIFESTS, name), encoding="utf-8") as manifest:
            for doc in yaml.safe_load_all(manifest):
                docs[doc["kind"]] = doc
    return docs


@patch("autocli.utils.run_and_return")
def test_discover_ingress_hosts_dedupes_and_sorts(mock_return):
    """All route hosts are collected, de-duplicated, and returned sorted."""
    # A single pod exposing two hosts, with one host repeated by a second route.
    mock_return.return_value = (
        "portal.new-d8.local\nnew-d8.local\nportal.new-d8.local\n"
    )
    assert https.discover_ingress_hosts() == ["new-d8.local", "portal.new-d8.local"]
    cmd = mock_return.call_args[0][0]
    assert "kubectl get httproutes.gateway.networking.k8s.io --all-namespaces" in cmd
    # Leftover Ingress objects are not served, so their hosts must not be listed
    assert "get ingress" not in cmd


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
@patch("autocli.https._envoy_gateway_manifest", return_value=None)
def test_install_envoy_gateway_stops_without_manifest(
    _mock_manifest, mock_rprint, mock_run
):
    """No manifest (offline on a first start) -> an error, and nothing is applied."""
    assert https.install_envoy_gateway(False, None, None) is False

    mock_run.assert_not_called()
    assert any("[red]" in str(call.args[0]) for call in mock_rprint.call_args_list)


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


def test_gateway_manifest_https_listener_serves_local_tls():
    """With HTTPS on, one hostname-less 443 listener serves local-tls for every host.

    That listener is what replaces nginx's --default-ssl-certificate: pods never
    declare a `tls:` block, so it must not be tied to any one hostname.
    """
    listeners = {
        lsn["name"]: lsn for lsn in _docs(True)["Gateway"]["spec"]["listeners"]
    }

    assert set(listeners) == {"http", "https"}
    https_listener = listeners["https"]
    assert https_listener["port"] == 443
    assert "hostname" not in https_listener
    assert https_listener["tls"]["certificateRefs"] == [
        {"kind": "Secret", "name": "local-tls"}
    ]
    # Pods live in their own namespaces, so routes from anywhere may attach
    assert all(
        lsn["allowedRoutes"]["namespaces"]["from"] == "All"
        for lsn in listeners.values()
    )


def test_gateway_manifest_plain_http_has_no_tls_listener():
    """With HTTPS off there is no secret to reference, so no 443 listener either."""
    listeners = _docs(False)["Gateway"]["spec"]["listeners"]
    assert [lsn["name"] for lsn in listeners] == ["http"]


def test_gateway_manifest_lifts_envoy_production_defaults():
    """The settings that keep pods working the way they did behind nginx.

    Envoy cuts requests at 15s and silent streams at 5 minutes, and Envoy
    Gateway's Local traffic policy drops connections k3d's load balancer sends
    to the node without the proxy pod.
    """
    docs = _docs(True)

    assert (
        docs["BackendTrafficPolicy"]["spec"]["timeout"]["http"]["requestTimeout"]
        == "0s"
    )
    assert (
        docs["ClientTrafficPolicy"]["spec"]["timeout"]["http"]["streamIdleTimeout"]
        == "3600s"
    )
    for kind in ("BackendTrafficPolicy", "ClientTrafficPolicy"):
        assert docs[kind]["spec"]["targetRefs"][0]["name"] == https.GATEWAY_NAME
        assert docs[kind]["metadata"]["namespace"] == https.GATEWAY_NAMESPACE

    kube = docs["EnvoyProxy"]["spec"]["provider"]["kubernetes"]
    assert kube["envoyService"]["externalTrafficPolicy"] == "Cluster"
    assert kube["envoyDeployment"]["replicas"] == 1
    # The proxy settings hang off the class, so every Gateway of it gets them
    params = docs["GatewayClass"]["spec"]["parametersRef"]
    assert params["name"] == docs["EnvoyProxy"]["metadata"]["name"]
    assert params["namespace"] == docs["EnvoyProxy"]["metadata"]["namespace"]
    assert (
        docs["Gateway"]["spec"]["gatewayClassName"]
        == docs["GatewayClass"]["metadata"]["name"]
    )
    assert docs["Gateway"]["metadata"]["name"] == https.GATEWAY_NAME


def test_gateway_manifest_logs_json_and_exposes_metrics():
    """JSON access logs to stdout and Prometheus metrics, the same locally as in production."""
    telemetry = _docs(True)["EnvoyProxy"]["spec"]["telemetry"]
    log = telemetry["accessLog"]["settings"][0]
    assert log["format"]["type"] == "JSON"
    assert {"method", "authority", "response_code", "duration_ms"} <= set(
        log["format"]["json"]
    )
    assert log["sinks"] == [{"type": "File", "file": {"path": "/dev/stdout"}}]
    assert telemetry["metrics"]["prometheus"]["disable"] is False


def test_gateway_manifests_paths_follow_https_setting():
    """The base always, then the Gateway with or without its HTTPS listener."""
    assert https.gateway_manifests(True)[0].endswith("/envoy-gateway/base.yaml")
    assert https.gateway_manifests(True)[1].endswith(
        "/envoy-gateway/gateway-https.yaml"
    )
    assert https.gateway_manifests(False)[1].endswith(
        "/envoy-gateway/gateway-http.yaml"
    )


@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.https._envoy_gateway_manifest", return_value="/cache/eg.yaml")
def test_install_envoy_gateway_applies_in_order_without_waiting(
    _mock_manifest, mock_run
):
    """Manifest, then its CRDs served, then the secret, then the Gateway -- and no wait.

    Waiting for Envoy to serve here is what made `auto start` slow; that wait
    belongs to finish_ingress, after the pods have loaded.
    """
    assert https.install_envoy_gateway(True, "key.pem", "cert.pem") is True

    cmds = [c.args[0] for c in mock_run.call_args_list]
    apply = cmds.index(
        "kubectl apply --server-side --force-conflicts -f /cache/eg.yaml"
    )
    crds = next(i for i, c in enumerate(cmds) if "condition=Established" in c)
    base = next(i for i, c in enumerate(cmds) if c.endswith("/envoy-gateway/base.yaml"))
    secret = next(
        i for i, c in enumerate(cmds) if f"-n {https.GATEWAY_NAMESPACE} --dry-run" in c
    )
    gateway = next(
        i for i, c in enumerate(cmds) if c.endswith("/envoy-gateway/gateway-https.yaml")
    )

    # The base creates the namespace the secret goes in; the Gateway uses the secret
    assert apply < crds < base < secret < gateway
    assert "crd/httproutes.gateway.networking.k8s.io" in cmds[crds]
    assert not any("Programmed" in c or c.startswith("helm") for c in cmds)


@patch("autocli.utils.run_and_wait", return_value=0)
@patch("autocli.https._envoy_gateway_manifest", return_value="/cache/eg.yaml")
def test_install_envoy_gateway_stops_when_apply_fails(_mock_manifest, mock_run):
    """A failed install must not go on to apply a Gateway nothing accepts."""
    assert https.install_envoy_gateway(True, "key.pem", "cert.pem") is False
    assert mock_run.call_count == 1


def test_envoy_gateway_manifest_downloads_once_per_version(tmp_path):
    """The release manifest is fetched on first use, then served from the cache."""

    def fake_download(cmd, **_kwargs):
        partial = cmd.split(" -o ")[1].split(" ")[0]
        with open(partial, "w", encoding="utf-8") as out:
            out.write("kind: CustomResourceDefinition\n")
        return 1

    with patch("autocli.https.os.path.expanduser", return_value=str(tmp_path)), patch(
        "autocli.utils.run_and_wait", side_effect=fake_download
    ) as mock_run:
        first = https._envoy_gateway_manifest()
        second = https._envoy_gateway_manifest()

    assert first == second
    assert first.endswith(f"envoy-gateway-{https.ENVOY_GATEWAY_VERSION}-auto.yaml")
    assert mock_run.call_count == 1
    assert https.ENVOY_GATEWAY_MANIFEST_URL in mock_run.call_args.args[0]
    assert (
        f"/{https.ENVOY_GATEWAY_VERSION}/install.yaml"
        in https.ENVOY_GATEWAY_MANIFEST_URL
    )


@patch("autocli.utils.run_and_wait", return_value=0)
def test_envoy_gateway_manifest_failed_download_is_not_cached(_mock_run, tmp_path):
    """A failed download returns None and leaves nothing to be mistaken for a cache."""
    with patch("autocli.https.os.path.expanduser", return_value=str(tmp_path)):
        assert https._envoy_gateway_manifest() is None
    assert not list((tmp_path / ".auto" / "cache").glob("envoy-gateway-*.yaml"))


@patch("autocli.https.refresh_https_for_ingresses")
@patch("autocli.https.wait_for_gateway")
def test_finish_ingress_waits_for_envoy_then_refreshes_cert(mock_wait, mock_refresh):
    """The URLs printed after this answer, and the cert covers every route host."""
    manager = MagicMock()
    manager.attach_mock(mock_wait, "wait")
    manager.attach_mock(mock_refresh, "refresh")

    https.finish_ingress(True)

    assert [c[0] for c in manager.mock_calls] == ["wait", "refresh"]


@patch("autocli.https.refresh_https_for_ingresses")
@patch("autocli.https.wait_for_gateway")
def test_finish_ingress_plain_http_still_waits(mock_wait, mock_refresh):
    """Without HTTPS there is no cert to refresh, but Envoy still has to be serving."""
    https.finish_ingress(False)
    mock_wait.assert_called_once()
    mock_refresh.assert_not_called()


@patch("autocli.https.rprint")
@patch("autocli.utils.run_and_wait", side_effect=[0, 1])
def test_wait_for_gateway_warns_when_not_serving(mock_run, mock_rprint):
    """A Gateway that never comes up is a yellow warning, not a failed start."""
    assert not https.wait_for_gateway(timeout="1s")
    assert any("[yellow]" in str(c.args[0]) for c in mock_rprint.call_args_list)
    assert "--timeout=1s" in mock_run.call_args_list[0].args[0]


@patch("autocli.utils.run_and_wait")
def test_update_tls_secrets_needs_no_restart(mock_run):
    """Envoy Gateway reloads the certificate live; nothing is restarted."""
    https._update_tls_secrets("key.pem", "cert.pem")

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert cmds and all("create secret tls local-tls" in c for c in cmds)
    assert any(f"-n {https.GATEWAY_NAMESPACE} " in c for c in cmds)
    assert not any("ingress-nginx" in c or "rollout restart" in c for c in cmds)


@patch("autocli.utils.run_and_return", return_value="default/legacy\nshop/web\n")
def test_warn_unrouted_ingresses_names_each_one(_mock_return):
    """Leftover Ingress objects are named so the user knows what to convert."""
    with patch("autocli.https.rprint") as mock_print:
        https.warn_unrouted_ingresses()

    printed = [str(call.args[0]) for call in mock_print.call_args_list]
    assert "HTTPRoutes" in printed[0]
    assert printed[1:] == ["       default/legacy", "       shop/web"]


@patch("autocli.utils.run_and_return", return_value="")
def test_warn_unrouted_ingresses_silent_without_ingresses(_mock_return):
    """A fully converted cluster gets no warning at all."""
    with patch("autocli.https.rprint") as mock_print:
        https.warn_unrouted_ingresses()
    mock_print.assert_not_called()


@patch("autocli.utils.declare_error")
@patch("autocli.utils.run_and_wait", return_value=1)
def test_require_envoy_gateway_passes_on_new_cluster(_mock_run, mock_error):
    """A cluster with the Gateway carries on untouched."""
    https.require_envoy_gateway()
    mock_error.assert_not_called()


@patch("autocli.utils.declare_error", side_effect=SystemExit(1))
@patch("autocli.utils.run_and_wait", return_value=0)
def test_require_envoy_gateway_stops_old_cluster_with_the_way_out(
    _mock_run, mock_error
):
    """An ingress-nginx cluster stops with how to recreate it -- and how to wait."""
    with pytest.raises(SystemExit):
        https.require_envoy_gateway()

    message = mock_error.call_args.args[0]
    assert "auto stop --delete-cluster" in message
    assert "auto update 0.8.3" in message


INSTALL_YAML = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: httproutes.gateway.networking.k8s.io
spec:
  description: "kind: Deployment, name: envoy-gateway\\n inside a CRD stays untouched"
---
# Source: gateway-helm/templates/envoy-gateway-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: envoy-gateway
  namespace: envoy-gateway-system
spec:
  template:
    spec:
      containers:
      - name: envoy-gateway
        resources:
          limits:
            memory: 1024Mi
          requests:
            cpu: 100m
            memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: envoy-gateway
"""


def test_lower_controller_request_rewrites_only_the_controller(tmp_path):
    """The controller asks 128Mi instead of 256Mi; every other document is untouched."""
    path = tmp_path / "install.yaml"
    path.write_text(INSTALL_YAML, encoding="utf-8")

    https._lower_controller_request(str(path))

    out = path.read_text(encoding="utf-8").split("\n---\n")
    before = INSTALL_YAML.split("\n---\n")
    assert out[0] == before[0] and out[2] == before[2]
    deployment = yaml.safe_load(out[1])
    resources = deployment["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources["requests"]["memory"] == https.CONTROLLER_MEMORY_REQUEST
    assert resources["limits"]["memory"] == "1024Mi"

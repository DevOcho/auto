"""Local HTTPS and ingress: mkcert certificates and the Envoy Gateway that serves pods.

Split out of core.py so the bootstrap orchestrator stays focused. The public
entry points (setup_https_certificates, refresh_https_for_ingresses,
discover_ingress_hosts, warn_missing_host_entries, warn_unrouted_ingresses,
install_envoy_gateway, finish_ingress, require_envoy_gateway, create_local_certs)
are what core.py calls; the rest is internal to this module.

Pods are exposed with Gateway API HTTPRoutes attached to one shared Gateway
(GATEWAY_NAME in GATEWAY_NAMESPACE). Envoy Gateway replaced ingress-nginx,
which was retired in March 2026, and it does not read networking.k8s.io
Ingress objects at all -- hence warn_unrouted_ingresses.
"""

import os
import subprocess
from subprocess import CalledProcessError

import yaml
from autocli import checks, utils
from autocli.utils import run_silent
from rich import print as rprint

# Pinned so every developer's cluster runs the same controller. Bump on purpose,
# after trying the new release against a real `auto start`.
ENVOY_GATEWAY_VERSION = "v1.9.3"

# The k3s image core.start_cluster creates clusters with. It lives here because
# it is coupled to ENVOY_GATEWAY_VERSION: Envoy Gateway v1.9 runs on Kubernetes
# 1.33-1.36 and its CRDs fail to install on anything older -- including k3d's
# default 1.31, which is already out of support. Check the compatibility
# matrix (gateway.envoyproxy.io/news/releases/matrix) when bumping either one.
K3S_IMAGE = "rancher/k3s:v1.35.9-k3s1"

# The one Gateway every pod's HTTPRoute attaches to, in a namespace of its own.
# The controller and the Envoy proxy pods run in CONTROLLER_NAMESPACE, which the
# release's install.yaml creates.
GATEWAY_NAME = "auto"
GATEWAY_NAMESPACE = "auto-gateway"
CONTROLLER_NAMESPACE = "envoy-gateway-system"

# The GatewayClass, Gateway, proxy settings and timeout policies are plain
# manifests (auto/k3s/envoy-gateway/, installed to ~/.auto/k3s) so the production
# ingress project can reuse them; why each setting is what it is lives in the
# comments of base.yaml.
GATEWAY_MANIFEST_DIR = os.path.expanduser("~") + "/.auto/k3s/envoy-gateway"

# The release manifest asks 256Mi for the controller, which uses about 40Mi; the
# cached copy asks for this instead.
CONTROLLER_MEMORY_REQUEST = "128Mi"


def gateway_manifests(use_https):
    """The manifests applied after install.yaml: the shared base, then the Gateway"""
    gateway = "gateway-https.yaml" if use_https else "gateway-http.yaml"
    return f"{GATEWAY_MANIFEST_DIR}/base.yaml", f"{GATEWAY_MANIFEST_DIR}/{gateway}"


def create_local_certs(cert_path, additional_domains=None):
    """Create local certificates using mkcert"""

    if additional_domains is None:
        additional_domains = []

    # Create the directory if it doesn't exist
    if not os.path.isdir(cert_path):
        os.makedirs(cert_path)

    key_file = os.path.join(cert_path, "key.pem")
    cert_file = os.path.join(cert_path, "cert.pem")

    # Install the local CA
    # Try silently first (success if already installed or no sudo needed)
    try:
        run_silent("mkcert -install")
    except CalledProcessError:
        # If silent fail, run interactively (likely needs sudo password)
        rprint("  -- Installing local CA (may prompt for password)")
        os.system("mkcert -install")

    # Generate the certs
    # We suppress output here unless it fails
    domain_args = " ".join(additional_domains)
    cmd = (
        f"mkcert -key-file {key_file} -cert-file {cert_file} "
        f"'*.local' localhost 127.0.0.1 ::1 {domain_args}"
    )

    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
    except CalledProcessError as e:
        rprint("[red]Error generating certificates:[/red]")
        print(e.stderr.decode())

    return key_file, cert_file


def setup_https_certificates(pods):
    """Helper to setup HTTPS certificates interactively"""
    rprint("[deep_sky_blue1]Setting up HTTPS certificates...[/]")
    checks.check_mkcert()

    pod_domains = []
    for repo in pods:
        p_name = repo["repo"].split("/")[-1:][0].replace(".git", "")
        pod_domains.append(f"{p_name}.local")

    cert_path = os.path.expanduser("~") + "/.auto/certs"
    key_file, cert_file = create_local_certs(cert_path, additional_domains=pod_domains)
    rprint(" :white_heavy_check_mark:[green] Certificates Ready")
    return key_file, cert_file


def _apply_tls_secrets(key_file, cert_file):
    """Create or replace the local-tls secret the Gateway's HTTPS listener serves.

    The Gateway can only reference a secret in its own namespace (without a
    ReferenceGrant). The copy in `default` is kept for pods that mount it.
    """
    for ns in ["default", GATEWAY_NAMESPACE]:
        cmd = (
            f"kubectl create secret tls local-tls --key {key_file} --cert {cert_file} "
            f"-n {ns} --dry-run=client -o yaml | kubectl apply -f -"
        )
        utils.run_and_wait(cmd, capture_output=True)


def _update_tls_secrets(key_file, cert_file):
    """Update TLS secrets in the running cluster.

    No restart needed: Envoy Gateway watches the secret and pushes the new
    certificate to Envoy live, where ingress-nginx needed a rollout restart.
    """
    rprint("[deep_sky_blue1]Updating TLS secrets in cluster...[/]")
    _apply_tls_secrets(key_file, cert_file)
    rprint(" :white_heavy_check_mark:[green] TLS secrets updated")


def discover_ingress_hosts():
    """Read the hostnames every deployed HTTPRoute actually declares.

    The cluster is the source of truth for SSL coverage: pods define their own
    hostnames in their manifests (a single pod can expose several, e.g. both
    'app.local' and a customer 'portal.app.local'), which auto cannot guess from
    the repo name alone. We collect spec.hostnames[] across all namespaces and
    return the de-duplicated, sorted list. Leftover Ingress objects are left out
    on purpose: nothing serves them, so listing their hosts would only point the
    user at URLs that fail (warn_unrouted_ingresses names them instead).
    """
    cmd = (
        "kubectl get httproutes.gateway.networking.k8s.io --all-namespaces "
        "-o jsonpath="
        "'{range .items[*]}{range .spec.hostnames[*]}{@}{\"\\n\"}{end}{end}'"
    )
    output = utils.run_and_return(cmd)
    hosts = {line.strip() for line in output.split() if line.strip()}
    return sorted(hosts)


def warn_unrouted_ingresses():
    """Name any Ingress objects still deployed, since nothing serves them now.

    Pod repos written for ingress-nginx ship `kind: Ingress`; Envoy Gateway
    ignores those, so the pod starts fine and its URL simply never answers.
    Saying which objects need converting beats leaving the user to work out why.
    """
    cmd = (
        "kubectl get ingress --all-namespaces -o jsonpath="
        "'{range .items[*]}{.metadata.namespace}/{.metadata.name}{\"\\n\"}{end}'"
    )
    output = utils.run_and_return(cmd)
    names = sorted({line.strip() for line in output.split() if line.strip()})
    if not names:
        return
    rprint(
        "  -- [yellow]These Ingress objects are not served. auto now routes through "
        "Envoy Gateway, so convert them to HTTPRoutes (see the README)[/]:"
    )
    for name in names:
        rprint(f"       {name}")


def warn_missing_host_entries(hosts):
    """Warn about ingress hosts that won't resolve until added to /etc/hosts.

    Each ingress host must point at 127.0.0.1 locally; auto does not edit
    /etc/hosts for the user, so surface exactly which lines are missing rather
    than letting the browser fail with an opaque resolution error.

    Public because core calls it for the plain-HTTP path too -- system pods
    like mailpit and minio serve on a hostname whether or not HTTPS is on.
    """
    current = utils.run_and_return("cat /etc/hosts")
    missing = [host for host in hosts if host not in current]
    if not missing:
        return
    rprint("  -- [yellow]These hosts are not in /etc/hosts yet[/]:")
    for host in missing:
        rprint(f"       127.0.0.1      {host}")


def refresh_https_for_ingresses():
    """Re-issue the local cert to cover every deployed ingress hostname.

    Runs after application pods are installed -- that's when their ingresses
    exist. The initial cert is generated from a guessed '<podname>.local', which
    misses extra hosts a pod's chart may expose, and the '*.local' wildcard does
    not match multi-label subdomains like 'portal.app.local'. So we read the real
    hosts from the cluster and put them in the cert's SAN list explicitly, then
    refresh the in-cluster TLS secret.
    """
    hosts = discover_ingress_hosts()
    if not hosts:
        return

    if len(hosts) > 1:
        rprint(f"  -- Detected {len(hosts)} ingress hosts; issuing one cert for all")

    cert_path = os.path.expanduser("~") + "/.auto/certs"
    key_file, cert_file = create_local_certs(cert_path, additional_domains=hosts)
    _update_tls_secrets(key_file, cert_file)
    warn_missing_host_entries(hosts)


# The release publishes the whole install -- Gateway API and Envoy Gateway CRDs,
# the controller and its certgen job -- as one manifest: the same objects the
# Helm chart renders, so helm is no longer needed for ingress.
ENVOY_GATEWAY_MANIFEST_URL = (
    "https://github.com/envoyproxy/gateway/releases/download/"
    f"{ENVOY_GATEWAY_VERSION}/install.yaml"
)

# Custom resources auto creates right after the install. Their CRDs have to be
# served before the Gateway, its policies or any pod's HTTPRoute can be applied.
_GATEWAY_CRDS = [
    "gatewayclasses.gateway.networking.k8s.io",
    "gateways.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
    "envoyproxies.gateway.envoyproxy.io",
    "clienttrafficpolicies.gateway.envoyproxy.io",
    "backendtrafficpolicies.gateway.envoyproxy.io",
]


def _lower_controller_request(path):
    """Rewrite the controller Deployment's memory request in a downloaded install.yaml.

    Only that one document is parsed and rewritten; the 4 MB of CRDs around it
    stay byte for byte. A release that changes shape is left as it is.
    """
    with open(path, encoding="utf-8") as manifest:
        docs = manifest.read().split("\n---\n")
    for index, text in enumerate(docs):
        if "kind: Deployment" not in text or "name: envoy-gateway\n" not in text:
            continue
        doc = yaml.safe_load(text)
        if not isinstance(doc, dict) or doc.get("kind") != "Deployment":
            continue
        if doc.get("metadata", {}).get("name") != "envoy-gateway":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            requests = container.get("resources", {}).get("requests", {})
            if "memory" in requests:
                requests["memory"] = CONTROLLER_MEMORY_REQUEST
        docs[index] = yaml.safe_dump(doc, sort_keys=False).rstrip("\n")
    with open(path, "w", encoding="utf-8") as manifest:
        manifest.write("\n---\n".join(docs))


def _envoy_gateway_manifest():
    """Path to the release's install.yaml, downloaded once per version and cached.

    The cached copy carries auto's one change to it, the controller's memory
    request; the "-auto" in its name keeps it apart from an untouched download.
    """
    cache_dir = os.path.expanduser("~") + "/.auto/cache"
    path = f"{cache_dir}/envoy-gateway-{ENVOY_GATEWAY_VERSION}-auto.yaml"
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(cache_dir, exist_ok=True)
    # Download beside it and rename, so a cut-off download is never mistaken
    # for the cached manifest next time
    partial = path + ".part"
    if not utils.run_and_wait(
        f"curl -fsSL -o {partial} {ENVOY_GATEWAY_MANIFEST_URL}", capture_output=True
    ) or not os.path.isfile(partial):
        return None
    _lower_controller_request(partial)
    os.replace(partial, path)
    return path


def install_envoy_gateway(use_https, key_file, cert_file):
    """Install Envoy Gateway and the shared Gateway, without waiting for it to serve.

    The controller takes about a minute to start however it is installed
    (measured on a fresh cluster: helm 86 s, kubectl 78 s to a serving
    Gateway). Nothing auto does next needs it running -- system pods and app
    pods only need the CRDs to apply their HTTPRoutes -- so this returns once
    the objects are in, and finish_ingress() confirms it is serving after the
    pods have loaded. That overlap is what takes the minute off `auto start`.
    """
    manifest = _envoy_gateway_manifest()
    if not manifest:
        rprint("     [red]Could not download the Envoy Gateway manifest[/red]")
        return False
    rprint("     = Installing Envoy Gateway...")

    # Server-side apply: the CRDs are too large for client-side apply, whose
    # last-applied annotation is capped at 256 KiB
    if not utils.run_and_wait(
        f"kubectl apply --server-side --force-conflicts -f {manifest}",
        capture_output=True,
    ):
        rprint("     [red]Error installing Envoy Gateway[/red]")
        return False
    crds = " ".join(f"crd/{crd}" for crd in _GATEWAY_CRDS)
    if not utils.run_and_wait(
        f"kubectl wait --for=condition=Established --timeout=60s {crds}",
        capture_output=True,
    ):
        rprint("     [red]Envoy Gateway's CRDs did not become available[/red]")
        return False

    # The shared base first: it creates the Gateway's namespace the secret goes in
    base, gateway = gateway_manifests(use_https)
    if not utils.run_and_wait(f"kubectl apply -f {base}", capture_output=True):
        rprint("     [red]Error applying the Envoy Gateway base manifest[/red]")
        return False

    # New cluster created, if HTTPS, inject the secret the HTTPS listener serves
    if use_https and key_file and cert_file:
        rprint("     = Configuring Cluster HTTPS (Envoy Gateway)")
        _apply_tls_secrets(key_file, cert_file)

    if not utils.run_and_wait(f"kubectl apply -f {gateway}", capture_output=True):
        rprint("     [red]Error creating the Envoy Gateway listeners[/red]")
        return False
    return True


def wait_for_gateway(timeout="300s"):
    """Wait until the Gateway is serving, then clear the finished certgen pod"""
    # Programmed means the Envoy proxy is running and the load balancer has
    # given it an address -- i.e. pods' hostnames will actually answer
    ready = utils.run_and_wait(
        f"kubectl wait --for=condition=Programmed gateway/{GATEWAY_NAME} "
        f"-n {GATEWAY_NAMESPACE} --timeout={timeout}",
        capture_output=True,
    )
    if not ready:
        rprint(
            "  -- [yellow]Envoy Gateway is not serving yet; pod URLs may not answer "
            f"until it is (kubectl get pods -n {CONTROLLER_NAMESPACE})[/]"
        )
    # Let's remove the completed setup job containers (the certgen job)
    utils.run_and_wait(
        f"kubectl delete pod -n {CONTROLLER_NAMESPACE} "
        "--field-selector=status.phase==Succeeded",
        capture_output=True,
    )
    return ready


def finish_ingress(use_https):
    """Last ingress step of a full start, once the pods have loaded.

    install_envoy_gateway() returned without waiting for Envoy, and loading the
    pods covered most of its start-up. Confirm it is serving now, so the URLs
    printed next answer, then cover every route's hostname with the cert.
    """
    wait_for_gateway()
    if use_https:
        refresh_https_for_ingresses()


def require_envoy_gateway():
    """Stop on a cluster created before auto moved to Envoy Gateway.

    Those clusters still run ingress-nginx on Kubernetes 1.31. No supported
    Envoy Gateway release runs on 1.31, and k3d cannot upgrade a cluster in
    place, so a new cluster is the only way forward. Say that plainly instead of
    carrying on with a cluster where no converted pod's hostname will answer.
    """
    gateway_check = f"kubectl get gateway {GATEWAY_NAME} -n {GATEWAY_NAMESPACE}"
    if utils.run_and_wait(gateway_check, capture_output=True, suppress_error=True):
        return

    utils.declare_error(
        "This cluster predates Envoy Gateway and has to be recreated:\n"
        "     auto stop --delete-cluster\n"
        "     auto start\n"
        "   That resets the local databases; re-seed with `auto seed <pod>`.\n"
        "   Not ready yet? `auto update 0.8.3` puts the previous auto back."
    )

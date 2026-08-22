"""Local HTTPS: mkcert certificate generation and nginx-ingress TLS wiring.

Split out of core.py so the bootstrap orchestrator stays focused. The public
entry points (setup_https_certificates, refresh_https_for_ingresses,
discover_ingress_hosts, install_nginx_ingress, create_local_certs) are what
core.py calls; the rest is internal to this module.
"""

import os
import shutil
import subprocess
from subprocess import CalledProcessError

from autocli import checks, utils
from autocli.utils import run_silent
from rich import print as rprint


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


def _update_tls_secrets(key_file, cert_file):
    """Update TLS secrets in the running cluster and restart ingress to pick them up"""
    rprint("[deep_sky_blue1]Updating TLS secrets in cluster...[/]")
    for ns in ["default", "ingress-nginx"]:
        cmd = (
            f"kubectl create secret tls local-tls --key {key_file} --cert {cert_file} "
            f"-n {ns} --dry-run=client -o yaml | kubectl apply -f -"
        )
        utils.run_and_wait(cmd, capture_output=True)
    utils.run_and_wait(
        "kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx",
        capture_output=True,
    )
    rprint(" :white_heavy_check_mark:[green] TLS secrets updated")


def discover_ingress_hosts():
    """Read the hostnames every deployed Ingress actually declares.

    The cluster is the source of truth for SSL coverage: pods define their own
    hostnames in their Helm charts (a single pod can expose several, e.g. both
    'app.local' and a customer 'portal.app.local'), which auto cannot guess from
    the repo name alone. We collect spec.rules[].host and spec.tls[].hosts[]
    across all namespaces and return the de-duplicated, sorted list.
    """
    cmd = (
        "kubectl get ingress --all-namespaces -o jsonpath="
        '\'{range .items[*]}{range .spec.rules[*]}{.host}{"\\n"}{end}'
        '{range .spec.tls[*].hosts[*]}{@}{"\\n"}{end}{end}\''
    )
    output = utils.run_and_return(cmd)
    hosts = {line.strip() for line in output.split() if line.strip()}
    return sorted(hosts)


def _warn_missing_host_entries(hosts):
    """Warn about ingress hosts that won't resolve until added to /etc/hosts.

    Each ingress host must point at 127.0.0.1 locally; auto does not edit
    /etc/hosts for the user, so surface exactly which lines are missing rather
    than letting the browser fail with an opaque resolution error.
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
    _warn_missing_host_entries(hosts)


def install_nginx_ingress(use_https, key_file, cert_file):
    """Install and configure Nginx Ingress Controller"""
    if shutil.which("helm") is None:
        rprint(
            "  -- [yellow]helm not found; skipping Nginx Ingress install. "
            "Ingress will not be available until helm is installed.[/]"
        )
        return
    rprint("     = Installing Nginx Ingress Controller...")
    utils.run_and_wait(
        "helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx",
        capture_output=True,
    )
    utils.run_and_wait("helm repo update", capture_output=True)

    # Build Helm command
    helm_cmd = (
        "helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx "
        "--namespace ingress-nginx --create-namespace "
        "--set controller.service.type=LoadBalancer "
        "--set controller.watchIngressWithoutClass=true "
        "--set controller.ingressClassResource.default=true "
        "--set controller.admissionWebhooks.enabled=false "
    )

    # New cluster created, if HTTPS, inject secrets and config Nginx
    if use_https and key_file and cert_file:
        rprint("     = Configuring Cluster HTTPS (Nginx)")

        # Create the namespace first (needed for secrets)
        utils.run_and_wait(
            "kubectl create namespace ingress-nginx", capture_output=True
        )

        # Create secrets in default and ingress-nginx namespaces
        for ns in ["default", "ingress-nginx"]:
            cmd = (
                f"kubectl create secret tls local-tls --key {key_file} --cert {cert_file} "
                f"-n {ns} --dry-run=client -o yaml | kubectl apply -f -"
            )
            utils.run_and_wait(cmd, capture_output=True)

        # Add default cert arg
        extra_args = "controller.extraArgs.default-ssl-certificate"
        helm_cmd += f" --set {extra_args}=ingress-nginx/local-tls"

    # Run the Helm install silently
    if not utils.run_and_wait(helm_cmd, capture_output=True):
        rprint("     [red]Error installing Nginx Ingress Controller[/red]")
    else:
        # Explicitly Patch the Deployment to FORCE the argument if Helm missed it
        if use_https:
            patch_cmd = (
                "kubectl patch deployment ingress-nginx-controller -n ingress-nginx "
                '--type=json -p=\'[{"op": "add", "path": '
                '"/spec/template/spec/containers/0/args/-", '
                '"value": "--default-ssl-certificate=ingress-nginx/local-tls"}]\''
            )
            utils.run_and_wait(patch_cmd, capture_output=True, suppress_error=True)

        # Force restart Nginx pods to ensure they pick up the new certificate
        utils.run_and_wait(
            "kubectl rollout restart deployment ingress-nginx-controller -n ingress-nginx",
            capture_output=True,
        )

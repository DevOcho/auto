"""Ephemeral one-shot pod runner.

Spawns a short-lived Pod that mirrors an application Deployment but overrides
the command — used by `auto migrate`, `auto init`, and `auto seed` so those
scripts run even when the app pod itself is CrashLooping. Split out of utils.py
to keep that module focused on low-level primitives.
"""

import json
import os
import shlex
import signal
import subprocess
import uuid
from subprocess import CalledProcessError
from time import sleep

import yaml
from autocli.utils import declare_error, run_and_return, run_and_wait
from rich import print as rprint


def get_deployment_spec(name, namespace="default"):
    """Fetch a deployment's spec as a dict, or None if it doesn't exist"""
    cmd = (
        f"kubectl get deployment {shlex.quote(name)} "
        f"-n {shlex.quote(namespace)} -o json"
    )
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, check=True, text=True
        )
        return json.loads(result.stdout)
    except (CalledProcessError, json.JSONDecodeError):
        return None


def _build_runner_pod_manifest(
    pod_name,
    runner_name,
    action_label,
    command_args,
    deployment,
    extra_env,
    namespace,
):  # pylint: disable=too-many-arguments
    """Build a Pod manifest mirroring deployment's first container, with overrides"""
    spec_template = deployment["spec"]["template"]["spec"]
    container = spec_template["containers"][0]

    env_list = list(container.get("env", []))
    if extra_env:
        env_list.extend(extra_env)

    pod_spec = {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": action_label,
                "image": container["image"],
                "command": list(command_args),
                "workingDir": container.get("workingDir", f"/mnt/code/{pod_name}"),
                "env": env_list,
                "envFrom": container.get("envFrom", []),
                "volumeMounts": container.get("volumeMounts", []),
            }
        ],
        "volumes": spec_template.get("volumes", []),
    }
    if "serviceAccountName" in spec_template:
        pod_spec["serviceAccountName"] = spec_template["serviceAccountName"]
    if "imagePullSecrets" in spec_template:
        pod_spec["imagePullSecrets"] = spec_template["imagePullSecrets"]

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": runner_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "auto",
                "auto.devocho/role": action_label,
                "auto.devocho/target": pod_name,
            },
        },
        "spec": pod_spec,
    }


def _runner_phase(phase_cmd):
    """Read a runner pod's phase, normalized (jsonpath wraps it in quotes)."""
    return run_and_return(phase_cmd).strip().strip("'")


def _create_runner_pod(manifest_yaml, runner_name, action_label, namespace):
    """Apply the runner manifest and wait for it to schedule.

    Returns True once the pod is created and scheduled. On failure it prints
    diagnostics (the apply error, or a describe dump) and returns False — a
    short schedule timeout, because if the pod can't even schedule something
    structural is wrong (missing image, bad envFrom secret, etc.).
    """
    result = subprocess.run(
        "kubectl apply -f -",
        shell=True,
        input=manifest_yaml,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        rprint(f"[red]Failed to create {action_label} pod[/red]")
        print(result.stderr)
        return False

    sched_cmd = (
        f"kubectl wait --for=condition=PodScheduled "
        f"pod/{runner_name} -n {namespace} --timeout=60s"
    )
    if not run_and_wait(sched_cmd, capture_output=True, suppress_error=True):
        rprint(f"[red]{action_label} pod failed to schedule — describe output:[/red]")
        run_and_wait(
            f"kubectl describe pod/{runner_name} -n {namespace}",
            capture_output=False,
        )
        return False

    return True


def _wait_for_runner_start(phase_cmd, action_label, pod_name):
    """Block until the runner container starts (leaves Pending) or ~120s passes.

    PodScheduled fires before the image is pulled, so streaming logs too early
    races the container start and bails with "is waiting to start". We poll the
    phase and return once it leaves Pending; if it never does, we warn and let
    the caller stream anyway.
    """
    for _ in range(240):  # up to ~120s at 0.5s per cycle
        phase = _runner_phase(phase_cmd)
        if phase and phase != "Pending":
            return
        sleep(0.5)
    rprint(
        f"  -- [yellow]{action_label} pod for {pod_name} still not "
        f"running after 120s; streaming anyway[/yellow]"
    )


def _wait_for_runner_finish(phase_cmd):
    """Block until the runner pod reaches a terminal phase or vanishes (~30s).

    `kubectl logs -f` can return a beat before the phase flips off Running, so
    reading the phase once would misreport a finished run. An empty phase means
    the pod was deleted/evicted (or kubectl errored) — stop immediately rather
    than spin the full window. Returns the last phase seen.
    """
    phase = ""
    for _ in range(60):  # up to ~30s at 0.5s per cycle
        phase = _runner_phase(phase_cmd)
        if phase in ("Succeeded", "Failed") or not phase:
            return phase
        sleep(0.5)
    return phase


def _log_stream_interrupted(log_status):
    """True if the user Ctrl-C'd the `kubectl logs -f` stream.

    os.system ignores SIGINT in the parent, so the only signal is the child's
    exit status: killed by SIGINT, or exit code 130 (128+SIGINT).
    """
    if os.WIFSIGNALED(log_status):
        return os.WTERMSIG(log_status) == signal.SIGINT
    return os.WEXITSTATUS(log_status) == 130


def run_one_shot_pod_command(
    pod_name,
    command_args,
    action_label,
    extra_env=None,
    namespace="default",
):
    """Run command_args in an ephemeral pod that mirrors pod_name's deployment.

    Spawns a fresh Pod using the same image, env, envFrom, volumeMounts, and
    volumes as the application Deployment, but overrides the command. This
    avoids depending on the application container being healthy — the right
    behavior for migrations, db init, and seed scripts that should run even
    if the app pod is CrashLooping.

    Returns 0 on Pod phase Succeeded, 1 otherwise.
    """
    deployment = get_deployment_spec(pod_name, namespace)
    if not deployment:
        declare_error(
            f"Deployment '{pod_name}' not found in namespace '{namespace}'. "
            f"Run 'auto start {pod_name}' first to install it."
        )
        return 1

    # Unique pod name so concurrent runs and old failed migrators don't collide
    runner_name = f"{pod_name}-{action_label}-{uuid.uuid4().hex[:8]}"
    runner_env = [{"name": "FORCE_COLOR", "value": "1"}, *(extra_env or [])]
    pod_manifest = _build_runner_pod_manifest(
        pod_name,
        runner_name,
        action_label,
        command_args,
        deployment,
        runner_env,
        namespace,
    )
    manifest_yaml = yaml.safe_dump(pod_manifest)
    phase_cmd = (
        f"kubectl get pod/{runner_name} -n {namespace} " "-o jsonpath='{.status.phase}'"
    )

    try:
        rprint(f"  -- Spawning {action_label} pod for {pod_name}")
        if not _create_runner_pod(manifest_yaml, runner_name, action_label, namespace):
            return 1

        _wait_for_runner_start(phase_cmd, action_label, pod_name)

        # Stream logs until the container exits. os.system avoids buffering
        # so the user sees output in real time.
        rprint(f"  -- Streaming {action_label} output for {pod_name}")
        log_status = os.system(f"kubectl logs -f pod/{runner_name} -n {namespace}")

        # If the user Ctrl-C'd the stream, don't wait for a terminal phase that
        # will never come.
        if _log_stream_interrupted(log_status):
            rprint(f"  -- [yellow]{action_label} for {pod_name} interrupted[/yellow]")
            return 1

        phase = _wait_for_runner_finish(phase_cmd)
        if phase == "Succeeded":
            rprint(f"  -- [green]{action_label} for {pod_name} completed[/green]")
            return 0

        rprint(
            f"  -- [red]{action_label} for {pod_name} ended in phase "
            f"{phase or 'unknown'}[/red]"
        )
        return 1
    finally:
        # Always clean up so a leaked migrator doesn't block a retry
        run_and_wait(
            f"kubectl delete pod/{runner_name} -n {namespace} " f"--ignore-not-found",
            capture_output=True,
            suppress_error=True,
        )

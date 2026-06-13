"""Tests for auto.autocli.runner (the ephemeral one-shot pod runner)."""

import signal
from unittest.mock import MagicMock, patch

import yaml
from autocli import runner


def _fake_deployment(image="reg/api:1", env=None, volume_mounts=None, volumes=None):
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "image": image,
                            "env": env or [],
                            "volumeMounts": volume_mounts or [],
                            "workingDir": "/mnt/code/api",
                        }
                    ],
                    "volumes": volumes or [],
                }
            }
        }
    }


@patch("autocli.runner.run_and_return", return_value="Succeeded")
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.os.system", return_value=0)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_builds_manifest(
    mock_get_dep, mock_subproc, _mock_system, _mock_run_wait, _mock_run_return
):
    """The generated Pod manifest mirrors the deployment's container spec."""
    mock_get_dep.return_value = _fake_deployment(
        image="reg/api:1",
        env=[{"name": "DB_HOST", "value": "postgres"}],
        volume_mounts=[{"name": "code-pvc", "mountPath": "/mnt/code"}],
        volumes=[{"name": "code-pvc", "persistentVolumeClaim": {"claimName": "code"}}],
    )

    # apply succeeds; run_and_return reports the pod phase as Succeeded
    apply_result = MagicMock(returncode=0, stderr="")
    mock_subproc.side_effect = [apply_result]

    rc = runner.run_one_shot_pod_command(
        "api",
        command_args=["/mnt/code/api/smalls.py", "migrate"],
        action_label="migrate",
        extra_env=[{"name": "SMALLS_ENV", "value": "PROD"}],
    )

    assert rc == 0

    # First subprocess.run is `kubectl apply -f -` with the manifest on stdin
    apply_call = mock_subproc.call_args_list[0]
    assert apply_call.args[0] == "kubectl apply -f -"
    manifest_yaml = apply_call.kwargs["input"]

    manifest = yaml.safe_load(manifest_yaml)
    assert manifest["kind"] == "Pod"
    assert manifest["metadata"]["name"].startswith("api-migrate-")
    assert manifest["metadata"]["labels"]["auto.devocho/role"] == "migrate"
    spec = manifest["spec"]
    assert spec["restartPolicy"] == "Never"
    container = spec["containers"][0]
    assert container["image"] == "reg/api:1"
    assert container["command"] == ["/mnt/code/api/smalls.py", "migrate"]
    assert container["workingDir"] == "/mnt/code/api"
    # Both deployment env and extra_env are present
    env_pairs = {(e["name"], e.get("value")) for e in container["env"]}
    assert ("DB_HOST", "postgres") in env_pairs
    assert ("SMALLS_ENV", "PROD") in env_pairs
    # FORCE_COLOR is injected so colored output survives the kubectl logs stream
    assert ("FORCE_COLOR", "1") in env_pairs
    assert container["volumeMounts"][0]["mountPath"] == "/mnt/code"
    assert spec["volumes"][0]["name"] == "code-pvc"


@patch("autocli.runner.declare_error")
@patch("autocli.runner.get_deployment_spec", return_value=None)
def test_run_one_shot_pod_command_errors_when_deployment_missing(_mock_get, mock_err):
    """Missing deployment short-circuits with a clear declare_error call."""
    rc = runner.run_one_shot_pod_command(
        "api", command_args=["x"], action_label="migrate"
    )
    assert rc == 1
    mock_err.assert_called_once()
    msg = mock_err.call_args.args[0]
    assert "Deployment 'api' not found" in msg


@patch("autocli.runner.run_and_return", return_value="Failed")
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.os.system", return_value=0)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_failed_phase_returns_nonzero(
    mock_get_dep, mock_subproc, _mock_system, _mock_run_wait, _mock_run_return
):
    """A non-Succeeded terminal phase returns 1."""
    mock_get_dep.return_value = _fake_deployment()
    apply_result = MagicMock(returncode=0, stderr="")
    mock_subproc.side_effect = [apply_result]

    rc = runner.run_one_shot_pod_command(
        "api", command_args=["x"], action_label="migrate"
    )
    assert rc == 1


@patch("autocli.runner.sleep")
@patch("autocli.runner.run_and_return")
@patch("autocli.runner.os.system", return_value=0)
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_waits_for_container_start(
    mock_get_dep,
    mock_subproc,
    _mock_run_wait,
    mock_system,
    mock_run_return,
    _mock_sleep,
):
    """Logs are streamed only after the container leaves ContainerCreating.

    Regression test: PodScheduled fires while the container is still
    ContainerCreating; reading the phase or streaming logs immediately would
    misread the transient Pending as a failure. The runner must poll until the
    pod leaves Pending before streaming.
    """
    mock_get_dep.return_value = _fake_deployment()
    apply_result = MagicMock(returncode=0, stderr="")
    mock_subproc.side_effect = [apply_result]
    # Pre-stream poll: Pending (ContainerCreating) then Running -> start
    # streaming; post-stream poll: Succeeded.
    mock_run_return.side_effect = ["Pending", "Running", "Succeeded"]

    rc = runner.run_one_shot_pod_command("api", command_args=["x"], action_label="init")

    assert rc == 0
    # Two pre-stream polls (Pending -> Running) plus one post-stream poll.
    assert mock_run_return.call_count == 3
    log_cmd = mock_system.call_args[0][0]
    assert "kubectl logs -f pod/api-init-" in log_cmd


@patch("autocli.runner.sleep")
@patch("autocli.runner.run_and_return")
@patch("autocli.runner.os.system", return_value=0)
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_waits_for_terminal_phase(
    mock_get_dep,
    mock_subproc,
    _mock_run_wait,
    mock_system,
    mock_run_return,
    _mock_sleep,
):
    """A run is reported successful even if the phase lags behind the logs.

    Regression test: `kubectl logs -f` can return a beat before the pod's
    phase flips from Running to Succeeded. Reading the phase once, immediately,
    would misreport a successful run as "ended in phase Running"; the runner
    must poll until the phase reaches a terminal value.
    """
    mock_get_dep.return_value = _fake_deployment()
    apply_result = MagicMock(returncode=0, stderr="")
    mock_subproc.side_effect = [apply_result]
    # Pre-stream poll: Running (start streaming). Post-stream poll: phase still
    # Running for a moment, then settles on Succeeded.
    mock_run_return.side_effect = ["Running", "Running", "Succeeded"]

    rc = runner.run_one_shot_pod_command("api", command_args=["x"], action_label="init")

    assert rc == 0
    assert mock_system.called  # logs were streamed before the phase settled
    assert mock_run_return.call_count == 3


@patch("autocli.runner.sleep")
@patch("autocli.runner.run_and_return")
@patch("autocli.runner.os.system", return_value=0)
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_stops_when_pod_vanishes(
    mock_get_dep,
    mock_subproc,
    _mock_run_wait,
    _mock_system,
    mock_run_return,
    _mock_sleep,
):
    """A vanished pod stops the poll instead of burning the full window.

    Regression test: if the pod is deleted/evicted (or kubectl errors) after the
    log stream ends, the phase query returns "". Without an early exit the runner
    would spin the full ~30s window before reporting "ended in phase unknown".
    """
    mock_get_dep.return_value = _fake_deployment()
    mock_subproc.side_effect = [MagicMock(returncode=0, stderr="")]
    # Pre-stream poll: Running (start streaming). Post-stream poll: empty (gone).
    mock_run_return.side_effect = ["Running", ""]

    rc = runner.run_one_shot_pod_command("api", command_args=["x"], action_label="init")

    assert rc == 1
    # One pre-stream poll plus a single post-stream poll — no 60-iteration spin.
    assert mock_run_return.call_count == 2


@patch("autocli.runner.sleep")
@patch("autocli.runner.run_and_return")
@patch("autocli.runner.os.system")
@patch("autocli.runner.run_and_wait", return_value=1)
@patch("autocli.runner.subprocess.run")
@patch("autocli.runner.get_deployment_spec")
def test_run_one_shot_pod_command_stops_on_interrupt(
    mock_get_dep,
    mock_subproc,
    _mock_run_wait,
    mock_system,
    mock_run_return,
    _mock_sleep,
):
    """Ctrl-C on the log stream exits immediately without polling the phase.

    Regression test: when the user interrupts `kubectl logs -f`, os.system reports
    the child as killed by SIGINT. The runner must stop right away instead of
    waiting ~30s for a terminal phase that will never come.
    """
    mock_get_dep.return_value = _fake_deployment()
    mock_subproc.side_effect = [MagicMock(returncode=0, stderr="")]
    # os.system status 2 == killed by signal 2 (SIGINT), no core dump.
    mock_system.return_value = signal.SIGINT
    mock_run_return.side_effect = ["Running"]  # only the pre-stream poll runs

    rc = runner.run_one_shot_pod_command("api", command_args=["x"], action_label="init")

    assert rc == 1
    assert mock_run_return.call_count == 1  # no post-stream phase polling

"""Tests for auto.autocli.services orchestration"""

# pylint: disable=protected-access

import subprocess
from unittest.mock import MagicMock, patch

from autocli import services
from autocli.config import CONFIG


@patch("autocli.utils.get_full_pod_name")
@patch("subprocess.run")
def test_create_mysql_database(mock_run, mock_pod_name):
    """Test database creation with retries"""
    mock_pod_name.return_value = "mysql-pod"

    services.create_mysql_database("mydb")
    mock_run.assert_called()

    mock_run.side_effect = [subprocess.CalledProcessError(1, "cmd"), MagicMock()]
    with patch("time.sleep"):
        services.create_mysql_database("mydb", retries=0)
    assert mock_run.call_count == 3


@patch("autocli.utils.get_pod_config")
def test_create_databases_for_pod_no_system_pods(mock_get_config):
    """No system-pods declared → no readiness check, no processing."""
    mock_get_config.return_value = {"command": "helm install"}

    with patch("autocli.services._verify_required_dbs_ready") as mock_verify:
        with patch("autocli.services._process_pod_databases") as mock_process:
            services.create_databases_for_pod("api")
            mock_verify.assert_not_called()
            mock_process.assert_not_called()


@patch("autocli.utils.get_pod_config")
def test_create_databases_for_pod_creates_buckets(mock_get_config):
    """A pod that needs minio buckets triggers _process_pod_databases."""
    pod_config = {
        "system-pods": [
            {"name": "minio", "buckets": [{"name": "uploads"}, {"name": "thumbs"}]},
        ],
    }
    mock_get_config.return_value = pod_config

    with patch("autocli.services._verify_required_dbs_ready", return_value=True):
        with patch("autocli.services._process_pod_databases") as mock_process:
            services.create_databases_for_pod("api")
            mock_process.assert_called_once_with(pod_config)


@patch("autocli.utils.get_pod_config")
def test_create_databases_for_pod_skips_when_db_not_ready(mock_get_config):
    """Readiness failure short-circuits before processing."""
    mock_get_config.return_value = {
        "system-pods": [{"name": "mysql", "databases": [{"name": "app"}]}],
    }

    with patch("autocli.services._verify_required_dbs_ready", return_value=False):
        with patch("autocli.services._process_pod_databases") as mock_process:
            services.create_databases_for_pod("api")
            mock_process.assert_not_called()


@patch("autocli.utils.get_pod_config")
def test_create_databases_for_pod_passes_correct_needs(mock_get_config):
    """`needs` set passed to readiness check matches the pod's system-pods."""
    mock_get_config.return_value = {
        "system-pods": [
            {"name": "minio", "buckets": [{"name": "uploads"}]},
            {"name": "postgres", "databases": [{"name": "app"}]},
        ],
    }

    with patch(
        "autocli.services._verify_required_dbs_ready", return_value=True
    ) as mock_verify:
        with patch("autocli.services._process_pod_databases"):
            services.create_databases_for_pod("api")
            assert mock_verify.call_args[0][0] == {"minio", "postgres"}


def test_verify_required_dbs_ready_empty_needs():
    """Empty needs returns True without spinning up any check."""
    with patch("autocli.services._verify_db_system_ready") as mock_check:
        assert services._verify_required_dbs_ready(set()) is True
        mock_check.assert_not_called()


def test_verify_required_dbs_ready_minio_only():
    """A `needs` set containing only minio shouldn't trigger DB readiness checks."""
    with patch("autocli.services._verify_db_system_ready") as mock_check:
        assert services._verify_required_dbs_ready({"minio"}) is True
        mock_check.assert_not_called()


def test_verify_required_dbs_ready_only_targets_db_pods():
    """When needs has both DB and non-DB names, only DBs are checked."""
    with patch(
        "autocli.services._verify_db_system_ready", return_value=True
    ) as mock_check:
        assert services._verify_required_dbs_ready({"mysql", "minio"}) is True
        # Only mysql is a DB target
        assert mock_check.call_count == 1
        assert mock_check.call_args[0][0] == "mysql"


@patch("autocli.services.create_minio_bucket")
def test_process_pod_databases_minio_buckets(mock_create_bucket):
    """_process_pod_databases iterates buckets for minio pods."""
    pod_config = {
        "system-pods": [
            {"name": "minio", "buckets": [{"name": "uploads"}, {"name": "thumbs"}]},
        ],
    }
    with patch.dict(CONFIG, {"skipped-system-pods": []}):
        services._process_pod_databases(pod_config)

    bucket_names = [call.args[0] for call in mock_create_bucket.call_args_list]
    assert bucket_names == ["uploads", "thumbs"]


@patch("autocli.services.create_minio_bucket")
def test_process_pod_databases_skips_skipped_pods(mock_create_bucket):
    """Pods in skipped-system-pods are not processed."""
    pod_config = {
        "system-pods": [
            {"name": "minio", "buckets": [{"name": "uploads"}]},
        ],
    }
    with patch.dict(CONFIG, {"skipped-system-pods": ["minio"]}):
        services._process_pod_databases(pod_config)

    mock_create_bucket.assert_not_called()


# --- init / seed ----------------------------------------------------------


@patch("autocli.runner.run_one_shot_pod_command", return_value=0)
@patch("autocli.utils.get_pod_config")
def test_init_pod_db_uses_ephemeral_pod(mock_get_config, mock_run):
    """init runs the configured init-command in a one-shot pod."""
    mock_get_config.return_value = {"init-command": "init_db.py"}
    services.init_pod_db("api")

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    args = mock_run.call_args.args
    assert args[0] == "api"
    assert kwargs["command_args"] == ["/mnt/code/api/init_db.py"]
    assert kwargs["action_label"] == "init"


@patch("autocli.runner.run_one_shot_pod_command", return_value=0)
@patch("autocli.utils.get_pod_config")
def test_seed_pod_uses_ephemeral_pod(mock_get_config, mock_run):
    """seed runs the configured seed-command in a one-shot pod."""
    mock_get_config.return_value = {"seed-command": "seed.py"}
    services.seed_pod("api")

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    args = mock_run.call_args.args
    assert args[0] == "api"
    assert kwargs["command_args"] == ["/mnt/code/api/seed.py"]
    assert kwargs["action_label"] == "seed"


@patch("autocli.utils.get_full_pod_name")
@patch("subprocess.run")
def test_connect_to_mailpit_forwards_the_ui_port(mock_run, mock_pod_name):
    """`auto mailpit` port-forwards the web UI, not the SMTP port."""
    mock_pod_name.return_value = "mailpit-9f6997588-9lzmn\n"

    services.connect_to_mailpit()

    cmd = mock_run.call_args.args[0]
    assert "port-forward" in " ".join(cmd)
    assert "8025:8025" in " ".join(cmd)
    assert "mailpit-9f6997588-9lzmn" in " ".join(cmd)

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


@patch("autocli.services.setup_minio", new=lambda: None)
@patch("autocli.utils.wait_for_pod_status", new=lambda *_a, **_k: True)
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


@patch("autocli.utils.is_port_in_use", side_effect=lambda port: port == 6379)
@patch(
    "autocli.utils.get_required_system_pods", return_value={"mysql", "redis", "minio"}
)
def test_cluster_port_args_maps_required_free_ports(_mock_required, _mock_in_use):
    """Only ports this start needs, and only ones free on the host, are mapped.

    A port taken locally is left out so `k3d cluster create` does not fail;
    install_system_pods then skips that pod exactly as before.
    """
    assert services.cluster_port_args() == '-p "3306:30036@loadbalancer"'


@patch("autocli.services.setup_minio")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"minio"})
def test_install_system_pods_does_not_wait_for_minio(
    _mock_required, mock_cmd, mock_setup
):
    """Loading system pods no longer blocks on minio's client setup."""
    minio = {"pod": {"name": "minio", "commands": ["kubectl apply -f minio.yaml"]}}
    with patch.dict(CONFIG, {"system-pods": [minio]}):
        services.install_system_pods()
    mock_cmd.assert_called_once_with("kubectl apply -f minio.yaml")
    mock_setup.assert_not_called()


@patch("autocli.services.create_minio_bucket")
@patch("autocli.services.setup_minio")
@patch("autocli.utils.wait_for_pod_status", return_value=True)
def test_minio_alias_is_set_up_before_buckets(mock_wait, mock_setup, mock_bucket):
    """Buckets need the in-pod `myminio` alias, so it is set once minio runs."""
    manager = MagicMock()
    manager.attach_mock(mock_wait, "wait")
    manager.attach_mock(mock_setup, "setup")
    manager.attach_mock(mock_bucket, "bucket")

    services._process_minio_buckets(
        {"name": "minio", "buckets": [{"name": "a"}, {"name": "b"}]}
    )

    assert [c[0] for c in manager.mock_calls] == ["wait", "setup", "bucket", "bucket"]


@patch("autocli.services.setup_minio")
@patch("autocli.utils.wait_for_pod_status")
def test_minio_without_buckets_waits_for_nothing(mock_wait, mock_setup):
    """A pod that uses minio but asks for no buckets costs no wait at all."""
    services._process_minio_buckets({"name": "minio"})
    mock_wait.assert_not_called()
    mock_setup.assert_not_called()


@patch("time.sleep")
@patch("subprocess.run")
@patch("autocli.utils.get_full_pod_name", return_value="mysql-pod\n")
def test_mysql_readiness_is_checked_over_tcp(_mock_pod, mock_run, _mock_sleep):
    """Only the real server listens on TCP, so 127.0.0.1 answering means ready.

    The socket alone also answers the temporary first-boot server, which then
    restarts under a CREATE DATABASE.
    """
    assert services.wait_for_mysql_socket()
    assert "mysql -h127.0.0.1 " in mock_run.call_args.args[0]


@patch("autocli.services.rprint")
@patch("autocli.services.create_mysql_database", return_value=False)
def test_failed_mysql_database_is_not_reported_created(_mock_create, mock_rprint):
    """A database that could not be made is not announced as created."""
    services._process_mysql_databases(
        {"name": "mysql", "databases": [{"name": "kirkland"}]}
    )
    assert not any("Created" in str(c.args[0]) for c in mock_rprint.call_args_list)


@patch("time.sleep")
@patch(
    "autocli.utils.run_silent",
    side_effect=[subprocess.CalledProcessError(1, "mc"), None],
)
@patch("autocli.utils.get_full_pod_name", return_value="minio-pod\n")
def test_setup_minio_retries_until_the_server_answers(
    _mock_pod, mock_silent, _mock_sleep
):
    """A Running pod whose server is not answering yet is retried, not fatal."""
    assert services.setup_minio() is True
    assert mock_silent.call_count == 2
    assert " -it " not in mock_silent.call_args.args[0]


@patch("autocli.services.rprint")
@patch("time.sleep")
@patch("autocli.utils.run_silent", side_effect=subprocess.CalledProcessError(1, "mc"))
@patch("autocli.utils.get_full_pod_name", return_value="minio-pod\n")
def test_setup_minio_gives_up_with_a_warning(
    _mock_pod, _mock_silent, _mock_sleep, mock_rprint
):
    """MinIO never answering warns instead of crashing `auto start`."""
    assert services.setup_minio(retries=3) is False
    assert any("[yellow]" in str(c.args[0]) for c in mock_rprint.call_args_list)


@patch("autocli.services.rprint")
@patch("autocli.utils.wait_for_pod_status", return_value=False)
def test_db_that_never_runs_is_not_treated_as_ready(mock_wait, mock_rprint):
    """A MySQL pod that never reaches Running stops database creation clearly.

    Before, the check fell through and returned ready, and every CREATE
    DATABASE then failed with "Could not create database".
    """
    mysql = {"pod": {"name": "mysql", "commands": []}}
    socket_check = MagicMock()
    with patch.dict(CONFIG, {"system-pods": [mysql], "skipped-system-pods": []}):
        assert services._verify_db_system_ready("mysql", "MySQL", socket_check) is False
    socket_check.assert_not_called()
    assert mock_wait.call_args.kwargs["max_wait_time"] >= 360
    assert any("[red]" in str(c.args[0]) for c in mock_rprint.call_args_list)


@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"mysql"})
def test_retired_database_routes_are_skipped(_mock_required, mock_cmd):
    """An old local.yaml still lists the database routes auto no longer ships."""
    mysql = {
        "pod": {
            "name": "mysql",
            "commands": [
                "kubectl apply -f ~/.auto/k3s/mysql/deployment.yaml",
                "kubectl apply -f ~/.auto/k3s/mysql/ingress.yaml",
            ],
        }
    }
    with patch.dict(CONFIG, {"system-pods": [mysql]}), patch(
        "autocli.services._expose_system_pod_port", return_value=True
    ):
        services.install_system_pods()
    assert [c.args[0] for c in mock_cmd.call_args_list] == [
        "kubectl apply -f ~/.auto/k3s/mysql/deployment.yaml"
    ]

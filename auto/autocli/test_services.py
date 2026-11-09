"""Tests for auto.autocli.services orchestration"""

# pylint: disable=protected-access

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from autocli import services
from autocli.config import CONFIG


@pytest.fixture(autouse=True)
def _fresh_run():
    """Each test is its own run of auto, and never reaches a real registry"""
    services._handled_this_run.clear()
    with patch("autocli.registry.load_missing_images"):
        yield


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
    """A pod that needs buckets triggers _process_pod_databases."""
    pod_config = {
        "system-pods": [
            {"name": "floci-aws", "buckets": [{"name": "uploads"}, {"name": "thumbs"}]},
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
            {"name": "floci-aws", "buckets": [{"name": "uploads"}]},
            {"name": "postgres", "databases": [{"name": "app"}]},
        ],
    }

    with patch(
        "autocli.services._verify_required_dbs_ready", return_value=True
    ) as mock_verify:
        with patch("autocli.services._process_pod_databases"):
            services.create_databases_for_pod("api")
            assert mock_verify.call_args[0][0] == {"floci-aws", "postgres"}


@patch("autocli.services._process_pod_databases")
@patch("autocli.utils.get_pod_config", return_value={})
@patch("autocli.utils.get_required_system_pods", return_value={"mysql", "floci-aws"})
def test_create_databases_waits_only_for_databases_that_were_asked_for(
    _mock_required, _mock_config, mock_process
):
    """A start without Postgres must not wait for it, then skip every bucket"""
    with patch(
        "autocli.services._verify_required_dbs_ready", return_value=True
    ) as mock_verify, patch.dict(CONFIG, {"pods": [{"repo": "x/portal.git"}]}):
        services.create_databases()
    assert mock_verify.call_args.args[0] == {"mysql", "floci-aws"}
    mock_process.assert_called_once()


def test_verify_required_dbs_ready_empty_needs():
    """Empty needs returns True without spinning up any check."""
    with patch("autocli.services._verify_db_system_ready") as mock_check:
        assert services._verify_required_dbs_ready(set()) is True
        mock_check.assert_not_called()


def test_verify_required_dbs_ready_s3_only():
    """A `needs` set containing only floci-aws shouldn't trigger DB readiness checks."""
    with patch("autocli.services._verify_db_system_ready") as mock_check:
        assert services._verify_required_dbs_ready({"floci-aws"}) is True
        mock_check.assert_not_called()


def test_verify_required_dbs_ready_only_targets_db_pods():
    """When needs has both DB and non-DB names, only DBs are checked."""
    with patch(
        "autocli.services._verify_db_system_ready", return_value=True
    ) as mock_check:
        assert services._verify_required_dbs_ready({"mysql", "floci-aws"}) is True
        # Only mysql is a DB target
        assert mock_check.call_count == 1
        assert mock_check.call_args[0][0] == "mysql"


@pytest.mark.parametrize("name", ["floci-aws", "minio"])
@patch("autocli.services.wait_for_floci", return_value=True)
@patch("autocli.services.create_s3_bucket", return_value=True)
def test_buckets_are_made_in_floci_under_either_name(
    mock_create_bucket, _mock_wait, name
):
    """A pod that still says minio gets its buckets in floci-aws too."""
    pod_config = {
        "system-pods": [
            {"name": name, "buckets": [{"name": "uploads"}, {"name": "thumbs"}]},
        ],
    }
    with patch.dict(CONFIG, {"skipped-system-pods": []}):
        services._process_pod_databases(pod_config)

    bucket_names = [call.args[0] for call in mock_create_bucket.call_args_list]
    assert bucket_names == ["uploads", "thumbs"]


@patch("autocli.services.create_s3_bucket")
def test_process_pod_databases_skips_skipped_pods(mock_create_bucket):
    """Pods in skipped-system-pods are not processed."""
    pod_config = {
        "system-pods": [
            {"name": "floci-aws", "buckets": [{"name": "uploads"}]},
        ],
    }
    with patch.dict(CONFIG, {"skipped-system-pods": ["floci-aws"]}):
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
    "autocli.utils.get_required_system_pods",
    return_value={"mysql", "redis", "floci-aws"},
)
def test_cluster_port_args_maps_required_free_ports(_mock_required, _mock_in_use):
    """Only ports this start needs, and only ones free on the host, are mapped.

    A port taken locally is left out so `k3d cluster create` does not fail;
    install_system_pods then skips that pod exactly as before.
    """
    with patch.dict(CONFIG, {"bind-address": "127.0.0.1"}):
        assert services.cluster_port_args() == '-p "127.0.0.1:3306:30036@loadbalancer"'


@patch("autocli.utils.is_port_in_use", return_value=False)
@patch("autocli.utils.get_required_system_pods", return_value={"mysql"})
def test_shared_setup_publishes_database_ports_on_every_interface(
    _mock_required, _mock_in_use
):
    """bind-address: 0.0.0.0 is how a team shares one dev setup on its network."""
    with patch.dict(CONFIG, {"bind-address": "0.0.0.0"}):
        assert services.cluster_port_args() == '-p "0.0.0.0:3306:30036@loadbalancer"'


@patch("autocli.utils.run_and_wait", return_value=True)
@patch("autocli.utils.is_port_in_use", return_value=False)
@patch("autocli.utils.is_port_exposed_on_k3d", return_value=False)
def test_port_added_to_a_running_cluster_uses_the_bind_address(
    _mock_exposed, _mock_in_use, mock_run
):
    """A database started later is published on bind-address too."""
    with patch.dict(CONFIG, {"bind-address": "127.0.0.1"}):
        assert services._expose_system_pod_port(
            "mysql", services.PORT_MAPPINGS["mysql"]
        )
    assert "--port-add 127.0.0.1:3306:30036" in mock_run.call_args.args[0]


MINIO_ENTRY = {"pod": {"name": "minio", "commands": ["kubectl apply -f minio.yaml"]}}
FLOCI_ENTRY = {
    "pod": {"name": "floci-aws", "commands": ["kubectl apply -f floci.yaml"]}
}
ALIAS = f"kubectl apply -f {services.MINIO_ALIAS_MANIFEST}"


@patch("autocli.services.wait_for_floci")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"minio"})
def test_a_pod_asking_for_minio_gets_floci_with_minio_addresses(
    _mock_required, mock_cmd, mock_wait
):
    """MinIO's image is gone, so its stale manifests are never applied.

    floci-aws starts in its place, with MinIO's Service name and hostname on
    top, and loading the system pods doesn't wait for it.
    """
    with patch.dict(CONFIG, {"system-pods": [MINIO_ENTRY, FLOCI_ENTRY]}):
        services.install_system_pods()
    commands = [c.args[0] for c in mock_cmd.call_args_list]
    assert commands == ["kubectl apply -f floci.yaml", ALIAS]
    mock_wait.assert_not_called()


@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"floci-aws"})
def test_floci_alone_does_not_bring_the_minio_names(_mock_required, mock_cmd):
    """minio.local and minio:9000 only appear for pods that still use them"""
    with patch.dict(CONFIG, {"system-pods": [MINIO_ENTRY, FLOCI_ENTRY]}):
        services.install_system_pods()
    assert [c.args[0] for c in mock_cmd.call_args_list] == [
        "kubectl apply -f floci.yaml"
    ]


@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"minio"})
def test_floci_starts_even_when_local_yaml_predates_it(_mock_required, mock_cmd):
    """A local.yaml from before floci shipped has no entry; S3 still starts."""
    with patch.dict(CONFIG, {"system-pods": [MINIO_ENTRY]}):
        services.install_system_pods()
    commands = [c.args[0] for c in mock_cmd.call_args_list]
    assert commands == services.FLOCI_COMMANDS + [ALIAS]
    assert any("floci/deployment.yaml" in cmd for cmd in commands)


@patch("autocli.services.create_s3_bucket", return_value=True)
@patch("autocli.services.wait_for_floci", return_value=True)
def test_buckets_wait_for_floci_to_answer(mock_wait, mock_bucket):
    """Buckets are made once floci answers, not while system pods load."""
    manager = MagicMock()
    manager.attach_mock(mock_wait, "wait")
    manager.attach_mock(mock_bucket, "bucket")

    services._process_s3_buckets(
        {"name": "floci-aws", "buckets": [{"name": "a"}, {"name": "b"}]}
    )

    assert [c[0] for c in manager.mock_calls] == ["wait", "bucket", "bucket"]


@patch("autocli.services.create_s3_bucket")
@patch("autocli.services.wait_for_floci")
def test_s3_without_buckets_waits_for_nothing(mock_wait, mock_bucket):
    """A pod that uses floci-aws but asks for no buckets costs no wait at all."""
    services._process_s3_buckets({"name": "floci-aws"})
    mock_wait.assert_not_called()
    mock_bucket.assert_not_called()


@patch("autocli.services.rprint")
@patch("autocli.services.create_s3_bucket", return_value=False)
@patch("autocli.services.wait_for_floci", return_value=True)
def test_failed_bucket_is_not_reported_created(_mock_wait, _mock_create, mock_rprint):
    """A bucket that could not be made is not announced as created."""
    services._process_s3_buckets(
        {"name": "floci-aws", "buckets": [{"name": "uploads"}]}
    )
    printed = " ".join(str(c.args[0]) for c in mock_rprint.call_args_list)
    assert "Created" not in printed
    assert "Could not create bucket" in printed


@patch("autocli.utils.run_silent")
def test_bucket_is_created_through_the_api_server_proxy(mock_silent):
    """S3 CreateBucket is a body-less PUT, which kubectl sends with replace --raw."""
    assert services.create_s3_bucket("uploads") is True
    cmd = mock_silent.call_args.args[0]
    assert cmd.startswith("kubectl replace --raw ")
    assert f"{services.FLOCI_PROXY}/uploads" in cmd
    assert cmd.endswith(" -f /dev/null")


@patch(
    "autocli.utils.run_silent", side_effect=subprocess.CalledProcessError(1, "kubectl")
)
def test_bucket_creation_failure_is_reported(_mock_silent):
    """The caller says which bucket is missing instead of claiming it was made"""
    assert services.create_s3_bucket("uploads") is False


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
    side_effect=[subprocess.CalledProcessError(1, "kubectl"), None],
)
@patch("autocli.utils.wait_for_pod_status", return_value=True)
def test_wait_for_floci_retries_until_it_answers(_mock_wait, mock_silent, _mock_sleep):
    """A Running pod that is not answering yet is retried, not fatal."""
    assert services.wait_for_floci() is True
    assert mock_silent.call_count == 2
    assert f"{services.FLOCI_PROXY}/_floci/health" in mock_silent.call_args.args[0]


@patch("autocli.services.rprint")
@patch("time.sleep")
@patch(
    "autocli.utils.run_silent", side_effect=subprocess.CalledProcessError(1, "kubectl")
)
@patch("autocli.utils.wait_for_pod_status", return_value=True)
def test_wait_for_floci_gives_up_with_a_warning(
    _mock_wait, _mock_silent, _mock_sleep, mock_rprint
):
    """floci never answering warns instead of crashing `auto start`."""
    assert services.wait_for_floci(retries=3) is False
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


@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"matomo"})
def test_retired_system_pod_is_not_started(_mock_required, mock_cmd):
    """An old local.yaml with matomo active must not apply its stale manifests."""
    matomo = {
        "pod": {
            "name": "matomo",
            "active": True,
            "commands": ["kubectl apply -f ~/.auto/k3s/matomo/deployment.yaml"],
        }
    }
    with patch.dict(CONFIG, {"system-pods": [matomo]}):
        services.install_system_pods()
    mock_cmd.assert_not_called()


@patch("autocli.services.rprint")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.system_pod_requests", return_value={"search": ["shop"]})
@patch("autocli.utils.get_required_system_pods", return_value={"redis", "search"})
def test_undefined_system_pod_is_named_with_the_pod_asking(
    _mock_required, _mock_requests, _mock_cmd, mock_rprint
):
    """shop asks for search, which local.yaml doesn't define: say so."""
    redis = {"pod": {"name": "redis", "commands": ["kubectl apply -f redis.yaml"]}}
    with patch.dict(CONFIG, {"system-pods": [redis]}), patch(
        "autocli.services._expose_system_pod_port", return_value=True
    ):
        services.install_system_pods()
    printed = " ".join(str(c.args[0]) for c in mock_rprint.call_args_list)
    assert "search won't start" in printed
    assert "shop asks for it" in printed
    assert "redis won't start" not in printed


@patch("autocli.services.rprint")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"minio"})
def test_minio_and_floci_without_entries_are_not_called_undefined(
    _mock_required, _mock_cmd, mock_rprint
):
    """minio is served by floci-aws, which starts with or without an entry."""
    with patch.dict(CONFIG, {"system-pods": []}), patch(
        "autocli.services.wait_for_floci", return_value=True
    ):
        services.install_system_pods()
    printed = " ".join(str(c.args[0]) for c in mock_rprint.call_args_list)
    assert "won't start" not in printed


def test_sites_and_api_are_published_on_the_bind_address():
    """With HTTPS, 80 and 443; without, 8088; the API on 6550; all on bind-address."""
    with patch.dict(CONFIG, {"bind-address": "127.0.0.1", "https": True}):
        assert services.load_balancer_args() == (
            '--api-port 127.0.0.1:6550 -p "127.0.0.1:80:80@loadbalancer" '
            '-p "127.0.0.1:443:443@loadbalancer"'
        )
    with patch.dict(CONFIG, {"bind-address": "0.0.0.0", "https": False}):
        assert services.load_balancer_args() == (
            '--api-port 0.0.0.0:6550 -p "0.0.0.0:8088:80@loadbalancer"'
        )


@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.manifest_images", return_value={"mysql:8.0"})
def test_starting_a_system_pod_loads_its_missing_images_first(_mock_images, mock_cmd):
    """Its images may have been skipped while it was off"""
    order = MagicMock()
    with patch("autocli.registry.load_missing_images", order.load):
        mock_cmd.side_effect = order.apply
        services._start_system_pod("mysql", ["kubectl apply -f mysql.yaml"])
    assert [c[0] for c in order.mock_calls] == ["load", "apply"]
    order.load.assert_called_once_with({"mysql:8.0"})


@patch("autocli.services.rprint")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"mysql", "redis"})
def test_databases_start_early_and_are_not_started_twice(
    _mock_required, mock_cmd, _mock_rprint
):
    """The early pass starts mysql; the pass after Envoy Gateway starts the rest"""
    mysql = {"pod": {"name": "mysql", "commands": ["kubectl apply -f mysql.yaml"]}}
    redis = {"pod": {"name": "redis", "commands": ["kubectl apply -f redis.yaml"]}}
    with patch.dict(CONFIG, {"system-pods": [mysql, redis]}), patch(
        "autocli.services._expose_system_pod_port", return_value=True
    ):
        services.install_system_pods(only=services.EARLY_SYSTEM_PODS)
        assert [c.args[0] for c in mock_cmd.call_args_list] == [
            "kubectl apply -f mysql.yaml"
        ]
        services.install_system_pods()
    assert [c.args[0] for c in mock_cmd.call_args_list] == [
        "kubectl apply -f mysql.yaml",
        "kubectl apply -f redis.yaml",
    ]


@patch("autocli.utils.is_port_exposed_on_k3d", return_value=False)
@patch("autocli.utils.is_port_in_use", return_value=True)
@patch("autocli.services.rprint")
@patch("autocli.services._run_command_with_retry")
@patch("autocli.utils.get_required_system_pods", return_value={"mysql"})
def test_a_database_skipped_early_is_warned_about_once(_req, mock_cmd, mock_rprint, *_):
    """Port 3306 is taken: one warning, not one per pass"""
    mysql = {"pod": {"name": "mysql", "commands": ["kubectl apply -f mysql.yaml"]}}
    with patch.dict(CONFIG, {"system-pods": [mysql], "skipped-system-pods": []}):
        services.install_system_pods(only=services.EARLY_SYSTEM_PODS)
        services.install_system_pods()
        assert CONFIG["skipped-system-pods"] == ["mysql"]
    warnings = [
        c for c in mock_rprint.call_args_list if "already in use" in str(c.args[0])
    ]
    assert len(warnings) == 1
    mock_cmd.assert_not_called()

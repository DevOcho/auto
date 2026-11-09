"""Tests for auto.autocli.utils and auto.autocli.config"""

import os
import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest

from autocli import config, utils


@patch("autocli.config.sys.stdin.isatty", return_value=True)
@patch("autocli.config._needs_code_folder", return_value=True)
@patch("autocli.config.Confirm.ask")
@patch("os.makedirs")
@patch("os.path.exists")
@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="code: /tmp/code")
@patch("yaml.safe_load")
def test_load_config(
    mock_yaml, _mock_file, mock_isfile, mock_exists, mock_makedirs, mock_confirm, *_
):
    """Test loading configuration safely routes from config.py"""
    mock_yaml.return_value = {"code": "/tmp/code"}

    # Case 1: Config exists, code dir exists
    mock_isfile.return_value = True
    mock_exists.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"

    # Case 2: Config exists, code folder MISSING (User says YES to create)
    mock_exists.return_value = False
    mock_confirm.return_value = True

    res_config = config.load_config()
    assert res_config["code"] == "/tmp/code"
    mock_makedirs.assert_called_with("/tmp/code")

    # Case 3: Config does not exist (should trigger create_initial_config)
    mock_isfile.return_value = False
    mock_exists.return_value = True
    with patch("autocli.config.create_initial_config") as mock_create:
        config.load_config()
        mock_create.assert_called()

    # Case 4: Code directory missing, user denies creation (triggers fatal error)
    mock_isfile.return_value = True
    mock_exists.return_value = False
    mock_confirm.return_value = False
    with patch("autocli.config._fatal_error") as mock_error:
        config.load_config()
        mock_error.assert_called_with("Code directory missing. Cannot proceed.")


@patch("subprocess.run")
def test_run_and_wait_success(mock_run):
    """Test successful command execution"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"success\n")

    result = utils.run_and_wait("echo test")
    assert result == 1
    mock_run.assert_called_with(
        "echo test", capture_output=True, shell=True, check=True, cwd=None
    )


@patch("subprocess.run")
def test_run_and_wait_check_result(mock_run):
    """Test checking output result"""
    mock_run.return_value = MagicMock(returncode=0, stdout=b"found me\n")

    result = utils.run_and_wait("echo test", check_result="found")
    assert result == 1

    result = utils.run_and_wait("echo test", check_result="missing")
    assert result == 0


@patch("subprocess.run")
def test_run_and_wait_failure(mock_run):
    """Test command failure handling"""
    mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr=b"error")

    result = utils.run_and_wait("fail_cmd")
    assert result == 0


@patch("subprocess.run")
def test_get_full_pod_name(mock_run):
    """Test getting full pod name with only_running filter"""
    mock_run.return_value = MagicMock(stdout=b"mypod-12345\n")

    # Case 1: Default (only_running=True)
    name = utils.get_full_pod_name("mypod")
    assert name == "mypod-12345"
    cmd = mock_run.call_args[0][0][0]
    assert "kubectl get pods" in cmd
    assert "grep Running" in cmd

    # Case 2: only_running=False
    utils.get_full_pod_name("mypod", only_running=False)
    cmd = mock_run.call_args[0][0][0]
    assert "grep Running" not in cmd


@patch("autocli.utils.run_and_return")
def test_get_pod_instances_matches_whole_names(mock_return):
    """`api` must not pick up the pods of `api-gateway` or `api-v2`"""
    mock_return.return_value = "\n".join(
        [
            "api-7d9f8c6b5-x2x9z",
            "api-7d9f8c6b5-q8w7e",
            "api-gateway-5c4b8d9f7-abcde",
            "api-v2-6b7c8d9f5-zzzzz",
            "api-gateway-0",
            "mysql-0",
            "api-0",
        ]
    )

    assert utils.get_pod_instances("api") == [
        "api-7d9f8c6b5-x2x9z",
        "api-7d9f8c6b5-q8w7e",
        "api-0",
    ]
    assert utils.get_pod_instances("api-gateway") == [
        "api-gateway-5c4b8d9f7-abcde",
        "api-gateway-0",
    ]
    assert utils.get_pod_instances("missing") == []


@patch("autocli.utils.run_and_return", return_value="")
def test_get_pod_instances_no_cluster(_mock_return):
    """kubectl failing (cluster down) yields no pods rather than an error"""
    assert not utils.get_pod_instances("api")


@patch("os.getcwd", return_value="/tmp")
@patch("os.chdir")
@patch("os.path.exists")
@patch("autocli.utils.run_and_wait")
def test_pull_repo(mock_run, mock_exists, mock_chdir, _mock_getcwd):
    """Test pulling repositories"""
    repo = {"repo": "git@github.com:org/repo.git", "branch": "main"}

    mock_exists.return_value = True
    # git status + git fetch + git merge --ff-only FETCH_HEAD
    mock_run.side_effect = [True, True, True]

    utils.pull_repo(repo, "/code")
    assert mock_chdir.call_count >= 2

    mock_exists.return_value = False
    mock_chdir.reset_mock()
    mock_run.side_effect = [True, True]

    utils.pull_repo(repo, "/code")
    # index 3 because exists=True path consumed calls 0-2 above
    assert "git clone" in mock_run.call_args_list[3][0][0]


@patch("autocli.utils.rprint")
@patch("os.getcwd", return_value="/tmp")
@patch("os.chdir")
@patch("os.path.exists")
@patch("autocli.utils.run_and_wait")
def test_pull_repo_divergence_leaves_untouched(
    mock_run, mock_exists, mock_chdir, _mock_getcwd, mock_rprint
):
    """Divergent local branch: ff-only merge fails, repo is left untouched"""
    repo = {"repo": "git@github.com:org/repo.git", "branch": "main"}

    mock_exists.return_value = True
    # git status (clean) + git fetch (ok) + git merge --ff-only (fails: diverged)
    mock_run.side_effect = [True, True, False]

    utils.pull_repo(repo, "/code")

    # No destructive fallback command — only status, fetch, and the failed merge
    assert mock_run.call_count == 3

    # cwd must be restored after the failed ff-only merge
    mock_chdir.assert_called_with("/tmp")

    # Warning must communicate "out of sync" + "left untouched", not "Skipping"
    warning_text = mock_rprint.call_args[0][0]
    assert "out of sync" in warning_text
    assert "left untouched" in warning_text
    assert "Skipping" not in warning_text


@patch("os.path.isfile")
@patch("builtins.open", new_callable=mock_open, read_data="name: test\n")
@patch("yaml.safe_load")
def test_get_pod_config(mock_yaml, _mock_file, mock_isfile):
    """Test fetching pod config mapped straight to global CONFIG object"""
    mock_isfile.return_value = True
    mock_yaml.return_value = {"name": "test"}

    # Simulate dynamically patched dictionary entry
    with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
        res_config = utils.get_pod_config("mypod")
        assert res_config["name"] == "test"

    mock_isfile.return_value = False
    with patch("autocli.utils.declare_error") as mock_err:
        with patch.dict("autocli.config.CONFIG", {"code": "/code"}):
            utils.get_pod_config("mypod")
            mock_err.assert_called()


NODES_OUTPUT = (
    "k3d-k3s-default-serverlb loadbalancer\n"
    "k3d-k3s-default-agent-0 agent\n"
    "k3d-k3s-default-server-0 server"
)


@patch("autocli.utils.run_and_wait")
@patch("autocli.utils.socket.gethostbyname", side_effect=OSError)
def test_nodes_without_dns_unknown_when_host_offline(_mock_resolve, mock_run):
    """If the host can't resolve the probe either, there's nothing to compare"""
    assert utils.nodes_without_dns() is None
    mock_run.assert_not_called()


@patch("autocli.utils.run_and_wait", return_value=1)
@patch("autocli.utils.run_and_return", return_value=NODES_OUTPUT)
@patch("autocli.utils.socket.gethostbyname", return_value="140.82.112.3")
def test_nodes_without_dns_probes_only_server_and_agent(
    _mock_resolve, _mock_nodes, mock_run
):
    """Healthy nodes report nothing, and the load balancer is never probed"""
    assert utils.nodes_without_dns() == []

    probed = [call.args[0] for call in mock_run.call_args_list]
    assert probed == [
        "docker exec k3d-k3s-default-agent-0 nslookup github.com",
        "docker exec k3d-k3s-default-server-0 nslookup github.com",
    ]


@patch("autocli.utils.run_and_return", return_value=NODES_OUTPUT)
@patch("autocli.utils.socket.gethostbyname", return_value="140.82.112.3")
def test_nodes_without_dns_reports_failing_nodes(_mock_resolve, _mock_nodes):
    """A node whose lookup fails while the host's succeeds is reported"""

    def lookup(cmd, **_kwargs):
        return 0 if "server-0" in cmd else 1

    with patch("autocli.utils.run_and_wait", side_effect=lookup):
        assert utils.nodes_without_dns() == ["k3d-k3s-default-server-0"]


def test_get_cluster_dns_status():
    """Each probe outcome maps to a status line for auto status"""
    with patch("autocli.utils.nodes_without_dns", return_value=[]):
        assert utils.get_cluster_dns_status() == ("Working", "green")
    with patch(
        "autocli.utils.nodes_without_dns", return_value=["k3d-k3s-default-server-0"]
    ):
        assert utils.get_cluster_dns_status()[1] == "red"
    with patch("autocli.utils.nodes_without_dns", return_value=None):
        assert utils.get_cluster_dns_status()[1] == "yellow"


def test_declare_error_exits_non_zero():
    """A fatal error must not look like success to a script or `script -qec`"""
    with pytest.raises(SystemExit) as exited:
        utils.declare_error("boom")
    assert exited.value.code == 1


def test_declare_error_can_continue():
    """exit_auto=False only reports; callers decide what happens next"""
    assert utils.declare_error("just saying", exit_auto=False) is None


def test_bind_address_defaults_to_this_machine():
    """Without bind-address, nothing auto publishes is reachable from the network."""
    with patch.dict(utils.CONFIG, {}):
        utils.CONFIG.pop("bind-address", None)
        assert utils.bind_address() == "127.0.0.1"


def test_bind_address_must_be_an_ipv4_address():
    """A typo stops auto with a message instead of reaching k3d."""
    with patch.dict(utils.CONFIG, {"bind-address": "lan"}), pytest.raises(SystemExit):
        utils.bind_address()


@pytest.mark.parametrize(
    "bind, published, warns",
    [
        ("127.0.0.1", "80/tcp -> 0.0.0.0:80\n80/tcp -> [::]:80", True),
        ("127.0.0.1", "80/tcp -> 127.0.0.1:80", False),
        ("0.0.0.0", "80/tcp -> 0.0.0.0:80\n80/tcp -> [::]:80", False),
        ("0.0.0.0", "80/tcp -> 127.0.0.1:80", True),
        ("127.0.0.1", "", False),
    ],
)
def test_container_made_with_another_address_is_reported(bind, published, warns):
    """A registry or cluster made before bind-address was set keeps its old ports."""
    with (
        patch.dict(utils.CONFIG, {"bind-address": bind}),
        patch("autocli.utils.run_and_return", return_value=published),
        patch("autocli.utils.rprint") as mock_print,
    ):
        utils.warn_if_bound_elsewhere(
            "k3d-registry.local", "The image registry", "recreate it"
        )
    assert mock_print.called is warns


def test_system_pod_requests_name_the_pods_asking(tmp_path):
    """Each requested system pod maps to the pods whose .auto/config.yaml ask for it"""
    for repo, pods in {"shop": ["mysql", "floci-aws"], "blog": ["mysql"]}.items():
        (tmp_path / repo / ".auto").mkdir(parents=True)
        (tmp_path / repo / ".auto" / "config.yaml").write_text(
            "system-pods:\n" + "".join(f"  - name: {p}\n" for p in pods),
            encoding="utf-8",
        )
    cfg = {
        "code": str(tmp_path),
        "pods": [
            {"repo": "git@example.com:acme/shop.git"},
            {"repo": "git@example.com:acme/blog.git"},
        ],
        "system-pods": [
            {"pod": {"name": "redis", "active": True}},
            {"pod": {"name": "mailpit"}},
        ],
    }
    assert utils.system_pod_requests(cfg) == {
        "mysql": ["shop", "blog"],
        "floci-aws": ["shop"],
    }
    assert utils.get_required_system_pods(cfg) == {"mysql", "floci-aws", "redis"}


@pytest.mark.parametrize(
    "image, plain",
    [
        ("k3d-registry.local:12345/mysql:8.0", "mysql:8.0"),
        ("docker.io/library/postgres:18.1-trixie", "postgres:18.1-trixie"),
        (
            "docker.io/envoyproxy/envoy:distroless-v1.39.5",
            "envoyproxy/envoy:distroless-v1.39.5",
        ),
        ("floci/floci:2.1.0@sha256:abc", "floci/floci:2.1.0"),
        (
            "mcr.microsoft.com/mssql/server:2022-latest",
            "mcr.microsoft.com/mssql/server:2022-latest",
        ),
    ],
)
def test_plain_image_name_matches_how_registry_lists_it(image, plain):
    """local.yaml's registry: names images without the local registry or docker.io"""
    assert utils.plain_image_name(image) == plain


def test_manifest_images_reads_every_container_the_commands_apply(tmp_path):
    """Containers and init containers, across documents; routes and missing files add none"""
    (tmp_path / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n"
        "      initContainers:\n      - name: init\n        image: busybox:1.36\n"
        "      containers:\n      - name: db\n        image: k3d-registry.local:12345/mysql:8.0\n"
        "---\napiVersion: v1\nkind: Service\nmetadata:\n  name: db\n",
        encoding="utf-8",
    )
    (tmp_path / "route.yaml").write_text(
        "apiVersion: gateway.networking.k8s.io/v1\nkind: HTTPRoute\nmetadata:\n  name: db\n",
        encoding="utf-8",
    )
    commands = [
        f"kubectl apply -f {tmp_path}/deployment.yaml",
        f"kubectl apply -f {tmp_path}/route.yaml",
        f"kubectl apply -f {tmp_path}/missing.yaml",
        "kubectl create namespace db",
    ]
    assert utils.manifest_images(commands) == {"busybox:1.36", "mysql:8.0"}


def test_auto_s_own_system_pod_manifests_name_their_images():
    """The shipped manifests are what populate_registry reads"""
    k3s = os.path.join(os.path.dirname(__file__), "..", "k3s")
    mysql = [f"kubectl apply -f {k3s}/mysql/deployment.yaml"]
    floci = [f"kubectl apply -f {k3s}/floci/deployment.yaml"]
    assert utils.manifest_images(mysql) == {"mysql:8.0"}
    assert utils.manifest_images(floci) == {"floci/floci:2.1.0"}


@pytest.mark.parametrize("ready_after, expected", [(0, True), (3, True), (99, False)])
def test_wait_for_api_waits_until_apply_would_work(ready_after, expected):
    """After a resume, cluster-info answers before kubectl apply's schema is served"""
    calls = []

    def probe(cmd, **_kw):
        calls.append(cmd)
        return MagicMock(returncode=0 if len(calls) > ready_after else 1)

    with (
        patch("autocli.utils.subprocess.run", side_effect=probe),
        patch("autocli.utils.sleep") as nap,
        patch("autocli.utils.rprint") as say,
    ):
        assert utils.wait_for_api(max_wait_time=10) is expected
    assert all("/openapi/v3" in cmd for cmd in calls)
    assert nap.call_count == min(ready_after, 10)
    assert say.called == (ready_after > 0)


@pytest.mark.parametrize(
    "config, sites",
    [
        ({}, "127.0.0.1"),
        ({"bind-address": "0.0.0.0"}, "0.0.0.0"),
        ({"sites-bind-address": "0.0.0.0"}, "0.0.0.0"),
        ({"bind-address": "0.0.0.0", "sites-bind-address": "127.0.0.1"}, "127.0.0.1"),
    ],
)
def test_sites_follow_bind_address_unless_set_on_their_own(config, sites):
    """A team can share its sites without sharing the databases, registry and API"""
    with patch.dict(utils.CONFIG, config):
        for key in ("bind-address", "sites-bind-address"):
            if key not in config:
                utils.CONFIG.pop(key, None)
        assert utils.sites_bind_address() == sites


def test_sites_bind_address_must_be_an_ipv4_address():
    """A typo stops auto with a message naming the setting"""
    with (
        patch.dict(utils.CONFIG, {"sites-bind-address": "office"}),
        patch("autocli.utils.rprint") as say,
        pytest.raises(SystemExit),
    ):
        utils.sites_bind_address()
    assert "sites-bind-address" in " ".join(str(c.args[0]) for c in say.call_args_list)


LB_PORTS = "80/tcp -> {sites}:80\n443/tcp -> {sites}:443\n6443/tcp -> {api}:6550\n30036/tcp -> {api}:3306"


@pytest.mark.parametrize(
    "sites_setting, made_with, warns_about",
    [
        ("0.0.0.0", {"sites": "0.0.0.0", "api": "127.0.0.1"}, set()),
        ("127.0.0.1", {"sites": "127.0.0.1", "api": "127.0.0.1"}, set()),
        # made by 0.9.3, now asked to share the sites: recreate
        ("0.0.0.0", {"sites": "127.0.0.1", "api": "127.0.0.1"}, {"sites-bind-address"}),
        # the API and databases were shared, but only the sites should be
        ("0.0.0.0", {"sites": "0.0.0.0", "api": "0.0.0.0"}, {"bind-address"}),
    ],
)
def test_each_cluster_port_is_checked_against_its_own_setting(
    sites_setting, made_with, warns_about
):
    """Site ports follow sites-bind-address; the API and database ports bind-address"""
    with (
        patch.dict(
            utils.CONFIG,
            {"bind-address": "127.0.0.1", "sites-bind-address": sites_setting},
        ),
        patch(
            "autocli.utils.run_and_return", return_value=LB_PORTS.format(**made_with)
        ),
        patch("autocli.utils.rprint") as say,
    ):
        utils.warn_if_bound_elsewhere(
            "k3d-k3s-default-serverlb",
            "The cluster",
            "recreate it",
            site_ports=("80", "443"),
        )
    printed = " ".join(str(c.args[0]) for c in say.call_args_list)
    assert {s for s in ("(sites-bind-address)", "(bind-address)") if s in printed} == {
        f"({s})" for s in warns_about
    }

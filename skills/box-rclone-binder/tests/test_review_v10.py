"""Generated deployment regressions with in-memory hosts and no remote commands."""
import builtins
import contextlib
import copy
import io
import json
from pathlib import Path
import posixpath
import stat
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "skills/box-rclone-binder/scripts"))
from make_fixtures import box10_deployment_cases
from boxbinder import config, deploy
from boxbinder.drivers import FakeHostDriver, RemoteError, SSHHostDriver


@pytest.fixture
def cases():
    return box10_deployment_cases()


def snapshot(driver):
    return copy.deepcopy((driver.fs, driver.directories, driver.services,
                          driver.mutations, driver.exec_log))


def installed(cases):
    driver = FakeHostDriver(cases["master"])
    deploy.deploy_host(driver, cases["master"], credentials=cases["credentials"])
    return driver


def test_shared_system_directory_spellings_reject_before_driver_access(cases):
    for path in cases["shared_spellings"]:
        host = {**cases["slave"], "config_dir": path}
        driver = FakeHostDriver(host)
        before = snapshot(driver)
        with pytest.raises(config.ConfigError):
            deploy.deploy_host(driver, host)
        assert snapshot(driver) == before


@pytest.mark.parametrize("mode", [0o755, 0o775])
def test_unproven_existing_directory_keeps_its_permissions(cases, mode):
    host = {**cases["slave"], "config_dir": cases["unproven_directory"]}
    driver = FakeHostDriver(host, directories={host["config_dir"]: mode})
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="private directory"):
        deploy.deploy_host(driver, host)
    assert snapshot(driver) == before


def test_non_directory_or_alias_result_rejects_before_mutation(cases):
    host = {**cases["slave"], "config_dir": cases["unproven_directory"]}
    driver = FakeHostDriver(host)
    original_stat = driver.stat
    driver.stat = lambda path: ({"kind": "other", "mode": 0o755}
                                if path == host["config_dir"] else original_stat(path))
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="private directory"):
        deploy.deploy_host(driver, host)
    assert snapshot(driver) == before


def test_directory_created_during_probe_is_never_chmodded(cases):
    path = cases["unproven_directory"]

    class RacedDirectory(FakeHostDriver):
        def exec(self, argv, **kwargs):
            if argv[:2] == ["mkdir", "-p"]:
                self.directories[path] = 0o755
            return super().exec(argv, **kwargs)

    driver = RacedDirectory(cases["slave"])
    with pytest.raises(RemoteError, match="readback"):
        driver.ensure_directory(path, 0o700)
    assert driver.directories[path] == 0o755


@pytest.mark.parametrize("legacy", [False, True])
def test_role_and_directory_change_cannot_hide_installed_master(cases, legacy):
    driver = installed(cases)
    if legacy:
        driver.fs.pop(cases["locator"])
        driver.modes.pop(cases["locator"])
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="role migration"):
        deploy.deploy_host(driver, cases["moved_slave"])
    assert snapshot(driver) == before


def test_same_role_directory_migration_requires_existing_state_review(cases):
    driver = installed(cases)
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="directory migration"):
        deploy.deploy_host(driver, cases["moved_master"], credentials=cases["credentials"])
    assert snapshot(driver) == before


def test_locator_without_its_host_settings_cannot_be_adopted(cases):
    driver = installed(cases)
    driver.fs.pop(cases["master"]["config_dir"] + "/host.json")
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="missing"):
        deploy.deploy_host(driver, cases["master"], credentials=cases["credentials"])
    assert snapshot(driver) == before


def test_loaded_orphan_broker_unit_blocks_fresh_slave_deployment(cases):
    driver = FakeHostDriver(cases["slave"], services={
        "box-binder-broker.timer": {"enabled": True, "active": True}})
    with pytest.raises(RemoteError, match="installed unit source"):
        deploy.deploy_host(driver, cases["slave"])
    assert driver.mutations == 0
    assert driver.fs == {} and driver.directories == {}
    assert all(not mutate for _, mutate in driver.exec_log)
    assert driver.services["box-binder-broker.timer"]["active"]


def test_unproven_unit_metadata_fails_before_mutation(cases):
    for output in cases["unproven_unit_outputs"]:
        driver = FakeHostDriver(cases["slave"], responses={"systemctl show": (0, output, "")})
        with pytest.raises(RemoteError, match="prior runtime state"):
            deploy.deploy_host(driver, cases["slave"])
        assert driver.mutations == 0 and driver.fs == {} and driver.directories == {}


def test_private_deployment_and_locator_are_idempotent(cases):
    driver = installed(cases)
    before = driver.mutations
    outcome = deploy.deploy_host(driver, cases["master"], credentials=cases["credentials"])
    assert outcome["status"] == "configured" and outcome["mutations"] == 0
    assert driver.mutations == before
    assert json.loads(driver.fs[cases["locator"]]) == json.loads(
        driver.fs[cases["master"]["config_dir"] + "/host.json"])


def test_remote_metadata_probe_normalizes_spellings_and_rejects_symlinks(cases):
    visited = []
    def lstat(path):
        visited.append(path)
        if path not in cases["metadata_entries"]:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=cases["metadata_entries"][path])

    driver = SSHHostDriver(cases["slave"])
    def in_memory_probe(argv, **kwargs):
        assert argv[:2] == ["python3", "-c"]
        modules = {
            "json": json, "os": SimpleNamespace(path=posixpath, lstat=lstat),
            "stat": stat, "sys": SimpleNamespace(argv=["-c", argv[3]]),
        }
        local_builtins = dict(vars(builtins))
        local_builtins["__import__"] = lambda name, *args, **options: modules[name]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(argv[2], {"__builtins__": local_builtins})
        return output.getvalue()

    driver.checked_exec = in_memory_probe
    for path in cases["metadata_spellings"]:
        visited.clear()
        assert driver.stat(path) == {"kind": "file", "mode": 0o600}
        assert visited == cases["metadata_expected_walk"]
    visited.clear()
    with pytest.raises(ValueError, match="path alias"):
        driver.stat(cases["metadata_symlink"])
    assert visited[-1] == cases["metadata_symlink_parent"]

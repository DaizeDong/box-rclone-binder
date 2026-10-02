"""Generated Box11 regressions using only in-memory host transport."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "skills/box-rclone-binder/scripts"))
from make_fixtures import box11_deployment_cases
from boxbinder import deploy, remote
from boxbinder.drivers import FakeHostDriver, RemoteError


def snapshot(driver):
    return copy.deepcopy((driver.fs, driver.modes, driver.directories,
                          driver.services, driver.mutations))


def installed(host, cases):
    driver = FakeHostDriver(host)
    deploy.deploy_host(driver, host, credentials=cases["credentials"])
    driver.exec_log.clear()
    return driver


def timer_path(action):
    return "/etc/systemd/system/box-binder-" + action + ".timer"


def marker_path(host):
    return host.get("config_dir", "/etc/box-binder") + "/loaded-runtime.sha256"


def test_all_auth_mode_transitions_refuse_before_mutation():
    cases = box11_deployment_cases()
    for previous, requested in cases["transitions"]:
        driver = installed(previous, cases)
        before = snapshot(driver)
        with pytest.raises(RemoteError, match="auth mode migration"):
            deploy.deploy_host(driver, requested, credentials=cases["credentials"])
        assert snapshot(driver) == before
        assert all(not mutate for _, mutate in driver.exec_log)


@pytest.mark.parametrize("action", ["health", "broker"])
def test_wrong_timer_unit_is_rejected_without_mutation(action):
    cases = box11_deployment_cases()
    host = cases["master"]
    driver = installed(host, cases)
    path = timer_path(action)
    driver.fs[path] = driver.fs[path].replace(
        "Unit=box-binder-" + action + ".service", "Unit=" + cases["unexpected_unit"])
    before = snapshot(driver)
    with pytest.raises(RemoteError, match="timer definition"):
        deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert snapshot(driver) == before


def test_legacy_custom_schedules_remain_idempotent():
    cases = box11_deployment_cases()
    host = cases["custom_host"]
    driver = installed(host, cases)
    for path in (host["config_dir"] + "/host.json", cases["locator"]):
        settings = json.loads(driver.fs[path])
        assert "health_interval" not in settings and "mint_interval_min" not in settings
    before = driver.mutations
    outcome = deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert outcome["mutations"] == 0 and driver.mutations == before


def test_timer_drift_forces_reload_even_with_current_marker():
    cases = box11_deployment_cases()
    host = cases["master"]
    driver = installed(host, cases)
    marker = marker_path(host)
    expected = driver.fs[marker]
    driver.fs[timer_path("health")] = remote._timer("health", cases["calendar_drift"])
    deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert driver.fs[timer_path("health")] == remote.render_health_timer(host)
    assert driver.fs[marker] == expected
    assert ("systemctl daemon-reload", True) in driver.exec_log


def test_failed_reload_remains_pending_after_timer_bytes_match():
    cases = box11_deployment_cases()
    host = cases["master"]
    driver = installed(host, cases)
    marker = marker_path(host)
    expected = driver.fs[marker]
    driver.fs[timer_path("health")] = remote._timer("health", cases["calendar_drift"])
    driver.responses["systemctl daemon-reload"] = (1, "", "synthetic reload failure")
    with pytest.raises(RemoteError):
        deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert driver.fs[timer_path("health")] == remote.render_health_timer(host)
    assert driver.fs[marker] != expected
    driver.responses.pop("systemctl daemon-reload")
    driver.exec_log.clear()
    deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert ("systemctl daemon-reload", True) in driver.exec_log
    assert driver.fs[marker] == expected


def test_pending_marker_precedes_any_systemd_unit_write():
    cases = box11_deployment_cases()
    host = cases["master"]

    class InterruptedHost(FakeHostDriver):
        fail_unit_write = False

        def write_text(self, path, content, mode=0o600):
            if self.fail_unit_write and path == timer_path("health"):
                raise RemoteError("synthetic interrupted unit write", "interrupted")
            return super().write_text(path, content, mode)

    driver = InterruptedHost(host)
    deploy.deploy_host(driver, host, credentials=cases["credentials"])
    marker = marker_path(host)
    expected = driver.fs[marker]
    driver.fs[timer_path("health")] = remote._timer("health", cases["calendar_drift"])
    driver.fail_unit_write = True
    with pytest.raises(RemoteError, match="interrupted unit write"):
        deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert driver.fs[marker] != expected


def test_valid_custom_schedule_change_reloads_and_then_is_idempotent():
    cases = box11_deployment_cases()
    driver = installed(cases["master"], cases)
    host = cases["custom_host"]
    deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert driver.fs[timer_path("health")] == remote.render_health_timer(host)
    assert driver.fs[timer_path("broker")] == remote._timer("broker", "*:0/17")
    assert ("systemctl daemon-reload", True) in driver.exec_log
    driver.exec_log.clear()
    result = deploy.deploy_host(driver, host, credentials=cases["credentials"])
    assert result["mutations"] == 0
    assert all(not mutate for _, mutate in driver.exec_log)

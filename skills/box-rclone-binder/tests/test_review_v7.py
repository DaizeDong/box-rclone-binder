"""Generated Box6 and Box7 regressions; synthetic inputs and inert host transport."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "skills/box-rclone-binder/scripts"))
from make_fixtures import box6_cases, box7_path_cases
import box_binder as cli
from boxbinder import config, deploy, health, remote
from boxbinder.drivers import FakeHostDriver, RemoteError, SSHHostDriver


def invoke(command, data, factory=None):
    output = io.StringIO()
    with patch.object(config, "open", create=True, return_value=io.StringIO(json.dumps(data))), \
            patch.object(cli, "discover_config_path", return_value="synthetic-inventory.json"), \
            contextlib.redirect_stdout(output):
        code = cli.run([command, "-c", "synthetic-inventory.json", "--json"], factory=factory)
    return code, json.loads(output.getvalue())


def broker_host(config_dir="/etc/box-binder"):
    return {"host": "node1.example.com", "remote_name": "box", "auth_mode": "oauth-broker",
            "broker_role": "slave", "config_dir": config_dir}


class Box6BehaviorTests(unittest.TestCase):
    def test_observed_health_roles_keep_unknown_distinct(self):
        generated = box6_cases()
        for case in generated["health"]:
            with self.subTest(case=case["id"]):
                roles = case["roles"]
                mode = "jwt" if "jwt" in roles else "oauth-broker"
                hosts = [dict(generated["base_inventory"]["hosts"][0],
                              host="node%d.example.com" % (index + 1), auth_mode=mode,
                              broker_role="master" if index == 0 else "slave")
                         for index in range(len(roles))]
                calls = []

                def factory(host, **kwargs):
                    index = next(i for i, item in enumerate(hosts) if item["host"] == host["host"])
                    calls.append(host["host"])

                    def response(argv, input_text):
                        if case.get("failed_index") == index:
                            raise RemoteError("SSH transport", "unavailable or timed out")
                        receipt = {"host": host["host"], "ok": True, "action": "health", "auth_mode": mode,
                                   "operation_id": argv[argv.index("--operation-id") + 1]}
                        if roles[index] is not None:
                            observed = health.runtime_settings(dict(host, broker_role=roles[index]))
                            receipt["observed_settings"] = dict(observed, rclone_version="v1.71.0")
                        return 0, json.dumps(receipt), ""

                    return FakeHostDriver(host, responses={"python3": response})

                code, report = invoke("healthcheck", {"version": 1, "hosts": hosts}, factory)
                self.assertIs(report["refresh_token_invariant"]["ok"], case["invariant"])
                self.assertEqual(code, case["exit"])
                self.assertEqual(len(report["hosts"]), len(hosts))
                self.assertEqual(len(calls), len(hosts))

    def test_doctor_continues_after_each_failed_host(self):
        generated = box6_cases()
        for case in generated["doctor"]:
            with self.subTest(case=case["id"]):
                data = copy.deepcopy(generated["base_inventory"])
                data["hosts"].append(dict(data["hosts"][0], host="node2.example.com"))
                calls = []

                def factory(host, **kwargs):
                    index = len(calls)
                    calls.append(host["host"])
                    failure = case.get("failure") if index == case.get("fail_index") else None
                    if failure == "factory":
                        raise ValueError("synthetic factory detail must not be reported")

                    def rclone(argv, input_text):
                        if failure == "transport":
                            raise RemoteError("SSH transport", "unavailable or timed out")
                        if failure == "malformed":
                            return 0, None, ""
                        if failure == "rclone-nonzero":
                            return 127, "rclone v1.71.0", "synthetic stderr must not be reported"
                        if failure == "rclone-empty":
                            return 0, "", ""
                        return 0, "rclone v1.71.0", ""

                    systemd = (1, "yes", "synthetic stderr must not be reported") if failure == "systemd-nonzero" else (
                        (0, "no", "") if failure == "systemd-missing" else (0, "yes", ""))
                    return FakeHostDriver(host, responses={"rclone": rclone, "sh": systemd})

                code, report = invoke("doctor", data, factory)
                self.assertEqual(code, case["exit"])
                self.assertEqual(report["exit_code"], code)
                self.assertEqual(calls, [host["host"] for host in data["hosts"]])
                self.assertEqual([host["host"] for host in report["hosts"]], calls)
                self.assertNotIn("must not be reported", json.dumps(report))
                if case.get("failure", "").startswith("systemd"):
                    self.assertEqual(report["hosts"][0]["rclone"], "rclone v1.71.0")

    def test_invalid_fields_refuse_before_driver_creation(self):
        generated = box6_cases()
        for case in generated["invalid_fields"]:
            with self.subTest(case=case["id"]):
                data = copy.deepcopy(generated["base_inventory"])
                target = data["hosts"][0] if case["scope"] == "host" else data.setdefault("defaults", {})
                target[case["field"]] = case["value"]
                factory = Mock(side_effect=AssertionError("invalid configuration reached the driver"))
                code, report = invoke("doctor", data, factory)
                self.assertEqual(code, 3)
                self.assertIsNot(report.get("schema_valid"), True)
                factory.assert_not_called()

    def test_valid_fields_remain_usable(self):
        generated = box6_cases()
        for index, changes in enumerate(generated["valid_fields"]):
            with self.subTest(case=str(index)):
                data = copy.deepcopy(generated["base_inventory"])
                data["hosts"][0].update(changes)
                code, report = invoke("verify-config", data)
                self.assertEqual(code, 0)
                self.assertIs(report.get("schema_valid"), True)
                SSHHostDriver(data["hosts"][0])


class Box7PathTests(unittest.TestCase):
    def test_invalid_current_paths_refuse_before_driver_creation(self):
        for case in box7_path_cases()["invalid"]:
            for scope in ("host", "defaults"):
                with self.subTest(case=case["id"], scope=scope):
                    data = copy.deepcopy(box6_cases()["base_inventory"])
                    target = data["hosts"][0] if scope == "host" else data.setdefault("defaults", {})
                    target["config_dir"] = case["path"]
                    factory = Mock(side_effect=AssertionError("invalid path reached the driver"))
                    code, report = invoke("doctor", data, factory)
                    self.assertEqual(code, 3)
                    self.assertIsNot(report.get("schema_valid"), True)
                    factory.assert_not_called()

    def test_direct_deployment_refuses_before_planning_or_driver_access(self):
        for case in box7_path_cases()["invalid"]:
            for dry_run in (False, True):
                with self.subTest(case=case["id"], dry_run=dry_run):
                    driver = Mock(spec=FakeHostDriver)
                    with patch.object(remote, "runtime_manifest") as manifest:
                        with self.assertRaises(config.ConfigError):
                            deploy.deploy_host(driver, broker_host(case["path"]), dry_run=dry_run)
                        manifest.assert_not_called()
                    self.assertEqual(driver.mock_calls, [])

    def test_invalid_persisted_paths_refuse_before_prerequisites_or_mutations(self):
        for case in box7_path_cases()["invalid"]:
            with self.subTest(case=case["id"]):
                host = broker_host()
                prior = broker_host(case["path"])
                driver = FakeHostDriver(host, fs={host["config_dir"] + "/host.json": json.dumps(prior)})
                before = dict(driver.fs)
                with self.assertRaises(RemoteError):
                    deploy.deploy_host(driver, host)
                self.assertEqual(driver.exec_log, [])
                self.assertEqual(driver.mutations, 0)
                self.assertEqual(driver.fs, before)

    def test_dedicated_directories_remain_valid_and_idempotent(self):
        for path in box7_path_cases()["valid"]:
            with self.subTest(path=path):
                host = broker_host(path)
                config.validate_host(host)
                driver = FakeHostDriver(host)
                first = deploy.deploy_host(driver, host)
                second = deploy.deploy_host(driver, host)
                self.assertEqual(first["status"], "configured")
                self.assertEqual(second["status"], "configured")
                self.assertEqual(second["mutations"], 0)


if __name__ == "__main__":
    unittest.main()

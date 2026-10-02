"""Generated Box9 regressions for private config-directory separation."""
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
from make_fixtures import box6_cases, box9_reserved_path_cases
import box_binder as cli
from boxbinder import config, deploy, remote
from boxbinder.drivers import FakeHostDriver, RemoteError


def broker_host(config_dir="/etc/box-binder"):
    return {"host": "node1.example.com", "remote_name": "box", "auth_mode": "oauth-broker",
            "broker_role": "slave", "config_dir": config_dir}


class Box9ReservedDirectoryTests(unittest.TestCase):
    def test_current_configuration_rejects_reserved_directories_before_driver_creation(self):
        for case in box9_reserved_path_cases()["invalid"]:
            for scope in ("host", "defaults"):
                with self.subTest(case=case["id"], scope=scope):
                    data = copy.deepcopy(box6_cases()["base_inventory"])
                    target = data["hosts"][0] if scope == "host" else data.setdefault("defaults", {})
                    target["config_dir"] = case["path"]
                    factory = Mock(side_effect=AssertionError("reserved path reached the driver"))
                    output = io.StringIO()
                    with patch.object(config, "open", create=True, return_value=io.StringIO(json.dumps(data))), \
                            patch.object(cli, "discover_config_path", return_value="synthetic-inventory.json"), \
                            contextlib.redirect_stdout(output):
                        code = cli.run(["doctor", "-c", "synthetic-inventory.json", "--json"], factory=factory)
                    report = json.loads(output.getvalue())
                    self.assertEqual(code, 3)
                    self.assertIsNot(report.get("schema_valid"), True)
                    factory.assert_not_called()

    def test_direct_deployment_rejects_reserved_directories_before_planning_or_driver_access(self):
        for case in box9_reserved_path_cases()["invalid"]:
            for dry_run in (False, True):
                with self.subTest(case=case["id"], dry_run=dry_run):
                    driver = Mock(spec=FakeHostDriver)
                    with patch.object(remote, "runtime_manifest") as manifest:
                        with self.assertRaises(config.ConfigError):
                            deploy.deploy_host(driver, broker_host(case["path"]), dry_run=dry_run)
                        manifest.assert_not_called()
                    self.assertEqual(driver.mock_calls, [])

    def test_persisted_reserved_directories_reject_before_prerequisites_or_mutations(self):
        for case in box9_reserved_path_cases()["invalid"]:
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

    def test_dedicated_directories_keep_private_mode_and_runtime_directories_keep_shared_modes(self):
        cases = box9_reserved_path_cases()
        for path in cases["valid"]:
            with self.subTest(path=path):
                host = broker_host(path)
                config.validate_host(host)
                driver = FakeHostDriver(host)
                planned = deploy.deploy_host(driver, host, dry_run=True)
                first = deploy.deploy_host(driver, host)
                second = deploy.deploy_host(driver, host)
                self.assertEqual(planned["directories"][path], 0o700)
                self.assertEqual(driver.stat(path), {"kind": "directory", "mode": 0o700})
                for reserved in cases["reserved"]:
                    self.assertEqual(planned["directories"][reserved], 0o755)
                    self.assertEqual(driver.stat(reserved), {"kind": "directory", "mode": 0o755})
                self.assertEqual(first["status"], "configured")
                self.assertEqual(second["status"], "configured")
                self.assertEqual(second["mutations"], 0)


if __name__ == "__main__":
    unittest.main()

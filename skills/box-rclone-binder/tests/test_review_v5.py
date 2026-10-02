"""Generated Box5 controls; all hosts and observations are synthetic and external actions inert."""
import argparse
import ast
import builtins
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "skills/box-rclone-binder/scripts/"


def definitions(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_bytes(), str(path))
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and (names is None or node.name in names)]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def fixture():
    namespace = {"json": json, "RECIPE": "box-binder-synthetic-v1"}
    definitions("tools/make_fixtures.py", {"inventory", "box5_cases"}, namespace)
    return namespace["box5_cases"]()


def generator_control(storage, *, check=False, out="/synthetic/out"):
    class MemoryPath(PurePosixPath):
        def resolve(self):
            return self
        def is_file(self):
            return str(self) in storage
        def read_bytes(self):
            return storage[str(self)]
        def write_bytes(self, value):
            storage[str(self)] = value
            return len(value)
        def mkdir(self, *args, **kwargs):
            return None
    parser = SimpleNamespace(add_argument=lambda *args, **kwargs: None,
        parse_args=lambda: SimpleNamespace(check=check, out=MemoryPath(out) if out else None))
    real_import = builtins.__import__
    modules = {"pathlib": SimpleNamespace(Path=MemoryPath),
               "argparse": SimpleNamespace(ArgumentParser=lambda: parser)}
    def importing(name, *args, **kwargs):
        return modules[name] if name in modules else real_import(name, *args, **kwargs)
    namespace = {"__name__": "__main__", "__file__": "/synthetic/product/tools/make_fixtures.py",
                 "__builtins__": dict(vars(builtins), __import__=importing)}
    path = ROOT / "tools/make_fixtures.py"
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), namespace)
    except SystemExit as exc:
        return exc.code
    raise AssertionError("Generator entrypoint did not terminate")


def deployment():
    namespace = {"json": json, "hashlib": hashlib, "Path": Path,
                 "SCRIPTS": ROOT / PREFIX}
    definitions(PREFIX + "boxbinder/drivers.py",
                {"sha256_text", "RemoteError", "HostDriver", "FakeHostDriver"}, namespace)
    configuration = {"re": re, "shlex": shlex, "posixpath": posixpath, "AUTH_MODES": ("jwt", "ccg-native", "ccg-mint", "oauth-broker")}
    definitions(PREFIX + "boxbinder/config.py", None, configuration)
    namespace.update({name: configuration[name] for name in ("ConfigError", "validate_config_dir", "validate_host")})
    remote = {"json": json, "Path": Path, "SCRIPTS": ROOT / PREFIX,
              "sha256_text": namespace["sha256_text"]}
    definitions(PREFIX + "boxbinder/remote.py", None, remote)
    namespace["remote"] = SimpleNamespace(**remote)
    definitions(PREFIX + "boxbinder/deploy.py", {"_converge_file", "_service_state", "deploy_host"}, namespace)
    return namespace


def health_command(observed_roles):
    case = fixture()
    hosts = case["inventory"]["hosts"]
    selected = [dict(hosts[min(index, 1)], host="node%d.example.com" % (index + 1))
                for index in range(len(observed_roles))]
    health = {"CONSISTENCY_FIELDS": ("auth_mode", "root_folder_id", "box_sub_type",
                                     "remote_name", "rclone_version")}
    definitions(PREFIX + "boxbinder/health.py",
                {"runtime_settings", "has_refresh_token", "consistency", "refresh_token_invariant"}, health)
    observations = {}
    for host, role in zip(selected, observed_roles):
        observations[host["host"]] = (
            None if role is None else health["runtime_settings"](dict(host, broker_role=role)))
    namespace = {"_default_factory": None, "_select_hosts": lambda cfg, names: selected,
                 "_now": lambda: "synthetic-time", "healthmod": SimpleNamespace(**health),
                 "refreshmod": SimpleNamespace(
                     invoke_runtime=lambda driver, host, action, **kwargs:
                         {"host": host["host"], "observed_settings": observations[host["host"]]},
                     safe_error=lambda exc: type(exc).__name__),
                 "EXIT_OK": 0, "EXIT_PARTIAL": 1, "EXIT_ALL_FAILED": 2,
                 "EXIT_UNREACHABLE": 3, "EXIT_HEAL_FAILED": 4}
    definitions(PREFIX + "box_binder.py", {"cmd_healthcheck"}, namespace)
    return namespace["cmd_healthcheck"](None, SimpleNamespace(host=[], dry_run=False, timeout=1),
                                         factory=lambda *args, **kwargs: None)


class Box5AuthorTests(unittest.TestCase):
    def test_generator_cli_reproduces_every_declared_fixture_and_detects_corruption(self):
        declared = json.loads((ROOT / ".dataclass.json").read_bytes())["fixture"]
        storage = {}
        self.assertEqual(generator_control(storage), 0)
        self.assertEqual(set(storage), {"/synthetic/out/" + Path(rel).name for rel in declared})
        for rel in declared:
            self.assertEqual(storage["/synthetic/out/" + Path(rel).name], (ROOT / rel).read_bytes())
        self.assertEqual(generator_control(storage, check=True), 0)
        for filename in ("test_review_v4.py", "test_review_v5.py"):
            with self.subTest(fixture=filename):
                path = "/synthetic/out/" + filename
                original = storage[path]
                storage[path] = b"synthetic corrupt fixture"
                self.assertNotEqual(generator_control(storage, check=True), 0)
                storage[path] = original

    def test_actual_dependency_fixture_gate_uses_complete_cli_output(self):
        declared = json.loads((ROOT / ".dataclass.json").read_bytes())["fixture"]
        storage = {"/synthetic/product/" + rel: (ROOT / rel).read_bytes() for rel in declared}
        storage["/synthetic/product/tools/make_fixtures.py"] = b"present"
        class Temp:
            def __enter__(self):
                return "/synthetic/out"
            def __exit__(self, *args):
                pass
        namespace = {"os": SimpleNamespace(path=SimpleNamespace(
            join=posixpath.join, basename=posixpath.basename, isfile=lambda path: path in storage)),
            "tempfile": SimpleNamespace(TemporaryDirectory=Temp),
            "sys": SimpleNamespace(executable="synthetic-python"),
            "subprocess": SimpleNamespace(run=lambda *args, **kwargs:
                SimpleNamespace(returncode=generator_control(storage), stderr="")),
            "open": lambda path, mode: io.BytesIO(storage[path])}
        definitions("guards/tools/data_boundary.py", {"check_fixtures_are_generated"}, namespace)
        findings = []
        namespace["check_fixtures_are_generated"]("/synthetic/product", {"fixture": declared}, findings)
        self.assertEqual(findings, [])
        storage["/synthetic/product/" + declared[-1]] = b"synthetic corrupt fixture"
        findings = []
        namespace["check_fixtures_are_generated"]("/synthetic/product", {"fixture": declared}, findings)
        self.assertTrue(any(row[0] == "HAND-EDITED" for row in findings))

    def test_valid_defaulted_and_explicit_roles_deploy_idempotently(self):
        case, namespace = fixture(), deployment()
        for kind, host in case["valid_hosts"]:
            with self.subTest(kind=kind):
                namespace["validate_host"](host)
                driver = namespace["FakeHostDriver"](host)
                credentials = case["credentials"] if host.get("broker_role") == "master" else None
                first = namespace["deploy_host"](driver, host, credentials=credentials)
                second = namespace["deploy_host"](driver, host, credentials=credentials)
                self.assertEqual(first["status"], "configured")
                self.assertEqual(second["status"], "configured")
                self.assertEqual(second["mutations"], 0)
                stored = json.loads(driver.fs[host["config_dir"] + "/host.json"])
                self.assertEqual(stored["broker_role"], host.get("broker_role", "slave"))

    def test_legacy_default_slave_is_valid_but_malformed_prior_state_never_mutates(self):
        case, namespace = fixture(), deployment()
        host = case["implicit_slave"]
        path = host["config_dir"] + "/host.json"
        with self.subTest(case="legacy-default"):
            driver = namespace["FakeHostDriver"](host, fs={path: json.dumps(host)})
            self.assertEqual(namespace["deploy_host"](driver, host)["status"], "configured")
            self.assertEqual(namespace["deploy_host"](driver, host)["mutations"], 0)
        for name, previous in case["invalid_prior"]:
            with self.subTest(case=name):
                driver = namespace["FakeHostDriver"](host, fs={path: json.dumps(previous)})
                before = dict(driver.fs)
                with self.assertRaises(namespace["RemoteError"]):
                    namespace["deploy_host"](driver, host)
                self.assertEqual(driver.mutations, 0)
                self.assertEqual(driver.exec_log, [])
                self.assertEqual(driver.fs, before)

    def test_role_changes_still_refuse_before_prerequisites_and_mutations(self):
        case, namespace = fixture(), deployment()
        for previous, requested in ((case["master"], case["implicit_slave"]),
                                    (case["implicit_slave"], case["master"])):
            with self.subTest(previous=previous.get("broker_role", "slave")):
                path = requested["config_dir"] + "/host.json"
                driver = namespace["FakeHostDriver"](requested, fs={path: json.dumps(previous)})
                with self.assertRaises(namespace["RemoteError"]):
                    namespace["deploy_host"](driver, requested, credentials=case["credentials"])
                self.assertEqual(driver.mutations, 0)
                self.assertEqual(driver.exec_log, [])

    def test_health_exit_reflects_proven_violation_without_conflating_unknown(self):
        for roles, expected, success in fixture()["health_cases"]:
            with self.subTest(roles=roles):
                code, report = health_command(roles)
                self.assertIs(report["refresh_token_invariant"]["ok"], expected)
                self.assertEqual(code == 0, success)
                self.assertEqual(report["exit_code"], code)

"""box-binder acceptance signals (1-10 from ARCHITECTURE §7).

Hermetic: no real Box credentials, no real rclone/ssh. External commands go through an
injectable FakeHostDriver; token endpoints go through a local fake HTTP server. Every signal
is machine-adjudicable so self-evolve can gate regressions.
"""
import http.server
import json
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CONFIG = os.path.abspath(os.path.join(HERE, "..", "config", "machines.example.yaml"))
sys.path.insert(0, os.path.abspath(SCRIPTS))

from boxbinder import config as cfgmod          # noqa: E402
from boxbinder import deploy as deploymod        # noqa: E402
from boxbinder import health as healthmod        # noqa: E402
from boxbinder import refresh as refreshmod      # noqa: E402
from boxbinder import remote as remotemod        # noqa: E402
from boxbinder import alerts as alertsmod        # noqa: E402
from boxbinder import atomic as atomicmod        # noqa: E402
from boxbinder.drivers import FakeHostDriver     # noqa: E402
import box_binder as cli                          # noqa: E402


# ---- fake Box token endpoint --------------------------------------------------------------

class _TokenHandler(http.server.BaseHTTPRequestHandler):
    script = []  # list of (status, json) responses, popped per request

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        status, payload = self.server.script.pop(0)
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_token_server(script):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _TokenHandler)
    srv.script = list(script)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/token" % srv.server_address[1]


def host(**over):
    h = {"host": "h1", "auth_mode": "jwt", "remote_name": "box", "root_folder_id": "0",
         "box_sub_type": "enterprise", "config_dir": "/etc/box-binder"}
    h.update(over)
    return h


# ---- S1 refresh logic ---------------------------------------------------------------------

class S1Refresh(unittest.TestCase):
    def test_ccg_mint_body_and_atfile_injection(self):
        fields = refreshmod.build_mint_fields("cid", "csecret", "ent123")
        self.assertEqual(fields["grant_type"], "client_credentials")
        self.assertEqual(fields["box_subject_type"], "enterprise")
        self.assertEqual(fields["box_subject_id"], "ent123")
        self.assertIn("client_id", fields)
        self.assertIn("client_secret", fields)

        srv, url = start_token_server([(200, {"access_token": "AT-XYZ", "expires_in": 3600})])
        try:
            tf = os.path.join(self.tmp, "access.json")
            res = refreshmod.mint_access_token(url, fields, tf)
        finally:
            srv.shutdown()
        self.assertTrue(res["ok"])
        # injection is BY REFERENCE (@file), token value never on argv
        self.assertIn("token=@%s" % tf, res["inject"])
        self.assertFalse(any("AT-XYZ" in a for a in res["inject"]))
        self.assertTrue(os.path.exists(tf))

    def test_ccg_mint_timer_under_60(self):
        self.assertIn("0/45", remotemod.render_mint_timer(host(mint_interval_min=45)))
        with self.assertRaises(ValueError):
            remotemod.render_mint_timer(host(mint_interval_min=60))

    def test_broker_persist_before_distribute_and_strip(self):
        state = os.path.join(self.tmp, "state.json")
        with open(state, "w") as f:
            json.dump({"refresh_token": "RT-OLD"}, f)
        lock = os.path.join(self.tmp, "broker.lock")
        srv, url = start_token_server([(200, {"access_token": "AT", "refresh_token": "RT-NEW",
                                              "expires_in": 3600})])
        try:
            out = refreshmod.broker_refresh(state, url, lock, ["s1", "s2"], "cid", "csec")
        finally:
            srv.shutdown()
        ev = out["events"]
        self.assertLess(ev.index("refresh_token_persisted"),
                        min(i for i, e in enumerate(ev) if e.startswith("slave_blob_rendered")))
        for blob in out["slave_blobs"].values():
            self.assertNotIn("refresh_token", blob)        # slave structurally cannot rotate
            self.assertIn("access_token", blob)
        with open(state) as f:
            self.assertEqual(json.load(f)["refresh_token"], "RT-NEW")  # rotated + persisted

    def test_broker_flock_blocks_concurrency(self):
        lp = os.path.join(self.tmp, "x.lock")
        a = refreshmod.FileLock(lp).acquire()
        try:
            with self.assertRaises(refreshmod.Locked):
                refreshmod.FileLock(lp).acquire()
        finally:
            a.release()

    def test_broker_invalid_grant_is_nonretryable(self):
        state = os.path.join(self.tmp, "s.json")
        with open(state, "w") as f:
            json.dump({"refresh_token": "RT"}, f)
        srv, url = start_token_server([(400, {"error": "invalid_grant"})])
        try:
            with self.assertRaises(refreshmod.NonRetryable):
                refreshmod.broker_refresh(state, url, os.path.join(self.tmp, "l.lock"),
                                          ["s1"], "cid", "csec")
        finally:
            srv.shutdown()

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()


# ---- S2 idempotency -----------------------------------------------------------------------

class S2Idempotency(unittest.TestCase):
    def test_second_deploy_is_noop(self):
        fs = {}
        d1 = FakeHostDriver(host(), fs=fs)
        r1 = deploymod.deploy_host(d1, host(), dry_run=False)
        self.assertGreater(len(r1["changed"]), 0)
        d2 = FakeHostDriver(host(), fs=d1.fs)         # same backing store after run 1
        r2 = deploymod.deploy_host(d2, host(), dry_run=False)
        self.assertEqual(r2["changed"], [])
        self.assertEqual(d2.mutations, 0)
        # rendered bytes are deterministic
        self.assertEqual(remotemod.desired_artifacts(host()),
                         remotemod.desired_artifacts(host()))


# ---- S3 multi-host consistency ------------------------------------------------------------

class S3Consistency(unittest.TestCase):
    def test_divergence_detected_and_no_refresh_token(self):
        reps = [
            {"host": "a", "auth_mode": "jwt", "root_folder_id": "0", "box_sub_type": "enterprise",
             "remote_name": "box", "rclone_version": "1.74.0", "has_refresh_token": False},
            {"host": "b", "auth_mode": "jwt", "root_folder_id": "99", "box_sub_type": "enterprise",
             "remote_name": "box", "rclone_version": "1.70.0", "has_refresh_token": False},
        ]
        cons = healthmod.consistency(reps)
        self.assertFalse(cons["consistent"])
        fields = {d["field"] for d in cons["divergences"]}
        self.assertEqual(fields, {"root_folder_id", "rclone_version"})
        inv = healthmod.refresh_token_invariant(reps)
        self.assertTrue(inv["ok"])
        self.assertEqual(inv["holders"], [])

    def test_broker_exactly_one_holder(self):
        reps = [{"host": "m", "auth_mode": "oauth-broker", "has_refresh_token": True},
                {"host": "s", "auth_mode": "oauth-broker", "has_refresh_token": False}]
        inv = healthmod.refresh_token_invariant(reps)
        self.assertTrue(inv["ok"])
        self.assertEqual(inv["holders"], ["m"])

    def test_jwt_host_never_holds_refresh_token(self):
        self.assertFalse(healthmod.has_refresh_token(host(auth_mode="jwt")))
        self.assertFalse(healthmod.has_refresh_token(host(auth_mode="ccg-mint")))
        self.assertTrue(healthmod.has_refresh_token(host(auth_mode="oauth-broker"), role="master"))


# ---- S4 config validation -----------------------------------------------------------------

class S4Config(unittest.TestCase):
    def test_example_loads_and_refs_are_pointers(self):
        cfg = cfgmod.load(CONFIG)
        refs = cfgmod.resolve_refs(cfg)
        self.assertIn("jwt_config_ref", refs)
        # presence is computed without reading any value
        for v in refs.values():
            self.assertIn("present", v)

    def test_inline_secret_rejected(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "bad.yaml")
        with open(p, "w") as f:
            f.write("version: 1\ndefaults:\n  auth_mode: jwt\n  remote_name: box\n"
                    "secrets:\n  source: env\n  client_secret: sk_live_abcdef0123456789ABCDEF\n"
                    "hosts:\n  - host: h1\n")
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load(p)

    def test_bad_auth_mode_rejected(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "bad2.yaml")
        with open(p, "w") as f:
            f.write("version: 1\ndefaults:\n  auth_mode: telepathy\n  remote_name: box\n"
                    "hosts:\n  - host: h1\n")
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.load(p)


# ---- S5 dry-run no side effects -----------------------------------------------------------

class S5DryRun(unittest.TestCase):
    def test_deploy_dry_run_zero_mutations(self):
        r = deploymod.deploy_host(None, host(), dry_run=True)
        self.assertEqual(r["mutations"], 0)
        self.assertGreater(len(r["would_write"]), 0)
        self.assertEqual(r["fingerprint"], remotemod.artifact_fingerprint(host()))

    def test_cli_deploy_dry_run_no_driver(self):
        def boom(h, dry_run):
            raise AssertionError("driver must not be built in dry-run")
        code = cli.run(["deploy", "-c", CONFIG, "--dry-run", "--json"], factory=boom)
        self.assertEqual(code, 0)


# ---- S6 secret hygiene --------------------------------------------------------------------

class S6Hygiene(unittest.TestCase):
    def test_no_secret_values_in_repo(self):
        hits = []
        for dirpath, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
            for fn in files:
                if fn.endswith((".pyc",)):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    text = open(fp, "r", encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if cfgmod._PEM_RE.search(text) or cfgmod._JWT_RE.search(text):
                    hits.append(fp)
                for ln, reason in cfgmod._scan_inline_secrets(text):
                    hits.append("%s:%d %s" % (fp, ln, reason))
        self.assertEqual(hits, [], "secret-like material found: %s" % hits)

    def test_gitignore_covers_secret_globs(self):
        gi = open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8").read()
        for need in ("*.conf", "config.json", "*.env", "secrets.env", "*.pem", "*.key",
                     "credentials*", "*.token"):
            self.assertIn(need, gi, "gitignore missing %s" % need)


# ---- S7 probe error classification --------------------------------------------------------

class S7Classify(unittest.TestCase):
    def test_routing(self):
        cases = {
            "Invalid refresh token": "auth",
            "HTTP 401 Unauthorized": "auth",
            "token expired and there is no refresh token": "auth",
            "Error 429 rate limit exceeded": "ratelimit",
            "dial tcp: i/o timeout": "network",
            "connection refused": "network",
            "weird unmapped explosion": "unknown",
        }
        for stderr, expect in cases.items():
            self.assertEqual(healthmod.classify(stderr, returncode=1), expect, stderr)
        self.assertEqual(healthmod.classify("", 0), "ok")
        self.assertEqual(healthmod.ACTION["auth"], "heal")
        self.assertEqual(healthmod.ACTION["ratelimit"], "retry")
        self.assertEqual(healthmod.ACTION["network"], "retry")
        self.assertEqual(healthmod.ACTION["unknown"], "fail")


# ---- S8 anti-pattern guard ----------------------------------------------------------------

class S8AntiPattern(unittest.TestCase):
    def _cfg(self, n, mode="jwt"):
        data = {"version": 1, "defaults": {"auth_mode": mode, "remote_name": "box"},
                "hosts": [{"host": "h%d" % i} for i in range(n)]}
        return cfgmod.Config(data)

    def test_shared_refresh_token_multi_host_rejected(self):
        conf = "[box]\ntype = box\nrefresh_token = RT-SECRET\n"
        with self.assertRaises(cfgmod.AntiPatternError):
            cfgmod.assert_no_shared_refresh_token(self._cfg(2, "jwt"), conf)

    def test_single_host_ok(self):
        conf = "[box]\nrefresh_token = RT\n"
        cfgmod.assert_no_shared_refresh_token(self._cfg(1, "jwt"), conf)  # no raise

    def test_server_auth_no_conf_ok(self):
        cfgmod.assert_no_shared_refresh_token(self._cfg(5, "jwt"), "")    # no refresh_token


# ---- S9 atomic write ----------------------------------------------------------------------

class S9Atomic(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_write_roundtrip(self):
        p = os.path.join(self.tmp, "sub", "f.txt")
        atomicmod.atomic_write(p, "hello", mode=0o600)
        self.assertEqual(open(p).read(), "hello")

    def test_cross_volume_rejected(self):
        orig = atomicmod._same_volume
        atomicmod._same_volume = lambda a, b: False
        try:
            with self.assertRaises(atomicmod.CrossVolumeError):
                atomicmod.assert_same_volume(os.path.join(self.tmp, "t"),
                                             os.path.join(self.tmp, "d"))
        finally:
            atomicmod._same_volume = orig

    def test_no_half_file_on_failed_replace(self):
        p = os.path.join(self.tmp, "d.txt")
        atomicmod.atomic_write(p, "OLD")
        orig = os.replace
        os.replace = lambda a, b: (_ for _ in ()).throw(OSError("boom"))
        try:
            with self.assertRaises(OSError):
                atomicmod.atomic_write(p, "NEW")
        finally:
            os.replace = orig
        self.assertEqual(open(p).read(), "OLD")     # destination untouched
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith(".bbtmp.")]
        self.assertEqual(leftovers, [])             # no half-written temp left behind


# ---- S10 CLI contract ---------------------------------------------------------------------

class S10CLI(unittest.TestCase):
    def test_verify_config_exit_0(self):
        self.assertEqual(cli.run(["verify-config", "-c", CONFIG, "--json"]), 0)

    def test_bad_config_exit_3(self):
        self.assertEqual(cli.run(["verify-config", "-c", os.path.join(HERE, "nope.yaml")]),
                         cli.EXIT_CONFIG)

    def test_healthcheck_json_and_exit(self):
        def fac(h, dry_run):
            return FakeHostDriver(h, responses={"rclone lsd": (0, "  -1 dir\n", "")})
        code = cli.run(["healthcheck", "-c", CONFIG], factory=fac)
        self.assertEqual(code, 0)

    def test_alerts_scrub(self):
        msg = alertsmod.scrub("token=AT-supersecret refresh_token=RT-zzz")
        self.assertNotIn("AT-supersecret", msg)
        self.assertNotIn("RT-zzz", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Host drivers: how box-binder reads/writes/execs on a target host.

`HostDriver` is the seam. `SSHHostDriver` is the real implementation (non-interactive ssh,
atomic remote writes, content over stdin so secrets never hit argv). `FakeHostDriver` is an
in-memory simulation used by the test-suite so every behaviour is verifiable without real
infra. `dry_run` drivers refuse mutating ops and count them (must stay 0 under --dry-run).
"""
from __future__ import annotations

import hashlib
import shlex
import subprocess


def sha256_text(s) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


class HostDriver:
    def __init__(self, host: dict, dry_run: bool = False):
        self.host = host
        self.name = host.get("host")
        self.dry_run = dry_run
        self.mutations = 0          # count of state-changing ops actually performed
        self.planned = []           # human-readable plan (what would change)

    # read-only ------------------------------------------------------------------
    def read_text(self, path):
        raise NotImplementedError

    def exists(self, path) -> bool:
        return self.read_text(path) is not None

    def sha256(self, path):
        t = self.read_text(path)
        return None if t is None else sha256_text(t)

    def exec(self, argv, mutate=False, input_text=None, timeout=None):
        raise NotImplementedError

    # write ----------------------------------------------------------------------
    def write_text(self, path, content, mode=0o600):
        if self.dry_run:
            self.planned.append(("write", path))
            return False
        self._write_impl(path, content, mode)
        self.mutations += 1
        self.planned.append(("write", path))
        return True

    def _write_impl(self, path, content, mode):
        raise NotImplementedError


class FakeHostDriver(HostDriver):
    """In-memory host. `fs` maps path->content; `responses` maps a command-prefix to (rc,out,err)."""

    def __init__(self, host, fs=None, responses=None, dry_run=False):
        super().__init__(host, dry_run)
        self.fs = dict(fs or {})
        self.responses = dict(responses or {})
        self.exec_log = []

    def read_text(self, path):
        return self.fs.get(path)

    def _write_impl(self, path, content, mode):
        self.fs[path] = content

    def exec(self, argv, mutate=False, input_text=None, timeout=None):
        cmd = " ".join(argv) if isinstance(argv, (list, tuple)) else str(argv)
        self.exec_log.append((cmd, mutate))
        if mutate:
            if self.dry_run:
                self.planned.append(("exec", cmd))
                return (0, "", "")
            self.mutations += 1
        for prefix, resp in self.responses.items():
            if cmd.startswith(prefix):
                return resp
        return (0, "", "")


class SSHHostDriver(HostDriver):
    """Real ssh/scp driver. Non-interactive, timeouts everywhere, content via stdin."""

    def __init__(self, host, dry_run=False):
        super().__init__(host, dry_run)
        self.ssh_target = host.get("ssh") or ("root@%s" % host.get("host"))
        self.ssh_opts = shlex.split(host.get(
            "ssh_opts",
            "-o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new",
        ))

    def _ssh(self, remote_cmd, input_text=None, timeout=60):
        argv = ["ssh"] + self.ssh_opts + [self.ssh_target, remote_cmd]
        p = subprocess.run(argv, input=(input_text.encode() if input_text else None),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return (p.returncode, p.stdout.decode("utf-8", "replace"),
                p.stderr.decode("utf-8", "replace"))

    def read_text(self, path):
        rc, out, _ = self._ssh("cat -- %s 2>/dev/null" % shlex.quote(path))
        return out if rc == 0 else None

    def exec(self, argv, mutate=False, input_text=None, timeout=None):
        cmd = " ".join(shlex.quote(a) for a in argv) if isinstance(argv, (list, tuple)) else str(argv)
        if mutate and self.dry_run:
            self.planned.append(("exec", cmd))
            return (0, "", "")
        rc, out, err = self._ssh(cmd, input_text=input_text, timeout=timeout or 60)
        if mutate:
            self.mutations += 1
        return (rc, out, err)

    def _write_impl(self, path, content, mode):
        # atomic remote write: temp in same dir, content via stdin, then mv.
        q = shlex.quote(path)
        m = "%o" % mode
        remote = (
            'set -e; d=$(dirname %s); t=$(mktemp "$d/.bbtmp.XXXXXX"); '
            'cat > "$t"; chmod %s "$t"; mv -f "$t" %s; sync' % (q, m, q)
        )
        rc, _, err = self._ssh(remote, input_text=content, timeout=60)
        if rc != 0:
            raise RuntimeError("remote atomic write failed for %s: %s" % (path, err.strip()))

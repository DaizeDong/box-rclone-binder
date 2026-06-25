"""Idempotent, declarative deploy: converge each host to its desired artifact set.

Compare desired vs current by sha256; write only what differs (atomic remote write); reload
systemd only when something changed. Running deploy twice on an already-correct host yields
zero changes and zero mutations. `--dry-run` produces a deterministic plan and contacts no host
(no ssh, no writes).
"""
from __future__ import annotations

from .drivers import sha256_text
from . import remote


def deploy_host(driver, host: dict, dry_run: bool = False) -> dict:
    arts = remote.desired_artifacts(host)
    if dry_run:
        return {
            "host": host.get("host"),
            "dry_run": True,
            "would_write": sorted(arts.keys()),
            "changed": [],
            "mutations": 0,
            "fingerprint": remote.artifact_fingerprint(host),
        }

    changed = []
    for path in sorted(arts):
        content = arts[path]
        want = sha256_text(content)
        cur = driver.sha256(path)
        if cur != want:
            driver.write_text(path, content, 0o600)
            changed.append(path)

    if changed:
        # converge the unit set, then reload (only when something actually changed)
        driver.exec(["systemctl", "daemon-reload"], mutate=True)
        driver.exec(["systemctl", "enable", "--now", "box-binder-health.timer"], mutate=True)
        if host.get("auth_mode") == "ccg-mint":
            driver.exec(["systemctl", "enable", "--now", "box-binder-mint.timer"], mutate=True)

    return {
        "host": host.get("host"),
        "dry_run": False,
        "changed": changed,
        "mutations": driver.mutations,
        "fingerprint": remote.artifact_fingerprint(host),
    }

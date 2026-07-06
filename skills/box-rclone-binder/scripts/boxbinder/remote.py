"""Render the desired on-host artifacts (the declarative target state).

NONE of these contain a secret value. The rclone remote is configured fileless via env vars
(systemd EnvironmentFile); the actual secret material (config.json for JWT, client_secret for
CCG) is injected at runtime from the secret backend and lives only in chmod-600 files the
deploy step installs out-of-band — never rendered here, never in the repo.
"""
from __future__ import annotations

from .drivers import sha256_text


def _envline(k, v):
    return "%s=%s" % (k, v)


def render_secrets_env(host: dict) -> str:
    """The non-secret rclone env-var remote definition written to /etc/box-binder/secrets.env.

    Secret-bearing vars (CLIENT_SECRET, the JWT config.json path content) are NOT written here;
    they are supplied by the systemd unit's secondary EnvironmentFile injected from the secret
    backend at runtime. This file is safe-by-construction (no values).
    """
    am = host.get("auth_mode", "jwt")
    remote = host.get("remote_name", "box")
    rid = host.get("root_folder_id", "0")
    sub = host.get("box_sub_type", "enterprise")
    cfg_dir = host.get("config_dir", "/etc/box-binder")
    P = "RCLONE_CONFIG_%s_" % remote.upper()
    lines = ["# Managed by box-binder. Do not edit. Contains NO secret values.",
             _envline(P + "TYPE", "box"),
             _envline(P + "ROOT_FOLDER_ID", rid)]
    if am in ("jwt",):
        lines += [_envline(P + "BOX_SUB_TYPE", sub),
                  _envline(P + "BOX_CONFIG_FILE", "%s/config.json" % cfg_dir)]
    elif am in ("ccg-native",):
        lines += [_envline(P + "CLIENT_CREDENTIALS", "true"),
                  _envline(P + "BOX_SUB_TYPE", sub),
                  _envline(P + "BOX_SUBJECT_ID", "${BOX_BINDER_ENTERPRISE_ID}"),
                  _envline(P + "CLIENT_ID", "${BOX_BINDER_CLIENT_ID}"),
                  _envline(P + "CLIENT_SECRET", "${BOX_BINDER_CLIENT_SECRET}")]
    elif am in ("ccg-mint",):
        # static access token reminted by the timer; injected via the runtime secret file.
        lines += [_envline(P + "ACCESS_TOKEN", "${BOX_BINDER_ACCESS_TOKEN}")]
    elif am in ("oauth-broker",):
        # token blob path (access-only on slaves); see refresh.py broker flow.
        lines += [_envline(P + "TOKEN", "${BOX_BINDER_TOKEN_JSON}")]
    imp = host.get("impersonate_user_id")
    if imp:
        lines.append(_envline(P + "IMPERSONATE", imp))
    return "\n".join(lines) + "\n"


def render_health_service(host: dict) -> str:
    cfg_dir = host.get("config_dir", "/etc/box-binder")
    return (
        "[Unit]\n"
        "Description=box-binder health check (read-only probe + self-heal)\n"
        "After=network-online.target\nWants=network-online.target\n\n"
        "[Service]\nType=oneshot\n"
        "EnvironmentFile=%s/secrets.env\n"
        "EnvironmentFile=-%s/runtime.env\n"
        "ExecStart=/usr/local/bin/box-binder-health\n"
        "Nice=10\n" % (cfg_dir, cfg_dir)
    )


def render_health_timer(host: dict) -> str:
    cal = host.get("health_interval", "*:0/15")
    return (
        "[Unit]\nDescription=box-binder health timer\n\n"
        "[Timer]\nOnCalendar=%s\nPersistent=true\nRandomizedDelaySec=30\n\n"
        "[Install]\nWantedBy=timers.target\n" % cal
    )


def render_mint_timer(host: dict) -> str:
    mins = int(host.get("mint_interval_min", 45))
    if mins >= 60:
        raise ValueError("mint_interval_min must be < 60 (token lives 60m); got %d" % mins)
    return (
        "[Unit]\nDescription=box-binder ccg token re-mint\n\n"
        "[Timer]\nOnCalendar=*:0/%d\nPersistent=true\nRandomizedDelaySec=30\n\n"
        "[Install]\nWantedBy=timers.target\n" % mins
    )


def desired_artifacts(host: dict) -> dict:
    """Map of {remote_path: content} that deploy will converge the host to (systemd path)."""
    cfg_dir = host.get("config_dir", "/etc/box-binder")
    am = host.get("auth_mode", "jwt")
    arts = {
        "%s/secrets.env" % cfg_dir: render_secrets_env(host),
        "/etc/systemd/system/box-binder-health.service": render_health_service(host),
        "/etc/systemd/system/box-binder-health.timer": render_health_timer(host),
    }
    if am == "ccg-mint":
        arts["/etc/systemd/system/box-binder-mint.timer"] = render_mint_timer(host)
    return arts


def artifact_fingerprint(host: dict) -> str:
    arts = desired_artifacts(host)
    joined = "\n".join("%s::%s" % (k, sha256_text(v)) for k, v in sorted(arts.items()))
    return sha256_text(joined)

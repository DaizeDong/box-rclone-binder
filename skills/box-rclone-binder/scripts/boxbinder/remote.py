"""Complete deterministic runtime manifest, containing no credential values."""
from __future__ import annotations

import json
from pathlib import Path

from .drivers import sha256_text

SCRIPTS = Path(__file__).resolve().parents[1]


def host_settings(host):
    keys = ('host', 'auth_mode', 'remote_name', 'root_folder_id', 'box_sub_type',
            'config_dir', 'impersonate_user_id', 'broker_role')
    settings = {key: host[key] for key in keys if key in host}
    settings.setdefault('auth_mode', 'jwt')
    settings.setdefault('config_dir', '/etc/box-binder')
    settings.setdefault('remote_name', 'box')
    if settings['auth_mode'] == 'oauth-broker':
        settings.setdefault('broker_role', 'slave')
    return settings


def render_secrets_env(host):
    """Compatibility name for non-secret runtime location metadata."""
    return 'BOX_BINDER_HOST_FILE=%s/host.json\n' % host.get('config_dir', '/etc/box-binder')


def render_health_service(host):
    return render_service(host, 'health')


def render_service(host, action):
    cfg = host.get('config_dir', '/etc/box-binder')
    return ('[Unit]\nDescription=Box binder %s\nAfter=network-online.target\n'
            'Wants=network-online.target\n\n[Service]\nType=oneshot\n'
            'EnvironmentFile=%s/secrets.env\nUMask=0077\n'
            'ExecStart=/usr/local/bin/box-binder-%s\n' % (action, cfg, action))


def render_health_timer(host):
    return _timer('health', host.get('health_interval', '*:0/15'))


def _timer(action, calendar):
    return ('[Unit]\nDescription=Box binder %s timer\n\n[Timer]\n'
            'OnCalendar=%s\nPersistent=true\nRandomizedDelaySec=30\n'
            'Unit=box-binder-%s.service\n\n[Install]\nWantedBy=timers.target\n'
            % (action, calendar, action))


def render_mint_timer(host):
    minutes = int(host.get('mint_interval_min', 45))
    if not 1 <= minutes < 60:
        raise ValueError('mint_interval_min must be between 1 and 59')
    return _timer('mint', '*:0/%d' % minutes)


def runtime_manifest(host):
    cfg = host.get('config_dir', '/etc/box-binder')
    artifacts = {}
    def add(path, content, mode=0o600):
        artifacts[path] = {'content': content, 'mode': mode}
    add(cfg + '/host.json', json.dumps(host_settings(host), sort_keys=True, indent=2) + '\n')
    add('/opt/box-binder/deployment.json', json.dumps(host_settings(host), sort_keys=True, indent=2) + '\n')
    add(cfg + '/secrets.env', render_secrets_env(host))
    add('/opt/box-binder/box_runtime.py', (SCRIPTS / 'box_runtime.py').read_text(encoding='utf-8'), 0o644)
    for source in sorted((SCRIPTS / 'boxbinder').glob('*.py')):
        add('/opt/box-binder/boxbinder/' + source.name, source.read_text(encoding='utf-8'), 0o644)
    add('/opt/box-binder/install/mint.sh', (SCRIPTS / 'install/mint.sh').read_text(encoding='utf-8'), 0o755)
    broker_master = host.get('auth_mode') == 'oauth-broker' and host.get('broker_role') == 'master'
    actions = ['health'] + (['mint'] if host.get('auth_mode') == 'ccg-mint' else [])
    if broker_master:
        actions.append('broker')
        add(cfg + '/peers.json', json.dumps({'hosts': host.get('broker_peers', [])}, sort_keys=True) + '\n')
    for action in actions:
        add('/usr/local/bin/box-binder-' + action,
            (SCRIPTS / 'install' / ('box-binder-' + action)).read_text(encoding='utf-8'), 0o755)
        add('/etc/systemd/system/box-binder-%s.service' % action, render_service(host, action), 0o644)
    add('/etc/systemd/system/box-binder-health.timer', render_health_timer(host), 0o644)
    if host.get('auth_mode') == 'ccg-mint':
        add('/etc/systemd/system/box-binder-mint.timer', render_mint_timer(host), 0o644)
    if broker_master:
        add('/etc/systemd/system/box-binder-broker.timer',
            _timer('broker', '*:0/%d' % int(host.get('mint_interval_min', 45))), 0o644)
    return artifacts


def desired_artifacts(host):
    return {path: entry['content'] for path, entry in runtime_manifest(host).items()}


def artifact_fingerprint(host):
    return sha256_text(json.dumps(runtime_manifest(host), sort_keys=True))

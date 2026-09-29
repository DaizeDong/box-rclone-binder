"""Converge runtime bytes, modes and service state; verify every mutation."""
from __future__ import annotations

import json

from .drivers import RemoteError
from . import remote


def _converge_file(driver, path, content, mode):
    current = driver.read_text(path)
    changed = current != content or driver.stat(path) != {'kind': 'file', 'mode': mode}
    if changed:
        driver.write_text(path, content, mode)
    if driver.read_text(path) != content or driver.stat(path) != {'kind': 'file', 'mode': mode}:
        raise RemoteError('file readback', 'content or mode mismatch')
    return changed


def _service_state(driver, unit, action):
    result = driver.exec(['systemctl', action, unit], timeout=30)
    valid = {'is-enabled': {(0, 'enabled'), (1, 'disabled')},
             'is-active': {(0, 'active'), (3, 'inactive')}}[action]
    if not isinstance(result, tuple) or len(result) != 3 or (result[0], str(result[1]).strip()) not in valid:
        raise RemoteError('systemctl ' + action, 'unrecognized state or command failure')
    return result[1].strip()


def deploy_host(driver, host, dry_run=False, credentials=None):
    manifest = remote.runtime_manifest(host)
    cfg = host.get('config_dir', '/etc/box-binder')
    directories = {cfg: 0o700, '/opt/box-binder': 0o755, '/opt/box-binder/boxbinder': 0o755,
                   '/opt/box-binder/install': 0o755, '/usr/local/bin': 0o755,
                   '/etc/systemd/system': 0o755}
    mode = host.get('auth_mode', 'jwt')
    secret_paths = [] if mode == 'oauth-broker' and host.get('broker_role') != 'master' else [
        cfg + ('/config.json' if mode == 'jwt' else '/runtime.json')]
    if mode == 'oauth-broker' and host.get('broker_role') == 'master':
        secret_paths.append(cfg + '/broker-state.json')
    fingerprint = remote.artifact_fingerprint(host)
    if dry_run:
        return {'host': host['host'], 'dry_run': True, 'status': 'planned', 'would_write': sorted(manifest),
                'directories': directories, 'required_secret_files': secret_paths,
                'changed': [], 'mutations': 0, 'fingerprint': fingerprint}
    changed = []
    initial_mutations = driver.mutations
    for command in (['python3', '--version'], ['rclone', 'version']):
        driver.checked_exec(command, operation='runtime prerequisite ' + command[0], timeout=30)
    if credentials is not None:
        if mode == 'jwt':
            manifest[cfg + '/config.json'] = {'content': credentials['jwt_config'], 'mode': 0o600}
        elif mode != 'oauth-broker' or host.get('broker_role') == 'master':
            values = {key: value for key, value in credentials.items() if key != 'broker_state'}
            manifest[cfg + '/runtime.json'] = {'content': json.dumps(values, sort_keys=True), 'mode': 0o600}
        if 'broker_state' in credentials and driver.read_text(cfg + '/broker-state.json') is None:
            manifest[cfg + '/broker-state.json'] = {'content': credentials['broker_state'], 'mode': 0o600}
    for path in secret_paths:
        if path not in manifest and not driver.read_text(path):
            raise RemoteError('required credentials', 'missing protected file')
    for path, file_mode in directories.items():
        if driver.ensure_directory(path, file_mode):
            changed.append(path)
    for path, entry in sorted(manifest.items()):
        if _converge_file(driver, path, entry['content'], entry['mode']):
            changed.append(path)
    for path in secret_paths:
        if driver.stat(path) != {'kind': 'file', 'mode': 0o600}:
            raise RemoteError('credential readback', 'unsafe mode')
    # Advance only after reload succeeds; retry reload after a partial file write.
    marker = cfg + '/loaded-runtime.sha256'
    if driver.read_text(marker) != fingerprint:
        driver.checked_exec(['systemctl', 'daemon-reload'], mutate=True, operation='systemctl daemon-reload')
        _converge_file(driver, marker, fingerprint, 0o600)
    timers = ['box-binder-health.timer'] + (['box-binder-mint.timer'] if mode == 'ccg-mint' else [])
    if mode == 'oauth-broker' and host.get('broker_role') == 'master':
        timers.append('box-binder-broker.timer')
    services = {}
    for timer in timers:
        if _service_state(driver, timer, 'is-enabled') != 'enabled':
            driver.checked_exec(['systemctl', 'enable', timer], mutate=True, operation='systemctl enable')
        if _service_state(driver, timer, 'is-active') != 'active':
            driver.checked_exec(['systemctl', 'start', timer], mutate=True, operation='systemctl start')
        enabled = _service_state(driver, timer, 'is-enabled')
        active = _service_state(driver, timer, 'is-active')
        if enabled != 'enabled' or active != 'active':
            raise RemoteError('timer readback', 'not enabled and active')
        services[timer] = {'enabled': True, 'active': True}
    return {'host': host['host'], 'dry_run': False, 'status': 'configured', 'changed': changed,
            'mutations': driver.mutations - initial_mutations, 'fingerprint': fingerprint, 'services': services}

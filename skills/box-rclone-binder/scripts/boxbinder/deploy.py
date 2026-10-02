"""Converge runtime bytes, modes and service state; verify every mutation."""
from __future__ import annotations

import json

from .drivers import RemoteError
from .config import ConfigError, validate_config_dir, validate_host
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
    validate_host(host)
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
    import posixpath
    import re

    def canonical(path):
        return posixpath.normpath('/' + path.lstrip('/'))

    def state_text(path):
        metadata = driver.stat(path)
        if metadata is not None and metadata.get('kind') != 'file':
            raise RemoteError('prior runtime state', 'expected a regular state file')
        return driver.read_text(path)

    def prior_settings(path, *, required=False, locator=False):
        previous_text = state_text(path)
        if previous_text is None:
            if required:
                raise RemoteError('prior runtime state', 'installed configuration is missing')
            return None
        try:
            previous = json.loads(previous_text)
        except (TypeError, ValueError) as exc:
            raise RemoteError('prior runtime state', 'invalid host.json; review existing deployment before retrying') from exc
        if not isinstance(previous, dict):
            raise RemoteError('prior runtime state', 'host.json must be an object')
        # Persisted runtime settings must establish an auth mode before migration decisions.
        if (not isinstance(previous.get('auth_mode'), str)
                or previous['auth_mode'] not in ('jwt', 'ccg-native', 'ccg-mint', 'oauth-broker')
                or any(not isinstance(previous.get(key), str) or not previous[key].strip()
                       for key in ('host', 'remote_name', 'config_dir'))):
            raise RemoteError('prior runtime state', 'invalid host settings; review existing deployment before retrying')
        prior_path = previous['config_dir']
        prior_host = previous['host']
        try:
            validate_config_dir(prior_path)
        except ConfigError as exc:
            raise RemoteError('prior runtime state', 'invalid config_dir; review existing deployment before retrying') from exc
        if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', previous['remote_name'])
                or prior_host.startswith('-') or prior_host != prior_host.strip()
                or any(character in prior_host for character in '\n\r\0')):
            raise RemoteError('prior runtime state', 'invalid host identity or path; review existing deployment before retrying')
        if previous['auth_mode'] != mode:
            raise RemoteError('auth mode migration',
                              'stop and retire the previous mode timers and state before changing auth mode')
        if previous['auth_mode'] == 'oauth-broker':
            previous_role = previous.get('broker_role', 'slave')
            if not isinstance(previous_role, str) or previous_role not in {'master', 'slave'}:
                raise RemoteError('prior broker role', 'unproven role; review existing state before retrying')
            if mode == 'oauth-broker' and previous_role != host.get('broker_role', 'slave'):
                raise RemoteError(
                    'broker role migration',
                    'stop and disable the old role timer; securely retain or relocate old master '
                    'state before replacing host.json; follow reference/deploy.md and retry')
        if canonical(prior_path) != canonical(cfg):
            raise RemoteError('config directory migration',
                              'installed runtime uses another directory; stop old timers and review migration first')
        if not locator and canonical(posixpath.dirname(path)) != canonical(prior_path):
            raise RemoteError('prior runtime state', 'host settings do not match their installed location')
        return previous

    current_directory = driver.stat(cfg)
    if current_directory is not None and current_directory != {'kind': 'directory', 'mode': 0o700}:
        raise RemoteError('private directory', 'existing destination is not already private')
    prior_settings(cfg + '/host.json')
    installed = prior_settings('/opt/box-binder/deployment.json', locator=True)
    if installed is not None:
        previous = prior_settings(installed['config_dir'] + '/host.json', required=True)
        if previous != installed:
            raise RemoteError('prior runtime state', 'deployment locator disagrees with host settings')

    # Fixed installed units remain discoverable when a request changes config_dir.
    unit_files = {}
    for action in ('health', 'mint', 'broker'):
        service = '/etc/systemd/system/box-binder-' + action + '.service'
        timer = '/etc/systemd/system/box-binder-' + action + '.timer'
        service_text, timer_text = state_text(service), state_text(timer)
        unit_files[service] = service_text
        unit_files[timer] = timer_text
        if service_text is None:
            if timer_text is not None:
                raise RemoteError('prior runtime state', 'installed timer has no proven service')
            continue
        locations = re.findall(r'^EnvironmentFile=(/[^\r\n]+/secrets\.env)$', service_text, re.M)
        if len(locations) != 1:
            raise RemoteError('prior runtime state', 'service configuration location is unproven')
        previous = prior_settings(posixpath.dirname(locations[0]) + '/host.json', required=True)
        if action == 'broker' and (previous['auth_mode'] != 'oauth-broker'
                                   or previous.get('broker_role', 'slave') != 'master'):
            raise RemoteError('prior runtime state', 'broker service does not match its recorded master role')
        if service_text != remote.render_service(previous, action):
            raise RemoteError('prior runtime state', 'installed service definition is unproven')
        if timer_text is not None:
            calendars = re.findall(r'^OnCalendar=([^\r\n]+)$', timer_text, re.M)
            if len(calendars) != 1:
                raise RemoteError('prior runtime state', 'installed timer definition is unproven')
            calendar = calendars[0]
            pattern = r'[A-Za-z0-9*/:,. -]+' if action == 'health' else r'\*:0/([1-9]|[1-5][0-9])'
            # Legacy host settings omit schedules; prove the full timer shape and its target.
            if not re.fullmatch(pattern, calendar) or timer_text != remote._timer(action, calendar):
                raise RemoteError('prior runtime state', 'installed timer definition is unproven')

    for path, content in unit_files.items():
        unit = posixpath.basename(path)
        output = driver.checked_exec(
            ['systemctl', 'show', unit, '--property=LoadState', '--property=ActiveState',
             '--property=FragmentPath', '--property=DropInPaths'],
            operation='inspect installed runtime', timeout=30)
        state = {}
        for line in output.splitlines():
            key, separator, value = line.partition('=')
            if not separator or key in state:
                raise RemoteError('prior runtime state', 'invalid installed unit response')
            state[key] = value
        if set(state) != {'LoadState', 'ActiveState', 'FragmentPath', 'DropInPaths'} or state['DropInPaths']:
            raise RemoteError('prior runtime state', 'unit overrides or state are unproven')
        if state['LoadState'] == 'not-found':
            if state['ActiveState'] != 'inactive' or state['FragmentPath']:
                raise RemoteError('prior runtime state', 'missing unit still has runtime state')
        elif (state['LoadState'] != 'loaded' or state['FragmentPath'] != path or content is None):
            raise RemoteError('prior runtime state', 'installed unit source is unproven')
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
    unit_drift = any(
        unit_files.get(path) != entry['content']
        or driver.stat(path) != {'kind': 'file', 'mode': entry['mode']}
        for path, entry in manifest.items()
        if path.startswith('/etc/systemd/system/') and path.endswith(('.service', '.timer')))
    for path, file_mode in directories.items():
        if driver.ensure_directory(path, file_mode):
            changed.append(path)
    marker = cfg + '/loaded-runtime.sha256'
    if unit_drift and driver.read_text(marker) == fingerprint:
        # Invalidate before writing units so an interrupted write or reload is retried.
        if _converge_file(driver, marker, '', 0o600):
            changed.append(marker)
    for path, entry in sorted(manifest.items()):
        if _converge_file(driver, path, entry['content'], entry['mode']):
            changed.append(path)
    for path in secret_paths:
        if driver.stat(path) != {'kind': 'file', 'mode': 0o600}:
            raise RemoteError('credential readback', 'unsafe mode')
    # Advance only after reload succeeds; retry reload after a partial file write.
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

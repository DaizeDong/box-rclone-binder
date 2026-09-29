"""Local runtime invoked on the target host. Secrets stay in files, pipes and env."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from . import config, health, refresh
from .atomic import atomic_write


def read_private_json(path):
    path = Path(path)
    if os.name != 'nt' and (path.is_symlink() or path.stat().st_mode & 0o077):
        raise RuntimeError('private runtime file has unsafe permissions')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise RuntimeError('runtime file must contain an object')
    return value


def access_blob(value):
    if not isinstance(value, dict) or not isinstance(value.get('access_token'), str) or not value['access_token']:
        raise RuntimeError('invalid access token response')
    if 'refresh_token' in value:
        raise RuntimeError('access-only token contains a refresh token')
    try:
        expiry = datetime.fromisoformat(value['expiry'].replace('Z', '+00:00'))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError('access token expiry is missing or elapsed') from None
    return {key: value[key] for key in ('access_token', 'token_type', 'expiry') if key in value}


def rclone_environment(host):
    cfg = Path(host.get('config_dir', '/etc/box-binder'))
    mode = host.get('auth_mode', 'jwt')
    prefix = 'RCLONE_CONFIG_' + host['remote_name'].upper() + '_'
    env = {key: value for key, value in os.environ.items() if not key.startswith('RCLONE_CONFIG_')}
    env[prefix + 'TYPE'] = 'box'
    env[prefix + 'ROOT_FOLDER_ID'] = str(host.get('root_folder_id', '0'))
    if mode == 'jwt':
        read_private_json(cfg / 'config.json')
        env[prefix + 'BOX_CONFIG_FILE'] = str(cfg / 'config.json')
        env[prefix + 'BOX_SUB_TYPE'] = host.get('box_sub_type', 'enterprise')
    elif mode == 'ccg-native':
        values = read_private_json(cfg / 'runtime.json')
        for key in ('client_id', 'client_secret', 'box_subject_id'):
            if not isinstance(values.get(key), str) or not values[key]:
                raise RuntimeError('native CCG credential missing')
            env[prefix + key.upper()] = values[key]
        env[prefix + 'CLIENT_CREDENTIALS'] = 'true'
        env[prefix + 'BOX_SUB_TYPE'] = host.get('box_sub_type', 'enterprise')
    elif mode in ('ccg-mint', 'oauth-broker'):
        env[prefix + 'TOKEN'] = json.dumps(access_blob(read_private_json(cfg / 'access.json')))
    else:
        raise RuntimeError('unsupported auth mode')
    if host.get('impersonate_user_id'):
        env[prefix + 'IMPERSONATE'] = str(host['impersonate_user_id'])
    return env


def validate_access(host, timeout=60):
    result = subprocess.run(health.probe_argv(host) + ['--config', os.devnull],
                            env=rclone_environment(host), capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('rclone access validation failed')


def execute(host, action, timeout=60):
    config.validate_host(host)
    mode = host.get('auth_mode', 'jwt')
    cfg = Path(host.get('config_dir', '/etc/box-binder'))
    if action == 'broker':
        return broker_cycle(host, timeout)
    if action == 'refresh' and mode == 'ccg-mint':
        values = read_private_json(cfg / 'runtime.json')
        fields = refresh.build_mint_fields(values['client_id'], values['client_secret'],
                                           values['box_subject_id'], host.get('box_sub_type', 'enterprise'))
        refresh.mint_access_token(os.environ.get('BOX_TOKEN_URL', refresh.DEFAULT_TOKEN_URL),
                                  fields, str(cfg / 'access.json'), timeout=timeout)
    elif action == 'refresh' and mode == 'oauth-broker':
        if host.get('broker_role') != 'master':
            raise RuntimeError('only the configured broker master may refresh')
        values = read_private_json(cfg / 'runtime.json')
        result = refresh.broker_refresh(str(cfg / 'broker-state.json'),
            os.environ.get('BOX_TOKEN_URL', refresh.DEFAULT_TOKEN_URL), str(cfg / 'broker.lock'),
            ['access'], values['client_id'], values['client_secret'], timeout=timeout)
        atomic_write(str(cfg / 'access.json'), json.dumps(access_blob(result['slave_blobs']['access'])), mode=0o600)
    validate_access(host, timeout)


def broker_cycle(host, timeout=60, factory=None):
    """Scheduled master cycle: persist locally, then attempt every peer independently."""
    from .drivers import SSHHostDriver
    from .deploy import _converge_file
    if host.get('auth_mode') != 'oauth-broker' or host.get('broker_role') != 'master':
        raise RuntimeError('broker schedule requires the master')
    peers = read_private_json(Path(host['config_dir']) / 'peers.json').get('hosts')
    if not isinstance(peers, list):
        raise RuntimeError('broker peer inventory is invalid')
    for peer in peers:
        config.validate_host(peer)
        if peer.get('auth_mode') != 'oauth-broker' or peer.get('broker_role') == 'master':
            raise RuntimeError('broker peers must be access-only slaves')
    execute(host, 'refresh', timeout)
    blob = json.dumps(access_blob(read_private_json(Path(host['config_dir']) / 'access.json')), sort_keys=True)
    results = []
    for peer in peers:
        try:
            driver = (factory or SSHHostDriver)(peer, dry_run=False)
            _converge_file(driver, peer.get('config_dir', '/etc/box-binder') + '/access.json', blob, 0o600)
            results.append(refresh.invoke_runtime(driver, peer, 'validate', timeout))
        except Exception as exc:
            results.append({'host': peer['host'], 'ok': False, 'error': refresh.safe_error(exc)})
    return {'ok': all(row['ok'] for row in results), 'results': results}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['health', 'refresh', 'validate', 'broker'])
    parser.add_argument('--host-file', required=True)
    parser.add_argument('--operation-id', default='scheduled')
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args(argv)
    report = {'ok': False, 'operation_id': args.operation_id, 'action': args.action}
    try:
        host = read_private_json(args.host_file)
        report['auth_mode'] = host.get('auth_mode', 'jwt')
        outcome = execute(host, args.action, args.timeout)
        report.update(outcome or {'ok': True})
    except Exception as exc:
        # Native tools and token endpoints can echo credentials; report only the error class.
        report['error'] = type(exc).__name__
    print(json.dumps(report, sort_keys=True))
    return 0 if report['ok'] else 1

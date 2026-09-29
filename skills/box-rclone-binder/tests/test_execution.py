"""Execution regressions with generated hosts and intercepted external boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'skills/box-rclone-binder/scripts'))
from make_fixtures import inventory, canary
import box_binder as cli
from boxbinder import config, deploy, remote
from boxbinder import refresh, runtime
from boxbinder.drivers import FakeHostDriver, SSHHostDriver


def args(**values):
    return SimpleNamespace(**dict({'host': [], 'dry_run': False, 'timeout': 20}, **values))


def credentials(mode='jwt'):
    if mode == 'jwt':
        return {'jwt_config': json.dumps({'synthetic': canary()})}
    return {'client_id': 'synthetic-client', 'client_secret': canary(),
            'box_subject_id': 'synthetic-subject'}


def test_refresh_plan_has_no_driver_and_execute_requires_current_result():
    cfg = config.Config(inventory('ccg-mint', 1))
    calls = []
    def factory(host, dry_run):
        calls.append(host['host'])
        return FakeHostDriver(host, responses={'python3': (1, '', canary())})
    code, plan = cli.cmd_refresh(cfg, args(dry_run=True), factory=factory)
    assert code == 0 and not calls and plan['status'] == 'planned'
    code, result = cli.cmd_refresh(cfg, args(), factory=factory)
    assert calls and code != 0 and result['summary']['failed'] == 1
    assert canary() not in json.dumps(result)


def test_runtime_manifest_contains_executables_package_and_mint_service():
    host = inventory('ccg-mint', 1)['hosts'][0]
    artifacts = remote.desired_artifacts(host)
    for path in ['/usr/local/bin/box-binder-health', '/usr/local/bin/box-binder-mint',
                 '/opt/box-binder/box_runtime.py', '/opt/box-binder/boxbinder/runtime.py',
                 '/opt/box-binder/boxbinder/refresh.py', '/opt/box-binder/install/mint.sh',
                 '/etc/systemd/system/box-binder-mint.service', '/etc/box-binder/host.json']:
        assert path in artifacts


@pytest.mark.parametrize('stage', ['daemon-reload', 'enable', 'start', 'is-enabled', 'is-active'])
def test_deployment_service_failure_is_not_success(stage):
    host = inventory('ccg-mint', 1)['hosts'][0]
    driver = FakeHostDriver(host, responses={'systemctl ' + stage: (7, '', canary())})
    with pytest.raises(RuntimeError) as failure:
        deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    assert canary() not in str(failure.value)


def test_remote_read_failure_is_distinct_from_missing(monkeypatch):
    driver = SSHHostDriver(inventory()['hosts'][0])
    monkeypatch.setattr(driver, '_ssh', lambda *a, **k: (255, '', canary()))
    with pytest.raises(RuntimeError) as failure:
        driver.read_text('/etc/box-binder/host.json')
    assert canary() not in str(failure.value)


def test_mint_form_never_expands_a_secret_into_curl_argv():
    text = (ROOT / 'skills/box-rclone-binder/scripts/install/mint.sh').read_text(encoding='utf-8')
    curl = text[text.index('curl '):]
    assert 'client_secret=${' not in curl
    assert '--data-binary @-' in curl or '--data @-' in curl


def fresh_receipt(host, *, stale=False):
    def respond(argv, input_text):
        return (0, json.dumps({'ok': True, 'operation_id': 'old' if stale else argv[argv.index('--operation-id') + 1],
                              'auth_mode': host['auth_mode'], 'action': argv[2]}), '')
    return respond


@pytest.mark.parametrize('mode', ['jwt', 'ccg-native', 'ccg-mint'])
@pytest.mark.parametrize('stale', [False, True])
def test_refresh_uses_selected_handler_and_rejects_old_receipts(mode, stale):
    cfg = config.Config(inventory(mode))
    drivers = {h['host']: FakeHostDriver(h, responses={'python3': fresh_receipt(h, stale=stale)}) for h in cfg.hosts}
    code, result = cli.cmd_refresh(cfg, args(), factory=lambda h, **k: drivers[h['host']])
    assert code == (2 if stale else 0)
    assert result['summary']['succeeded'] == (0 if stale else 2)
    for driver in drivers.values():
        assert len(driver.exec_log) == 1
        assert (' refresh ' if mode == 'ccg-mint' else ' validate ') in driver.exec_log[0][0]


def test_partial_refresh_retries_only_selected_failure():
    cfg = config.Config(inventory('ccg-mint'))
    drivers = {h['host']: FakeHostDriver(h, responses={'python3': fresh_receipt(h)}) for h in cfg.hosts}
    failed = cfg.hosts[1]['host']
    drivers[failed].responses['python3'] = (1, json.dumps({'ok': True}), canary())
    code, result = cli.cmd_refresh(cfg, args(), factory=lambda h, **k: drivers[h['host']])
    assert code == 1 and result['status'] == 'partial' and result['summary']['failed'] == 1
    assert canary() not in json.dumps(result)
    drivers[failed].responses['python3'] = fresh_receipt(cfg.hosts[1])
    code, result = cli.cmd_refresh(cfg, args(host=[failed]), factory=lambda h, **k: drivers[h['host']])
    assert code == 0 and len(drivers[cfg.hosts[0]['host']].exec_log) == 1
    assert result['summary']['total'] == 1


def test_unknown_selected_host_is_an_error():
    with pytest.raises(config.ConfigError):
        cli.cmd_refresh(config.Config(inventory()), args(host=['absent.example.com']))


def test_converge_rechecks_modes_and_disabled_timers_without_rewriting_files():
    host = inventory('ccg-mint', 1)['hosts'][0]
    driver = FakeHostDriver(host)
    first = deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    assert first['status'] == 'configured'
    before = driver.mutations
    second = deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    assert second['changed'] == [] and driver.mutations == before
    assert second['mutations'] == 0
    assert sum('is-active' in command for command, _ in driver.exec_log) >= 8
    driver.services['box-binder-mint.timer']['active'] = False
    driver.modes['/usr/local/bin/box-binder-health'] = 0o600
    third = deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    assert '/usr/local/bin/box-binder-health' in third['changed']
    assert driver.modes['/usr/local/bin/box-binder-health'] == 0o755
    assert driver.services['box-binder-mint.timer']['active']


def test_reload_failure_retries_even_after_all_files_were_written():
    host = inventory('ccg-mint', 1)['hosts'][0]
    driver = FakeHostDriver(host, responses={'systemctl daemon-reload': (1, '', canary())})
    with pytest.raises(RuntimeError):
        deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    driver.responses.clear()
    result = deploy.deploy_host(driver, host, credentials=credentials('ccg-mint'))
    assert result['status'] == 'configured'
    assert [cmd for cmd, _ in driver.exec_log].count('systemctl daemon-reload') == 2


def test_missing_credentials_fail_before_remote_mutation():
    host = inventory()['hosts'][0]
    driver = FakeHostDriver(host)
    with pytest.raises(RuntimeError, match='credentials'):
        deploy.deploy_host(driver, host)
    assert driver.mutations == 0


def test_write_success_without_matching_readback_is_failure():
    host = inventory()['hosts'][0]
    driver = FakeHostDriver(host)
    driver._write_impl = lambda *args: None
    with pytest.raises(RuntimeError, match='readback'):
        deploy.deploy_host(driver, host, credentials=credentials())


@pytest.mark.parametrize('payload', [None, {}, {'access_token': ''}, {'access_token': canary(), 'expires_in': -1}])
def test_invalid_token_response_does_not_replace_previous_file(tmp_path, monkeypatch, payload):
    tokenfile = tmp_path / 'access.json'
    tokenfile.write_text('previous', encoding='utf-8')
    monkeypatch.setattr(refresh, '_post_form', lambda *a, **k: (200, payload))
    with pytest.raises((refresh.Retryable, refresh.NonRetryable)):
        refresh.mint_access_token('https://example.invalid/token', {}, str(tokenfile))
    assert tokenfile.read_text() == 'previous'


def test_broker_response_without_rotated_token_cannot_distribute(tmp_path, monkeypatch):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'refresh_token': 'synthetic-old'}))
    monkeypatch.setattr(refresh, '_post_form', lambda *a, **k: (200, {'access_token': canary(), 'expires_in': 3600}))
    with pytest.raises(refresh.NonRetryable):
        refresh.broker_refresh(str(state), 'https://example.invalid/token', str(tmp_path/'lock'),
                               ['node2.example.com'], 'synthetic-client', canary())
    assert json.loads(state.read_text())['refresh_token'] == 'synthetic-old'


def test_rclone_consumes_access_file_through_environment_not_argv(tmp_path, monkeypatch):
    host = inventory('ccg-mint', 1)['hosts'][0]
    host['config_dir'] = str(tmp_path)
    token = {'access_token': canary(), 'token_type': 'bearer', 'expiry': '2099-01-01T00:00:00Z'}
    (tmp_path / 'access.json').write_text(json.dumps(token))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    runtime.validate_access(host)
    assert canary() not in json.dumps(calls[0][0])
    assert json.loads(calls[0][1]['env']['RCLONE_CONFIG_BOX_TOKEN']) == token
    assert calls[0][0][-2:] == ['--config', os.devnull]


def test_runtime_failure_output_never_echoes_exception_secrets(tmp_path, monkeypatch, capsys):
    host = inventory('ccg-mint', 1)['hosts'][0]
    config_path = tmp_path / 'host.json'
    config_path.write_text(json.dumps(host))
    def fail(*args):
        raise RuntimeError(canary())
    monkeypatch.setattr(runtime, 'execute', fail)
    assert runtime.main(['refresh', '--host-file', str(config_path), '--operation-id', 'fresh']) == 1
    output = capsys.readouterr().out
    assert canary() not in output and json.loads(output)['operation_id'] == 'fresh'


def test_broker_slave_retry_reuses_master_token_without_rotating():
    cfg = config.Config(inventory('oauth-broker'))
    blob = {'access_token': canary(), 'expiry': '2099-01-01T00:00:00Z', 'token_type': 'bearer'}
    master, slave = cfg.hosts
    drivers = {master['host']: FakeHostDriver(master, fs={'/etc/box-binder/access.json': json.dumps(blob)}),
               slave['host']: FakeHostDriver(slave, responses={'python3': fresh_receipt(slave)})}
    code, result = cli.cmd_refresh(cfg, args(host=[slave['host']]), factory=lambda h, **k: drivers[h['host']])
    assert code == 0 and drivers[master['host']].exec_log == []
    assert json.loads(drivers[slave['host']].fs['/etc/box-binder/access.json']) == blob
    assert canary() not in json.dumps(result)


@pytest.mark.parametrize('change', ['missing', 'expired', 'refresh_token'])
def test_invalid_broker_source_blocks_slave_write(change):
    cfg = config.Config(inventory('oauth-broker'))
    blob = {'access_token': canary(), 'expiry': '2099-01-01T00:00:00Z'}
    if change == 'expired':
        blob['expiry'] = '2000-01-01T00:00:00Z'
    if change == 'refresh_token':
        blob['refresh_token'] = 'synthetic-rotating'
    master, slave = cfg.hosts
    drivers = {master['host']: FakeHostDriver(master, fs={} if change == 'missing' else
               {'/etc/box-binder/access.json': json.dumps(blob)}), slave['host']: FakeHostDriver(slave)}
    code, result = cli.cmd_refresh(cfg, args(host=[slave['host']]), factory=lambda h, **k: drivers[h['host']])
    assert code != 0 and drivers[slave['host']].mutations == 0
    assert canary() not in json.dumps(result)


@pytest.mark.parametrize('reply', ['success', 'invalid', 'failure'])
def test_mint_shell_uses_curl_stdin_and_preserves_old_token_on_failure(tmp_path, reply):
    bash = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/sh')
    assert bash.is_file(), 'A shell is required to verify the shipped mint helper'
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    python_path = Path(sys.executable).as_posix()
    python_stub = fakebin / 'python3'
    python_stub.write_text('#!/bin/sh\nexec "' + python_path + '" "$@"\n', encoding='utf-8', newline='\n')
    curl_script = tmp_path / 'fake_curl.py'
    curl_script.write_text('''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
body=sys.stdin.read()
Path(os.environ['BOX_TEST_RECORD']).write_text(json.dumps({'argv':args,'stdin':body}))
if os.environ['BOX_TEST_REPLY']=='failure':
    sys.stderr.write(os.environ['BOX_BINDER_CLIENT_SECRET'])
    sys.exit(22)
output=Path(args[args.index('-o')+1])
payload={'access_token':os.environ['BOX_TEST_TOKEN'],'expires_in':3600,'refresh_token':'synthetic-ignored'}
output.write_text(json.dumps(payload) if os.environ['BOX_TEST_REPLY']=='success' else '{}')
''', encoding='utf-8')
    curl = fakebin / 'curl'
    curl.write_text('#!/bin/sh\nexec "' + python_path + '" "' + curl_script.as_posix() + '" "$@"\n', encoding='utf-8', newline='\n')
    for path in (curl, python_stub):
        path.chmod(0o755)
    tokenfile, record = tmp_path / 'access.json', tmp_path / 'curl-record.json'
    tokenfile.write_text('previous', encoding='utf-8')
    secret = canary() + '& synthetic=100% +'
    env = dict(os.environ, BOX_BINDER_CLIENT_ID='synthetic-client', BOX_BINDER_CLIENT_SECRET=secret,
               BOX_BINDER_ENTERPRISE_ID='synthetic-subject', BOX_BINDER_TOKENFILE=tokenfile.as_posix(),
               BOX_TOKEN_URL='https://example.invalid/token', BOX_TEST_RECORD=str(record),
               BOX_TEST_REPLY=reply, BOX_TEST_TOKEN=canary())
    env['BOX_TEST_BIN'] = str(fakebin)
    env['BOX_TEST_HELPER'] = str(ROOT/'skills/box-rclone-binder/scripts/install/mint.sh')
    preflight = ('stub_dir="$(cygpath -u "$BOX_TEST_BIN")"; ' if os.name == 'nt' else 'stub_dir="$BOX_TEST_BIN"; ')
    preflight += ('export PATH="$stub_dir:/usr/bin:/bin"; '
                  'test "$(command -v curl)" = "$stub_dir/curl" || exit 97; '
                  'test "$(command -v python3)" = "$stub_dir/python3" || exit 98; '
                  'exec /bin/sh "$BOX_TEST_HELPER"')
    result = subprocess.run([str(bash), '-c', preflight],
                            env=env, capture_output=True, text=True, timeout=30)
    assert record.is_file(), (result.returncode, result.stdout, result.stderr)
    captured = json.loads(record.read_text())
    assert secret not in json.dumps(captured['argv'])
    assert urllib.parse.parse_qs(captured['stdin'])['client_secret'] == [secret]
    assert canary() not in result.stdout + result.stderr
    assert result.returncode == (0 if reply == 'success' else 1)
    if reply == 'success':
        payload = json.loads(tokenfile.read_text())
        assert payload['access_token'] == canary() and 'refresh_token' not in payload
        assert runtime.access_blob(payload)
    else:
        assert tokenfile.read_text() == 'previous'
    assert not list(tmp_path.glob('.bb*'))


def test_broker_master_manifest_includes_its_distribution_schedule():
    host = inventory('oauth-broker')['hosts'][0]
    artifacts = remote.desired_artifacts(host)
    for path in ('/usr/local/bin/box-binder-broker', '/etc/systemd/system/box-binder-broker.service',
                 '/etc/systemd/system/box-binder-broker.timer', '/etc/box-binder/peers.json'):
        assert path in artifacts


@pytest.mark.parametrize('mode', ['jwt', 'ccg-native', 'ccg-mint', 'oauth-broker'])
def test_runtime_refresh_dispatches_then_validates(mode, monkeypatch):
    host = inventory(mode)['hosts'][0]
    events = []
    monkeypatch.setattr(runtime, 'read_private_json', lambda path: credentials('ccg-mint'))
    monkeypatch.setattr(refresh, 'mint_access_token', lambda *a, **k: events.append('mint'))
    def broker(*args, **kwargs):
        events.append('broker-persist')
        return {'slave_blobs': {'access': {'access_token': canary(), 'expiry': '2099-01-01T00:00:00Z'}}}
    monkeypatch.setattr(refresh, 'broker_refresh', broker)
    monkeypatch.setattr(runtime, 'atomic_write', lambda *a, **k: events.append('access-persist'))
    monkeypatch.setattr(runtime, 'validate_access', lambda *a, **k: events.append('validate'))
    runtime.execute(host, 'refresh')
    expected = {'jwt': ['validate'], 'ccg-native': ['validate'], 'ccg-mint': ['mint', 'validate'],
                'oauth-broker': ['broker-persist', 'access-persist', 'validate']}[mode]
    assert events == expected


def test_broker_never_steals_an_old_lock_from_a_live_owner(tmp_path, monkeypatch):
    path = tmp_path / 'broker.lock'
    owner = refresh.FileLock(str(path), stale_after=1, pid_alive=lambda pid: True).acquire()
    try:
        os.utime(path, (1, 1))
        with monkeypatch.context() as patcher:
            patcher.setattr(refresh.os, 'remove', lambda path: pytest.fail('attempted to unlink a live lock'))
            with pytest.raises(refresh.Locked):
                refresh.FileLock(str(path), stale_after=1, pid_alive=lambda pid: True).acquire()
    finally:
        owner.release()
    with refresh.FileLock(str(path)):
        pass


def test_scheduled_broker_distributes_after_refresh_and_reports_partial(monkeypatch):
    hosts = inventory('oauth-broker', 3)['hosts']
    master, first, second = hosts
    events = []
    def read(path):
        if str(path).endswith('peers.json'):
            return {'hosts': hosts[1:]}
        events.append('access-read')
        return {'access_token': canary(), 'expiry': '2099-01-01T00:00:00Z'}
    monkeypatch.setattr(runtime, 'read_private_json', read)
    monkeypatch.setattr(runtime, 'execute', lambda *a: events.append('refresh-persist'))
    drivers = {h['host']: FakeHostDriver(h, responses={'python3': fresh_receipt(h)}) for h in hosts[1:]}
    drivers[second['host']].responses['python3'] = (1, '', canary())
    result = runtime.broker_cycle(master, factory=lambda h, **kw: drivers[h['host']])
    assert events == ['refresh-persist', 'access-read']
    assert result['ok'] is False and [row['ok'] for row in result['results']] == [True, False]
    assert canary() not in json.dumps(result)


def test_healthcheck_uses_the_deployed_runtime_environment():
    cfg = config.Config(inventory('ccg-native', 1))
    host = cfg.hosts[0]
    driver = FakeHostDriver(host, responses={'python3': fresh_receipt(host),
                                            'rclone': (1, '', 'missing runtime credentials')})
    code, result = cli.cmd_healthcheck(cfg, args(), factory=lambda *a, **k: driver)
    assert code == 0 and result['hosts'][0]['healthy']
    assert all(command.startswith('python3 ') for command, _ in driver.exec_log)


def test_secret_resolution_supports_environment_and_refuses_unsupported_backend(monkeypatch):
    cfg = config.Config(inventory('ccg-mint', 1))
    monkeypatch.setenv('BOX_TEST_ID', 'synthetic-client')
    monkeypatch.setenv('BOX_TEST_SECRET', canary())
    monkeypatch.setenv('BOX_TEST_SUBJECT', 'synthetic-subject')
    assert config.secret_values(cfg, cfg.hosts[0]) == credentials('ccg-mint')
    monkeypatch.delenv('BOX_TEST_SECRET')
    with pytest.raises(config.ConfigError):
        config.secret_values(cfg, cfg.hosts[0])
    cfg.secrets['source'] = 'vault'
    with pytest.raises(config.ConfigError, match='env/file'):
        config.secret_values(cfg, cfg.hosts[0])


def test_broker_redeploy_never_overwrites_rotated_state():
    host = inventory('oauth-broker')['hosts'][0]
    state = json.dumps({'refresh_token': 'synthetic-newer'})
    driver = FakeHostDriver(host, fs={'/etc/box-binder/broker-state.json': state})
    values = dict(credentials('oauth-broker'), broker_state=json.dumps({'refresh_token': 'synthetic-old'}))
    deploy.deploy_host(driver, host, credentials=values)
    assert driver.fs['/etc/box-binder/broker-state.json'] == state


def test_failed_deploy_reports_already_completed_mutations(monkeypatch):
    cfg = config.Config(inventory('ccg-mint', 1))
    host = cfg.hosts[0]
    driver = FakeHostDriver(host, responses={'systemctl daemon-reload': (1, '', canary())})
    monkeypatch.setattr(config, 'secret_values', lambda *a: credentials('ccg-mint'))
    code, result = cli.cmd_deploy(cfg, args(), factory=lambda *a, **k: driver)
    row = result['results'][0]
    assert code != 0 and row['mutations'] == driver.mutations > 0
    assert row['changed'] and canary() not in json.dumps(result)


@pytest.mark.parametrize('tool', ['python3 --version', 'rclone version'])
def test_deploy_refuses_missing_runtime_prerequisite_before_writes(tool):
    host = inventory()['hosts'][0]
    driver = FakeHostDriver(host, responses={tool: (127, '', 'synthetic missing command')})
    with pytest.raises(RuntimeError):
        deploy.deploy_host(driver, host, credentials=credentials())
    assert driver.mutations == 0

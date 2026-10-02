"""Generated target-observation regressions; no live host is contacted."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'skills/box-rclone-binder/scripts')]
from make_fixtures import inventory, observed_runtime_receipt
import box_binder as cli
from boxbinder import config, runtime


class ReceiptDriver:
    def __init__(self, host, case):
        self.host, self.case = host, case

    def checked_exec(self, argv, mutate, timeout, operation):
        assert argv[2] == 'health' and mutate is False
        operation_id = argv[argv.index('--operation-id') + 1]
        return json.dumps(observed_runtime_receipt(self.host, 'health', operation_id, self.case))


def healthcheck(cases):
    cfg = config.Config(inventory(count=len(cases)))
    chosen = dict(zip((host['host'] for host in cfg.hosts), cases))
    def factory(host, dry_run):
        assert dry_run is False
        return ReceiptDriver(host, chosen[host['host']])
    args = SimpleNamespace(host=[], dry_run=False, timeout=5)
    return cli.cmd_healthcheck(cfg, args, factory=factory)


def test_target_settings_are_used_to_report_drift():
    code, result = healthcheck(['matching', 'drift'])
    assert code == 1
    assert result['hosts'][1]['root_folder_id'] == '987654'
    assert result['hosts'][1]['remote_name'] == 'box_other'
    assert result['consistency']['consistent'] is False
    assert {row['field'] for row in result['consistency']['divergences']} == {'root_folder_id', 'remote_name'}


def test_old_receipt_cannot_certify_deployed_consistency():
    code, result = healthcheck(['missing'])
    assert code == 0 and result['hosts'][0]['healthy'] is True
    assert 'root_folder_id' not in result['hosts'][0]
    assert result['consistency']['consistent'] is None
    assert 'root_folder_id' in result['consistency']['unobserved_fields']
    assert result['refresh_token_invariant']['ok'] is None


def test_rclone_version_is_explicitly_unobserved():
    _, result = healthcheck(['matching'])
    assert result['consistency']['unobserved_fields'] == ['rclone_version']
    assert result['consistency']['consistent'] is None


@pytest.mark.parametrize('case', ['malformed', 'empty', 'incomplete'])
def test_malformed_runtime_observations_fail_the_host_check(case):
    code, result = healthcheck([case])
    assert code != 0 and result['hosts'][0]['healthy'] is False
    assert result['consistency']['consistent'] is None


def test_runtime_reports_the_settings_it_used(monkeypatch, capsys):
    host = inventory(count=1)['hosts'][0]
    monkeypatch.setattr(runtime, 'read_private_json', lambda path: host)
    monkeypatch.setattr(runtime, 'validate_access', lambda host, timeout: None)
    assert runtime.main(['health', '--host-file', 'synthetic-host.json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['observed_settings'] == {'auth_mode': 'jwt', 'root_folder_id': '0',
        'box_sub_type': 'enterprise', 'remote_name': 'box'}

"""Generated local readiness checks use only synthetic inventories and credentials."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'skills/box-rclone-binder/scripts')]
from make_fixtures import canary, inventory
import verify_config


@pytest.mark.parametrize('mode', ['jwt', 'ccg-native', 'ccg-mint', 'oauth-broker'])
def test_missing_required_reference_is_not_ready(mode, tmp_path, monkeypatch, capsys):
    data = inventory(mode, 1)
    for value in data['secrets'].values():
        monkeypatch.delenv(value, raising=False)
    target = tmp_path / 'machines.yaml'
    target.write_text(json.dumps(data), encoding='utf-8')
    assert verify_config.main(['-c', str(target), '--json']) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['schema_valid'] is True
    assert report['ready'] is False
    assert report['required_ref_errors']


def test_only_selected_host_requirements_control_readiness(tmp_path, monkeypatch, capsys):
    data = inventory('jwt', 1)
    for value in data['secrets'].values():
        monkeypatch.delenv(value, raising=False)
    monkeypatch.setenv(data['secrets']['jwt_config_ref'], json.dumps({'synthetic': canary()}))
    target = tmp_path / 'machines.yaml'
    target.write_text(json.dumps(data), encoding='utf-8')
    assert verify_config.main(['-c', str(target), '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['ready'] is True and report['missing_refs']
    assert canary() not in json.dumps(report)


def test_host_override_cannot_borrow_fleet_readiness(tmp_path, monkeypatch, capsys):
    data = inventory('jwt', 2)
    monkeypatch.setenv(data['secrets']['jwt_config_ref'], json.dumps({'synthetic': canary()}))
    data['hosts'][1]['secrets'] = {'jwt_config_ref': data['secrets']['broker_state_ref']}
    monkeypatch.delenv(data['secrets']['broker_state_ref'], raising=False)
    target = tmp_path / 'machines.yaml'
    target.write_text(json.dumps(data), encoding='utf-8')
    assert verify_config.main(['-c', str(target), '--json']) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['ready'] is False
    assert canary() not in json.dumps(report)

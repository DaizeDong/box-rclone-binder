"""Generated schema-boundary tests using synthetic generator inputs only."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'skills/box-rclone-binder/scripts')]
from make_fixtures import canary, inventory, malformed_inventory, security_inventory_text
import box_binder as cli
from boxbinder import config, yamlmin


@pytest.mark.parametrize('parser', ['pyyaml', 'bundled'])
@pytest.mark.parametrize('case', ['json-inline', 'nested-list', 'host-literal-ref',
    'host-secret-value', 'quoted-inline', 'flow-inline', 'nested-secret-section',
    'nested-literal-ref', 'duplicate-mapping'])
def test_config_rejects_inline_secrets_in_every_supported_presentation(case, parser, tmp_path, monkeypatch):
    if parser == 'bundled':
        monkeypatch.setattr(config, '_yload', yamlmin.load)
    path = tmp_path / 'machines.yaml'
    path.write_text(security_inventory_text(case), encoding='utf-8')
    with pytest.raises(config.ConfigError) as failure:
        config.load(str(path))
    assert canary() not in str(failure.value)


@pytest.mark.parametrize('case', ['version-bool', 'version-string', 'version-future',
    'version-null', 'version-float', 'alias-conflict', 'root-list', 'defaults-list',
    'secrets-list', 'alerts-list', 'defaults-pairs', 'hosts-mapping', 'hosts-null-item',
    'host-secrets-list', 'host-secrets-literal', 'remote-name-bool', 'reference-number'])
def test_cli_reports_malformed_schema_as_redacted_config_error(case, tmp_path, capsys):
    path = tmp_path / 'machines.yaml'
    path.write_text(json.dumps(malformed_inventory(case)), encoding='utf-8')
    assert cli.run(['verify-config', '-c', str(path), '--json']) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['exit_code'] == 3 and report['error']
    assert not report.get('schema_valid', False)
    assert canary() not in json.dumps(report)


def test_cli_redacts_yaml_parser_failure(tmp_path, capsys):
    path = tmp_path / 'machines.yaml'
    path.write_text('hosts: [' + canary(), encoding='utf-8')
    assert cli.run(['verify-config', '-c', str(path), '--json']) == 3
    report = json.loads(capsys.readouterr().out)
    assert report['error'] and canary() not in json.dumps(report)


@pytest.mark.parametrize('version_key', ['version', 'schema_version', None])
def test_supported_schema_and_generated_references_still_load(version_key, tmp_path):
    data = inventory(count=1)
    data.pop('version')
    if version_key:
        data[version_key] = 1
    path = tmp_path / 'machines.yaml'
    path.write_text(json.dumps(data), encoding='utf-8')
    loaded = config.load(str(path))
    assert loaded.version == 1 and len(loaded.hosts) == 1


def test_host_reference_override_is_checked_before_reading_secret(monkeypatch):
    cfg = config.Config(inventory('ccg-native', 1))
    cfg.hosts[0]['secrets'] = {'client_secret_ref': canary()}
    with pytest.raises(config.ConfigError):
        config.validate(cfg)


def test_bundled_parser_accepts_generated_example():
    path = ROOT / 'skills/box-rclone-binder/config/machines.example.yaml'
    parsed = yamlmin.load(path.read_text(encoding='utf-8'))
    cfg = config.Config(parsed)
    config.validate(cfg)
    assert cfg.version == 1 and len(cfg.hosts) == 1

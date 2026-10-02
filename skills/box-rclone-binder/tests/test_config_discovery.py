"""Generated config-discovery regressions; inventories and paths are synthetic."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'skills/box-rclone-binder/scripts')]
from make_fixtures import inventory
import box_binder as cli
import init_config as initializer
import verify_config as verifier
from boxbinder.config import ConfigError


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    cwd = tmp_path / 'work'
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv(cli.CONFIG_ENV, raising=False)
    monkeypatch.delenv(cli.CONFIG_ENV_DIR, raising=False)
    monkeypatch.chdir(cwd)
    return home, cwd


@pytest.mark.parametrize('legacy', ['cwd', 'dotdir', 'xdg'])
@pytest.mark.parametrize('configured', [False, True])
def test_missing_companion_never_selects_legacy_inventory(
        legacy, configured, isolated_paths, tmp_path, monkeypatch):
    home, cwd = isolated_paths
    locations = {'cwd': cwd / 'machines.yaml',
                 'dotdir': home / '.box-rclone-binder-config/machines.yaml',
                 'xdg': home / '.config/box-rclone-binder/machines.yaml'}
    path = locations[legacy]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(inventory()), encoding='utf-8')
    companion = str(tmp_path / 'private-companion/machines.yaml') if configured else None
    monkeypatch.setattr(cli, '_companion_config_path', lambda: companion)
    if configured:
        assert cli.discover_config_path() == companion
    else:
        with pytest.raises(ConfigError):
            cli.discover_config_path()


def test_explicit_and_environment_priority_is_preserved(isolated_paths, tmp_path, monkeypatch):
    explicit = tmp_path / 'explicit.yaml'
    env_file = tmp_path / 'environment.yaml'
    env_dir = tmp_path / 'environment-dir'
    env_dir.mkdir()
    monkeypatch.setattr(cli, '_companion_config_path', lambda: None)
    monkeypatch.setenv(cli.CONFIG_ENV, str(env_file))
    monkeypatch.setenv(cli.CONFIG_ENV_DIR, str(env_dir))
    assert cli.discover_config_path(str(explicit)) == str(explicit)
    assert cli.discover_config_path() == str(env_file)
    monkeypatch.delenv(cli.CONFIG_ENV)
    assert cli.discover_config_path() == str(env_dir / 'machines.yaml')


@pytest.mark.parametrize('fault', ['no-spec', 'no-loader', 'no-api', 'bad-api', 'load-error'])
def test_broken_resolver_is_a_config_error(fault, isolated_paths, monkeypatch):
    def execute(module):
        if fault == 'load-error':
            raise ImportError('synthetic resolver dependency missing')
    spec = None if fault == 'no-spec' else SimpleNamespace(
        loader=None if fault == 'no-loader' else SimpleNamespace(exec_module=execute))
    module = SimpleNamespace()
    if fault == 'bad-api':
        module.resolve_companion_root = 1
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', lambda *args: spec)
    monkeypatch.setattr(importlib.util, 'module_from_spec', lambda value: module)
    with pytest.raises(ConfigError):
        cli._companion_config_path()


@pytest.mark.parametrize('entrypoint', ['cli', 'verify', 'init'])
def test_uninitialized_entrypoints_fail_without_creating_config(
        entrypoint, isolated_paths, monkeypatch, capsys):
    home, cwd = isolated_paths
    monkeypatch.setattr(cli, '_companion_config_path', lambda: None)
    if entrypoint == 'cli':
        code = cli.run(['verify-config', '--json'])
    elif entrypoint == 'verify':
        code = verifier.main(['--json'])
    else:
        monkeypatch.setattr(sys, 'argv', ['init_config.py'])
        code = initializer.main()
    assert code == 3
    assert not list(home.rglob('machines.yaml'))
    assert not (cwd / 'machines.yaml').exists()
    if entrypoint != 'init':
        report = json.loads(capsys.readouterr().out)
        assert report['exit_code'] == 3 and report['error']


@pytest.mark.parametrize('selected_by', ['companion', 'environment'])
def test_init_and_verify_use_the_same_default_inventory(
        selected_by, isolated_paths, tmp_path, monkeypatch, capsys):
    expected = tmp_path / 'private-companion/machines.yaml'
    monkeypatch.setattr(cli, '_companion_config_path', lambda: str(expected))
    if selected_by == 'environment':
        expected = tmp_path / 'selected-companion/machines.yaml'
        monkeypatch.setenv(cli.CONFIG_ENV, str(expected))
    monkeypatch.setattr(sys, 'argv', ['init_config.py'])
    assert initializer.main() == 0
    assert expected.is_file()
    assert expected.read_bytes() == Path(initializer.template_path()).read_bytes()
    assert cli.discover_config_path() == str(expected)
    capsys.readouterr()
    assert verifier.main(['--json']) == 0
    assert json.loads(capsys.readouterr().out)['config_path'] == str(expected)


def test_explicit_init_preserves_existing_file_without_force(
        isolated_paths, tmp_path, monkeypatch):
    target = tmp_path / 'selected-companion/machines.yaml'
    target.parent.mkdir()
    previous = json.dumps(inventory('ccg-native', 1)).encode('utf-8')
    target.write_bytes(previous)
    monkeypatch.setattr(sys, 'argv', ['init_config.py', '--out', str(target)])
    assert initializer.main() == 0
    assert target.read_bytes() == previous
    monkeypatch.setattr(sys, 'argv', ['init_config.py', '--out', str(target), '--force'])
    assert initializer.main() == 0
    assert target.read_bytes() == Path(initializer.template_path()).read_bytes()


@pytest.mark.parametrize('relative_target', ['machines.yaml', 'config/machines.yaml'])
def test_init_refuses_inventory_inside_the_public_tool_tree(
        relative_target, isolated_paths, tmp_path, monkeypatch):
    tool = tmp_path / 'synthetic-public-tool'
    entrypoint = tool / 'skills/box-rclone-binder/scripts/init_config.py'
    entrypoint.parent.mkdir(parents=True)
    target = tool / relative_target
    template = initializer.template_path()
    monkeypatch.setattr(initializer, '__file__', str(entrypoint))
    monkeypatch.setattr(initializer, 'template_path', lambda: template)
    monkeypatch.setattr(sys, 'argv', ['init_config.py', '--out', str(target)])
    assert initializer.main() == 3
    assert not target.exists()


def test_environment_hot_swap_changes_verified_inventory(
        isolated_paths, tmp_path, monkeypatch, capsys):
    for count in (1, 2):
        path = tmp_path / ('fleet%d.yaml' % count)
        path.write_text(json.dumps(inventory(count=count)), encoding='utf-8')
        monkeypatch.setenv(cli.CONFIG_ENV, str(path))
        assert verifier.main(['--json']) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['ready'] is True and len(result['hosts']) == count
        assert result['config_path'] == str(path)

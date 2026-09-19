"""The compatibility writer delegates to the shared atomic implementation."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from boxbinder import atomic
from fleet_guards import filesystem


def test_legacy_return_and_single_shared_writer(tmp_path, monkeypatch):
    destination = tmp_path / 'result'
    calls = []
    def fail(path, data, mode=0o600):
        calls.append((path, data, mode))
        raise OSError('synthetic shared publication failure')
    monkeypatch.setattr(filesystem, 'atomic_replace', fail)
    with pytest.raises(OSError, match='synthetic shared publication failure'):
        atomic.atomic_write(str(destination), 'value')
    assert calls == [(str(destination), 'value', 0o600)]
    assert not destination.exists()

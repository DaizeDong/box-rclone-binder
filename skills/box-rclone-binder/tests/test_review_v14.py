"""Generated synthetic broker rejection regressions."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/box-rclone-binder/scripts"))
from boxbinder import refresh


@pytest.mark.parametrize(
    "status,error,exception_type,message",
    [
        pytest.param(
            400, "invalid_client", refresh.NonRetryable,
            "broker refresh rejected: invalid_client; check client credentials and authorization",
            id="invalid-client",
        ),
        pytest.param(
            401, "unauthorized_client", refresh.NonRetryable,
            "broker refresh rejected: unauthorized_client; check client credentials and authorization",
            id="unauthorized-client",
        ),
        pytest.param(
            400, "invalid_grant", refresh.NonRetryable,
            "refresh chain broken (invalid_grant): manual re-auth required",
            id="invalid-grant",
        ),
        pytest.param(
            503, "temporarily_unavailable", refresh.Retryable,
            "broker refresh failed: http 503",
            id="transient-status",
        ),
    ],
)
def test_broker_rejection_preserves_state_and_releases_lock(
        tmp_path, monkeypatch, status, error, exception_type, message):
    state_path = tmp_path / "broker-state.json"
    lock_path = tmp_path / "broker.lock"
    original = json.dumps({
        "refresh_token": "synthetic-refresh-token",
        "retained": "synthetic-state-marker",
    }).encode("utf-8")
    state_path.write_bytes(original)
    token_url = "https://oauth.example.com/token"
    calls = []

    def reject(url, fields, timeout):
        calls.append((url, dict(fields), timeout))
        contender = refresh.FileLock(str(lock_path))
        try:
            with pytest.raises(refresh.Locked):
                contender.acquire()
        finally:
            contender.release()
        return status, {"error": error}

    def forbidden_stage(*args, **kwargs):
        pytest.fail("a rejected refresh must not persist or render tokens")

    class ForbiddenSlaves:
        def __iter__(self):
            pytest.fail("a rejected refresh must not begin slave distribution")

    monkeypatch.setattr(refresh, "_post_form", reject)
    monkeypatch.setattr(refresh, "atomic_write", forbidden_stage)
    monkeypatch.setattr(refresh, "_token_blob", forbidden_stage)

    with pytest.raises(exception_type) as caught:
        refresh.broker_refresh(
            str(state_path), token_url, str(lock_path), ForbiddenSlaves(),
            "synthetic-client-id", "synthetic-client-secret", timeout=7,
        )

    assert type(caught.value) is exception_type
    assert str(caught.value) == message
    assert calls == [(token_url, {
        "grant_type": "refresh_token",
        "refresh_token": "synthetic-refresh-token",
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
    }, 7)]
    assert state_path.read_bytes() == original
    with refresh.FileLock(str(lock_path)):
        assert state_path.read_bytes() == original

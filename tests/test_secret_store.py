import os

from app.services import secret_store


def test_set_secret_falls_back_to_file_when_keychain_fails(tmp_path, monkeypatch):
    secrets_path = tmp_path / "credentials.json"
    monkeypatch.setattr(secret_store, "_keyring_module", lambda: None)
    monkeypatch.setattr(secret_store, "_security_bin", lambda: None)
    monkeypatch.setattr(secret_store, "_secrets_file", lambda: str(secrets_path))

    secret_store.set_secret("facebook.default.user_token", "token-abc")
    assert secret_store.get_secret("facebook.default.user_token") == "token-abc"
    assert secrets_path.is_file()
    assert (os.stat(secrets_path).st_mode & 0o777) == 0o600

    secret_store.delete_secret("facebook.default.user_token")
    assert secret_store.get_secret("facebook.default.user_token") is None


def test_set_secret_retries_after_stale_keychain_item(monkeypatch):
    calls = {"set": 0, "delete": 0}

    class FakeKeyring:
        @staticmethod
        def set_password(service, username, password):
            calls["set"] += 1
            if calls["set"] == 1:
                raise RuntimeError("Can't store password on keychain: (-25244, 'Unknown Error')")

        @staticmethod
        def delete_password(service, username):
            calls["delete"] += 1

    monkeypatch.setattr(secret_store, "_keyring_module", lambda: FakeKeyring)
    monkeypatch.setattr(secret_store, "_security_delete", lambda _ref: None)
    monkeypatch.setattr(secret_store, "_security_write", lambda *_a, **_k: False)
    def _no_file(*_a, **_k):
        raise AssertionError("should not file-fallback")

    monkeypatch.setattr(secret_store, "_file_set", _no_file)

    secret_store.set_secret("facebook.default.app_secret", "secret")
    assert calls["set"] == 2
    assert calls["delete"] == 1

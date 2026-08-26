"""Store provider credentials outside SQLite and the Electron bundle.

macOS Keychain items created by another Python/Electron binary cannot be
updated (errSecInvalidOwnerEdit / -25244). Fall back to `security` CLI, then
a 0600 file under data/secrets/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Dict, Optional


SERVICE_NAME = "com.reupvideo.studio"


class SecretStoreError(RuntimeError):
    pass


def _keyring_module():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def _secrets_file() -> str:
    try:
        from app.config import settings

        base = os.path.join(str(getattr(settings, "BASE_DIR", ".")), "data", "secrets")
    except Exception:
        base = os.path.join("data", "secrets")
    return os.path.abspath(os.path.join(base, "credentials.json"))


def _file_load() -> Dict[str, str]:
    path = _secrets_file()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items() if k and v is not None}
    except Exception:
        return {}
    return {}


def _file_set(reference: str, value: str) -> None:
    path = _secrets_file()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        os.chmod(os.path.dirname(path), 0o700)
    except OSError:
        pass
    data = _file_load()
    data[reference] = value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _file_get(reference: str) -> Optional[str]:
    value = _file_load().get(reference)
    return value if value else None


def _file_delete(reference: str) -> None:
    path = _secrets_file()
    data = _file_load()
    if reference not in data:
        return
    data.pop(reference, None)
    if not data:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _security_bin() -> Optional[str]:
    return shutil.which("security")


def _security_delete(reference: str) -> None:
    security = _security_bin()
    if not security:
        return
    subprocess.run(
        [
            security,
            "delete-generic-password",
            "-a",
            reference,
            "-s",
            SERVICE_NAME,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _security_write(reference: str, value: str) -> bool:
    security = _security_bin()
    if not security:
        return False
    _security_delete(reference)
    result = subprocess.run(
        [
            security,
            "add-generic-password",
            "-U",
            "-a",
            reference,
            "-s",
            SERVICE_NAME,
            "-w",
            value,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _security_read(reference: str) -> Optional[str]:
    security = _security_bin()
    if not security:
        return None
    result = subprocess.run(
        [
            security,
            "find-generic-password",
            "-a",
            reference,
            "-s",
            SERVICE_NAME,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n") or None


def set_secret(reference: str, value: str) -> None:
    if not reference or not value:
        raise SecretStoreError("Thiếu mã tham chiếu hoặc giá trị secret")

    keyring = _keyring_module()
    if keyring is not None:
        try:
            keyring.set_password(SERVICE_NAME, reference, value)
            return
        except Exception:
            try:
                keyring.delete_password(SERVICE_NAME, reference)
            except Exception:
                pass
            _security_delete(reference)
            try:
                keyring.set_password(SERVICE_NAME, reference, value)
                return
            except Exception:
                pass

    if _security_write(reference, value):
        return

    try:
        _file_set(reference, value)
    except Exception as error:
        raise SecretStoreError(
            f"Không lưu được token Facebook (Keychain -25244). {error}"
        ) from error


def get_secret(reference: str) -> Optional[str]:
    if not reference:
        return None

    keyring = _keyring_module()
    if keyring is not None:
        try:
            found = keyring.get_password(SERVICE_NAME, reference)
            if found:
                return found
        except Exception:
            pass

    found = _security_read(reference)
    if found:
        return found
    return _file_get(reference)


def delete_secret(reference: str) -> None:
    if not reference:
        return

    keyring = _keyring_module()
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE_NAME, reference)
        except Exception:
            pass
    _security_delete(reference)
    _file_delete(reference)

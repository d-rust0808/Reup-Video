"""Store provider credentials outside SQLite and the Electron bundle."""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional


SERVICE_NAME = "com.reupvideo.studio"


class SecretStoreError(RuntimeError):
    pass


def _keyring_module():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def set_secret(reference: str, value: str) -> None:
    if not reference or not value:
        raise SecretStoreError("Secret reference and value are required")

    keyring = _keyring_module()
    if keyring is not None:
        keyring.set_password(SERVICE_NAME, reference, value)
        return

    security = shutil.which("security")
    if security:
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
        if result.returncode == 0:
            return
        raise SecretStoreError((result.stderr or "macOS Keychain write failed").strip())

    raise SecretStoreError("Install the 'keyring' package to store Facebook credentials securely")


def get_secret(reference: str) -> Optional[str]:
    if not reference:
        return None

    keyring = _keyring_module()
    if keyring is not None:
        return keyring.get_password(SERVICE_NAME, reference)

    security = shutil.which("security")
    if security:
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
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    return None


def delete_secret(reference: str) -> None:
    if not reference:
        return

    keyring = _keyring_module()
    if keyring is not None:
        try:
            keyring.delete_password(SERVICE_NAME, reference)
        except Exception:
            pass
        return

    security = shutil.which("security")
    if security:
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

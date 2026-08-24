"""Process lock preventing multiple backend workers from sharing one job database."""

import os


class BackendInstanceLock:
    def __init__(self, db_path: str):
        self.path = f"{os.path.abspath(db_path)}.backend.lock"
        self._file = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        lock_file = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if lock_file.tell() == os.path.getsize(self.path):
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            lock_file.close()
            return False

        self._file = lock_file
        return True

    def release(self) -> None:
        if not self._file:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

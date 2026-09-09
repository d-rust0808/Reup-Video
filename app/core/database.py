"""
SQLite Database Connection and Initialization Subsystem.
=========================================================
Target Path: app/core/database.py
"""

import os
import sqlite3
import logging
import sys
import time
from typing import Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/jobs.sqlite"

_T = TypeVar("_T")
_RETRY_DELAYS = (0.05, 0.15, 0.4, 0.8)
_RETRYABLE_FRAGMENTS = (
    "disk i/o error",
    "database is locked",
    "database table is locked",
    "unable to open database file",
    "locking protocol",
    "database schema is locked",
)
_conservative_cache: Dict[str, bool] = {}
_logged_modes = set()


def _is_retryable_sqlite_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _RETRYABLE_FRAGMENTS)


def _retry_sqlite(fn: Callable[..., _T], *args, **kwargs) -> _T:
    """Retry transient USB/lock I/O errors. Non-retryable errors raise immediately."""
    last: Optional[sqlite3.OperationalError] = None
    for index in range(len(_RETRY_DELAYS) + 1):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            last = exc
            if not _is_retryable_sqlite_error(exc) or index >= len(_RETRY_DELAYS):
                raise
            delay = _RETRY_DELAYS[index]
            logger.warning(
                "SQLite %s; retry %s/%s after %.2fs",
                exc,
                index + 1,
                len(_RETRY_DELAYS),
                delay,
            )
            time.sleep(delay)
    raise last  # pragma: no cover


def _mount_point(path: str) -> str:
    cur = os.path.abspath(path)
    if os.path.isfile(cur):
        cur = os.path.dirname(cur)
    while True:
        if os.path.ismount(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return cur
        cur = parent


def _windows_drive_is_unreliable(path: str) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return False
        dtype = ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\")
        # 2=DRIVE_REMOVABLE, 4=DRIVE_REMOTE, 5=DRIVE_CDROM
        return dtype in (2, 4, 5)
    except Exception:
        return False


def _detect_unreliable_volume(abs_path: str) -> bool:
    """USB / HFS+ / network mounts cannot host SQLite WAL+mmap reliably."""
    mount = _mount_point(abs_path)
    if sys.platform == "darwin" and mount.startswith("/Volumes/"):
        return True
    if mount.startswith(("/media/", "/run/media/", "/mnt/")):
        return True
    return _windows_drive_is_unreliable(abs_path)


def volume_needs_conservative_sqlite(db_path: str) -> bool:
    """True when WAL mmap is unsafe (USB, HFS+, network) or REUP_SQLITE_SAFE_IO=1."""
    flag = os.environ.get("REUP_SQLITE_SAFE_IO", "").strip().lower()
    if flag in {"1", "true", "yes"}:
        return True
    if flag in {"0", "false", "no"}:
        return False
    if not db_path or db_path == ":memory:":
        return False
    abs_path = os.path.abspath(db_path)
    cached = _conservative_cache.get(abs_path)
    if cached is not None:
        return cached
    result = _detect_unreliable_volume(abs_path)
    _conservative_cache[abs_path] = result
    return result


def _discard_empty_wal(db_path: str) -> None:
    """A 0-byte WAL has no header; SQLite then fails the next read with disk I/O error."""
    if not db_path or db_path == ":memory:":
        return
    abs_path = os.path.abspath(db_path)
    wal = abs_path + "-wal"
    shm = abs_path + "-shm"
    try:
        if os.path.isfile(wal) and os.path.getsize(wal) == 0:
            os.remove(wal)
            logger.warning("Removed empty SQLite WAL %s", wal)
            if os.path.isfile(shm):
                os.remove(shm)
    except OSError as exc:
        logger.warning("Could not recover empty WAL %s: %s", wal, exc)


def _cleanup_wal_sidecars(db_path: str) -> None:
    """Drop leftover WAL/SHM only when the WAL is already empty (headerless)."""
    if not db_path or db_path == ":memory:":
        return
    if not volume_needs_conservative_sqlite(db_path):
        return
    abs_path = os.path.abspath(db_path)
    wal = abs_path + "-wal"
    shm = abs_path + "-shm"
    try:
        wal_missing = not os.path.isfile(wal)
        wal_empty = wal_missing or os.path.getsize(wal) == 0
        if not wal_missing and wal_empty:
            os.remove(wal)
        if wal_empty and os.path.isfile(shm):
            os.remove(shm)
    except OSError as exc:
        logger.warning("Could not remove SQLite WAL sidecars for %s: %s", abs_path, exc)


def _prepare_sqlite_file(db_path: str) -> None:
    if not db_path or db_path == ":memory:":
        return
    _discard_empty_wal(db_path)


def _configure_connection(conn: sqlite3.Connection, db_path: str) -> None:
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA mmap_size=0;")
    conservative = volume_needs_conservative_sqlite(db_path)
    mode = "unknown"
    if conservative:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.OperationalError:
            pass
        try:
            row = conn.execute("PRAGMA journal_mode=DELETE;").fetchone()
            mode = str(row[0] if row else "delete")
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite DELETE journal switch failed: %s", exc)
        try:
            conn.execute("PRAGMA synchronous=FULL;")
        except sqlite3.OperationalError:
            pass
    else:
        try:
            row = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            mode = str(row[0] if row else "wal")
        except sqlite3.OperationalError:
            try:
                row = conn.execute("PRAGMA journal_mode=DELETE;").fetchone()
                mode = str(row[0] if row else "delete")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.OperationalError:
            pass
    key = os.path.abspath(db_path) if db_path and db_path != ":memory:" else ":memory:"
    if key not in _logged_modes:
        _logged_modes.add(key)
        logger.info(
            "SQLite ready at %s (journal_mode=%s, conservative_io=%s)",
            db_path,
            mode,
            conservative,
        )


class RetryingConnection(sqlite3.Connection):
    """sqlite3.Connection that retries transient disk I/O and lock errors."""

    def execute(self, *args, **kwargs):
        return _retry_sqlite(super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):
        return _retry_sqlite(super().executemany, *args, **kwargs)

    def commit(self):
        return _retry_sqlite(super().commit)


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Opens an SQLite connection with a busy timeout.

    WAL stays on local disks. USB / HFS+ / network volumes (and REUP_SQLITE_SAFE_IO=1)
    use DELETE journal + no mmap so concurrent UI polls do not raise disk I/O error.
    """
    if db_path != ":memory:":
        abs_path = os.path.abspath(db_path)
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _open() -> sqlite3.Connection:
        conn = sqlite3.connect(
            db_path,
            timeout=30.0,
            check_same_thread=False,
            factory=RetryingConnection,
        )
        conn.row_factory = sqlite3.Row
        try:
            _configure_connection(conn, db_path)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return conn

    return _retry_sqlite(_open)


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes the SQLite database schema and indices.
    """
    _prepare_sqlite_file(db_path)
    conn = get_db_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL DEFAULT '',
                platform TEXT DEFAULT 'auto',
                status TEXT NOT NULL DEFAULT 'PENDING',
                progress_percent REAL DEFAULT 0.0,
                input_file_path TEXT,
                output_file_path TEXT,
                error_message TEXT,
                message TEXT NOT NULL DEFAULT '',
                logs TEXT NOT NULL DEFAULT '[]',
                watermark_config TEXT NOT NULL DEFAULT '{}',
                reup_config TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Auto-migrate existing databases
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN message TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN logs TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN quality_status TEXT NOT NULL DEFAULT 'PENDING'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN quality_report TEXT NOT NULL DEFAULT '{}'")
        except Exception:
            pass

        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")

        # Channels Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'tiktok',
                handle TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT 'blue',
                overlays TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_platform ON channels(platform)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status)")
        try:
            conn.execute("ALTER TABLE channels ADD COLUMN overlays TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE channels ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT 'blue',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_group_members (
                group_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                PRIMARY KEY (group_id, channel_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_group_members_channel ON channel_group_members(channel_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS publish_log (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL DEFAULT '',
                channel_video_id TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                channel_name TEXT NOT NULL DEFAULT '',
                group_id TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                page_id TEXT NOT NULL DEFAULT '',
                page_name TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ASSIGNED',
                permalink TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_log_job ON publish_log(job_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_log_channel ON publish_log(channel_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_log_video ON publish_log(channel_video_id)")

        # Channel Videos Table (Content Management)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_videos (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                publish_status TEXT NOT NULL DEFAULT 'DRAFT',
                scheduled_at TEXT,
                published_at TEXT,
                video_path TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chan_vid_channel ON channel_videos(channel_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chan_vid_status ON channel_videos(publish_status)")

        # Facebook credentials stay in the OS credential store. SQLite only
        # keeps non-secret metadata and stable references to those secrets.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facebook_connections (
                id TEXT PRIMARY KEY,
                app_id TEXT NOT NULL DEFAULT '',
                graph_version TEXT NOT NULL DEFAULT 'v24.0',
                user_id TEXT NOT NULL DEFAULT '',
                user_name TEXT NOT NULL DEFAULT '',
                scopes TEXT NOT NULL DEFAULT '[]',
                token_expires_at TEXT,
                app_secret_ref TEXT NOT NULL DEFAULT '',
                user_token_ref TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'DISCONNECTED',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facebook_pages (
                page_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                tasks TEXT NOT NULL DEFAULT '[]',
                picture_url TEXT NOT NULL DEFAULT '',
                page_token_ref TEXT NOT NULL DEFAULT '',
                can_publish INTEGER NOT NULL DEFAULT 0,
                last_synced_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_facebook_pages_connection ON facebook_pages(connection_id)")
        for col, spec in (
            ("fan_count", "INTEGER NOT NULL DEFAULT 0"),
            ("followers_count", "INTEGER NOT NULL DEFAULT 0"),
            ("about", "TEXT NOT NULL DEFAULT ''"),
            ("username", "TEXT NOT NULL DEFAULT ''"),
            ("link", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE facebook_pages ADD COLUMN {col} {spec}")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_destinations (
                channel_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                destination_id TEXT NOT NULL,
                auto_publish INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (channel_id, provider)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_dest_provider ON channel_destinations(provider, destination_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS distribution_jobs (
                id TEXT PRIMARY KEY,
                channel_video_id TEXT NOT NULL,
                job_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL,
                destination_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                caption TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'PENDING',
                upload_phase TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TEXT,
                lease_until TEXT,
                upload_video_id TEXT NOT NULL DEFAULT '',
                upload_url TEXT NOT NULL DEFAULT '',
                remote_media_id TEXT NOT NULL DEFAULT '',
                remote_post_id TEXT NOT NULL DEFAULT '',
                permalink TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT,
                UNIQUE (channel_video_id, provider, destination_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_distribution_ready ON distribution_jobs(provider, status, next_attempt_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_distribution_video ON distribution_jobs(channel_video_id)")
        try:
            conn.execute(
                "ALTER TABLE distribution_jobs ADD COLUMN affiliate_comment_id TEXT NOT NULL DEFAULT ''"
            )
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tiktok_connections (
                id TEXT PRIMARY KEY,
                client_key TEXT NOT NULL DEFAULT '',
                redirect_uri TEXT NOT NULL DEFAULT '',
                client_secret_ref TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'DISCONNECTED',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tiktok_accounts (
                open_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                nickname TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                scopes TEXT NOT NULL DEFAULT '[]',
                privacy_options TEXT NOT NULL DEFAULT '[]',
                access_token_ref TEXT NOT NULL DEFAULT '',
                refresh_token_ref TEXT NOT NULL DEFAULT '',
                token_expires_at TEXT,
                can_publish INTEGER NOT NULL DEFAULT 0,
                last_synced_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tiktok_accounts_connection ON tiktok_accounts(connection_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS content_channels (
                channel_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'youtube',
                url TEXT NOT NULL DEFAULT '',
                handle TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                video_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_channels_platform ON content_channels(platform, handle)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS content_videos (
                id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                duration REAL NOT NULL DEFAULT 0,
                posted INTEGER NOT NULL DEFAULT 0,
                posted_at TEXT,
                published_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (channel_id, video_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_videos_channel ON content_videos(channel_id, posted)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_videos_native ON content_videos(video_id)")
        try:
            conn.execute("ALTER TABLE content_videos ADD COLUMN published_at TEXT")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS studio_state (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_growth_snapshots (
                channel_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                page_id TEXT NOT NULL DEFAULT '',
                followers INTEGER NOT NULL DEFAULT 0,
                fans INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                views INTEGER NOT NULL DEFAULT 0,
                posts INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'live',
                created_at TEXT NOT NULL,
                PRIMARY KEY (channel_id, snapshot_date)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_growth_snapshots_date "
            "ON channel_growth_snapshots(snapshot_date)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_growth_posts (
                post_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                page_id TEXT NOT NULL DEFAULT '',
                created_time TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                permalink TEXT NOT NULL DEFAULT '',
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                views INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_growth_posts_channel "
            "ON channel_growth_posts(channel_id, created_time)"
        )
        conn.commit()
    finally:
        conn.close()
    _cleanup_wal_sidecars(db_path)

    logger.info(f"Database initialized successfully at: {db_path}")


def checkpoint_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Flushes the WAL journal back into the main SQLite database file
    and truncates the WAL file.
    """
    try:
        with get_db_connection(db_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        logger.info(f"WAL checkpoint completed for: {db_path}")
    except Exception as e:
        logger.warning(f"WAL checkpoint failed for {db_path}: {e}")

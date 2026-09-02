"""
SQLite Database Connection and Initialization Subsystem.
=========================================================
Target Path: app/core/database.py
"""

import os
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/jobs.sqlite"


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Opens an SQLite connection with WAL mode and busy timeout.
    Creates parent directories if necessary.
    """
    if db_path != ":memory:":
        abs_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Configure WAL mode and a long busy timeout so a catalog sync is not
    # rolled back when the UI polls the same SQLite file.
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        pass  # In-memory databases do not support WAL mode
        
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """
    Initializes the SQLite database schema and indices.
    """
    with get_db_connection(db_path) as conn:
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

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
    
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    
    # Configure WAL mode and 5s busy timeout for high concurrency
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        pass  # In-memory databases do not support WAL mode
        
    conn.execute("PRAGMA busy_timeout=5000;")
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

        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at)")
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


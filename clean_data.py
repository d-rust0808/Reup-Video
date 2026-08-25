#!/usr/bin/env python3
"""
Maintenance utility to clean cache, temporary render files, and stale database locks.
"""

import os
import shutil
import glob
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

DIRS_TO_CLEAN = [
    ROOT_DIR / "data" / "cache",
    ROOT_DIR / "data" / "temp",
    ROOT_DIR / "data" / "previews",
    ROOT_DIR / "app" / "modules" / "tmp",
]

FILES_TO_CLEAN_PATTERNS = [
    str(ROOT_DIR / "data" / "*.lock"),
    str(ROOT_DIR / "data" / "*.sqlite-shm"),
    str(ROOT_DIR / "data" / "*.sqlite-wal"),
    str(ROOT_DIR / "app" / "modules" / "logs" / "*.log"),
]

def clean():
    print("🧹 Cleaning runtime temporary files and caches...")
    for d in DIRS_TO_CLEAN:
        if d.exists() and d.is_dir():
            for item in d.iterdir():
                if item.name == ".gitkeep":
                    continue
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception as e:
                    print(f"  Failed to delete {item}: {e}")

    for pattern in FILES_TO_CLEAN_PATTERNS:
        for fpath in glob.glob(pattern):
            try:
                os.remove(fpath)
                print(f"  Removed {fpath}")
            except Exception as e:
                print(f"  Failed to delete {fpath}: {e}")

    print("✨ Clean complete!")

if __name__ == "__main__":
    clean()

"""The homepage strip must show the most recently downloaded sources, not the seeded
demo clips, once real videos exist in RAW_INPUT_DIR."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api.extract import list_recent_sources
from app.config import settings
from app.services.sample_media import SAMPLE_IDS

# is_playable_mp4() requires >=20_000 bytes and "ftyp" in the first 64 bytes.
_FAKE_MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200_000


def _write_mp4(dirpath, name, mtime):
    path = os.path.join(dirpath, name)
    with open(path, "wb") as f:
        f.write(_FAKE_MP4)
    os.utime(path, (mtime, mtime))
    return path


def test_recent_sources_prefers_real_downloads_over_samples(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))

    now = time.time()
    for sid in SAMPLE_IDS:
        _write_mp4(str(raw), f"{sid}.mp4", now - 5000)
    # Real downloads use numeric douyin-style ids; newest first.
    _write_mp4(str(raw), "7412345678901234567.mp4", now - 10)
    _write_mp4(str(raw), "7498765432109876543.mp4", now - 1)

    res = asyncio.run(list_recent_sources())
    ids = [i["video_id"] for i in res["items"]]

    assert ids == ["7498765432109876543", "7412345678901234567"]
    assert all(i["is_sample"] is False for i in res["items"])


def test_recent_sources_falls_back_to_samples_when_library_empty(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))

    now = time.time()
    for sid in SAMPLE_IDS:
        _write_mp4(str(raw), f"{sid}.mp4", now)

    res = asyncio.run(list_recent_sources())

    assert res["count"] == 3
    assert all(i["is_sample"] is True for i in res["items"])
    # Titles stay human-readable rather than the raw stem from /library.
    assert any("Mẫu Douyin" in i["title"] for i in res["items"])


def test_recent_sources_respects_limit(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))

    now = time.time()
    for idx in range(5):
        _write_mp4(str(raw), f"741234567890123456{idx}.mp4", now - idx)

    assert asyncio.run(list_recent_sources())["count"] == 3
    assert asyncio.run(list_recent_sources(limit=2))["count"] == 2


def test_recent_sources_empty_dir(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))

    res = asyncio.run(list_recent_sources())
    assert res == {"items": [], "count": 0}

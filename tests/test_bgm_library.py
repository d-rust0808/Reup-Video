
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.services.bgm_library import harvest_bgm, import_audio_file, load_catalog, resolve_bgm, delete_bgm, bgm_dir
from app.models.job import ReupConfig


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_harvest_bgm_from_synthetic_clip(monkeypatch, tmp_path):
    monkeypatch.setenv("BGM_DIR", str(tmp_path / "bgm"))
    from app.config import settings
    settings.BGM_DIR = str(tmp_path / "bgm")
    ffmpeg = shutil.which("ffmpeg")
    vid = tmp_path / "src.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=1.0",
         "-f", "lavfi", "-i", "color=c=black:s=160x160:d=1.0",
         "-shortest", "-pix_fmt", "yuv420p", str(vid)],
        check=True,
    )
    item = harvest_bgm(str(vid), title="Sine test")
    assert item["id"].startswith("bgm_")
    assert os.path.isfile(item["path"])
    assert item["duration"] >= 0.5
    assert resolve_bgm(item["id"]) == item["path"]
    assert any(x["id"] == item["id"] for x in load_catalog())
    assert delete_bgm(item["id"]) is True


def test_reup_config_bgm_fields():
    cfg = ReupConfig(bgm_path="data/bgm/x.mp3", bgm_volume=0.7)
    assert cfg.bgm_path.endswith("x.mp3")
    assert abs(cfg.bgm_volume - 0.7) < 1e-6


# --- online BGM providers (no network: providers are stubbed) ---


def test_license_code_from_url():
    from app.services.bgm_providers import _license_code_from_url

    assert _license_code_from_url("https://creativecommons.org/licenses/by-sa/3.0/") == "by-sa"
    assert _license_code_from_url("https://creativecommons.org/licenses/by-nc-nd/3.0/") == "by-nc-nd"
    assert _license_code_from_url("https://creativecommons.org/publicdomain/zero/1.0/") == "cc0"
    assert _license_code_from_url("") == ""


def test_is_commercial_safe():
    from app.services.bgm_providers import is_commercial_safe

    assert is_commercial_safe("by-sa") is True
    assert is_commercial_safe("CC0") is True
    assert is_commercial_safe("by-nc-nd") is False
    assert is_commercial_safe("by-nd") is False
    assert is_commercial_safe("") is False


def test_available_providers_jamendo_needs_key(monkeypatch):
    from app.config import settings
    from app.services import bgm_providers

    monkeypatch.setattr(settings, "JAMENDO_CLIENT_ID", "", raising=False)
    by_id = {p["id"]: p for p in bgm_providers.available_providers()}
    assert by_id["openverse"]["ready"] is True
    assert by_id["jamendo"]["ready"] is False

    monkeypatch.setattr(settings, "JAMENDO_CLIENT_ID", "fake-id", raising=False)
    by_id = {p["id"]: p for p in bgm_providers.available_providers()}
    assert by_id["jamendo"]["ready"] is True


def test_search_tracks_rejects_empty_and_unknown_provider():
    from app.services.bgm_providers import search_tracks

    with pytest.raises(ValueError):
        search_tracks("   ")
    with pytest.raises(ValueError):
        search_tracks("piano", provider="spotify")


def _fake_row(track_id="x1", duration=120.0, provider="openverse"):
    return {
        "provider": provider,
        "external_id": track_id,
        "title": f"Track {track_id}",
        "artist": "Someone",
        "duration": duration,
        "license": "by-sa",
        "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
        "attribution": "attrib",
        "audio_url": f"https://example.test/{track_id}.mp3",
        "page_url": "https://example.test/page",
        "tags": ["instrumental"],
        "instrumental": True,
        "source_platform": provider,
    }


def test_search_tracks_duration_filter(monkeypatch):
    from app.services import bgm_providers

    rows = [_fake_row("short", 30.0), _fake_row("mid", 120.0), _fake_row("long", 600.0)]
    monkeypatch.setitem(bgm_providers.PROVIDER_FUNCS, "openverse", lambda q, **kw: list(rows))

    res = bgm_providers.search_tracks("x", provider="openverse", min_duration=60, max_duration=300)
    assert [i["external_id"] for i in res["items"]] == ["mid"]
    assert res["count"] == 1
    assert res["errors"] == {}


def test_search_tracks_collects_provider_errors(monkeypatch):
    from app.services import bgm_providers

    def boom(q, **kw):
        raise RuntimeError("nope")

    monkeypatch.setitem(bgm_providers.PROVIDER_FUNCS, "openverse", boom)
    res = bgm_providers.search_tracks("x", provider="openverse")
    assert res["count"] == 0
    assert "nope" in res["errors"]["openverse"]


def test_download_track_audio_rejects_bad_url(tmp_path):
    from app.services.bgm_providers import download_track_audio

    with pytest.raises(ValueError):
        download_track_audio("ftp://example.test/a.mp3", str(tmp_path / "out.bin"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_import_remote_track_persists_license(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import bgm_library

    settings.BGM_DIR = str(tmp_path / "bgm")
    ffmpeg = shutil.which("ffmpeg")
    src = tmp_path / "tone.mp3"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0", str(src)],
        check=True,
    )

    def fake_download(audio_url, dest_path, max_bytes=0):
        shutil.copyfile(src, dest_path)
        return os.path.getsize(dest_path)

    monkeypatch.setattr("app.services.bgm_providers.download_track_audio", fake_download)

    item = bgm_library.import_remote_track(_fake_row("abc123"))
    assert item["method"] == "openverse"
    assert item["license"] == "by-sa"
    assert item["external_id"] == "abc123"
    assert item["attribution"] == "attrib"
    assert item["source_url"] == "https://example.test/page"
    assert "Someone" in item["title"]
    assert os.path.isfile(item["path"])
    assert bgm_library.resolve_bgm(item["id"]) == item["path"]
    assert bgm_library.delete_bgm(item["id"]) is True


def test_import_remote_track_requires_audio_url():
    from app.services.bgm_library import import_remote_track

    with pytest.raises(ValueError):
        import_remote_track({"title": "no url"})

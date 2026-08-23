
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

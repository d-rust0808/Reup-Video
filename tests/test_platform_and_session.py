"""Studio session compacting + multi-platform export helpers."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import ReupConfig
from app.services.platform_export import fit_vf, normalize_platforms, PRESETS


def test_normalize_platforms_aliases():
    assert normalize_platforms(["TikTok", "yt", "fb", "junk"]) == [
        "tiktok",
        "youtube_shorts",
        "facebook",
    ]
    assert normalize_platforms([]) == []
    assert "youtube" in PRESETS


def test_fit_vf_is_contain_pad():
    vf = fit_vf(1080, 1920)
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920" in vf


def test_reup_config_default_platforms():
    cfg = ReupConfig()
    assert "tiktok" in cfg.target_platforms
    assert "youtube_shorts" in cfg.target_platforms

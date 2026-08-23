"""Studio session compacting + multi-platform export helpers + frame PNG."""

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


def test_frame_png_has_alpha_ring():
    from app.services.frame_studio import render_frame_png, PRESETS as FRAMES
    import cv2

    path = render_frame_png("gold", out_name="test_gold.png")
    assert os.path.exists(path)
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    assert img is not None and img.shape[2] == 4
    h, w = img.shape[:2]
    assert img[0, 0, 3] > 200
    assert img[h // 2, w // 2, 3] < 10
    assert any(p["id"] == "cinema" for p in FRAMES)

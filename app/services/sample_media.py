"""
Douyin-style sample clip generator.
Produces a playable H.264/AAC source with logo, Chinese hardsub, speech + BGM.
"""

from __future__ import annotations

import os
import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

ZH_SCRIPT = "注意看，这个男人叫小帅。他每天在抖音分享生活，没想到突然火了。"
VI_SCRIPT = (
    "Chú ý xem, người đàn ông này tên Tiểu Soái. "
    "Anh ấy chia sẻ cuộc sống trên Douyin mỗi ngày và bất ngờ nổi tiếng."
)
ZH_CAPTION = "注意看 这个男人叫小帅"
LOGO_TEXT = "抖音 @xiaoshuai"

SAMPLE_IDS = ("douyin_123", "kuaishou_456", "xiaohongshu_789")


def _ffmpeg() -> Optional[str]:
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in ("/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _cjk_font() -> str:
    for p in (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(p):
            return p
    return ""


def _run(cmd, timeout=60) -> bool:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        if res.returncode != 0:
            logger.warning("cmd failed (%s): %s", res.returncode, (res.stderr or "")[-400:])
            return False
        return True
    except Exception as e:
        logger.warning("cmd exception: %s", e)
        return False


def _edge_tts_to_mp3(text: str, voice: str, dest: str) -> bool:
    try:
        import asyncio
        import edge_tts

        async def _gen():
            communicator = edge_tts.Communicate(text, voice)
            await communicator.save(dest)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import threading

                err = []

                def _run_thread():
                    try:
                        asyncio.run(_gen())
                    except Exception as e:
                        err.append(e)

                t = threading.Thread(target=_run_thread)
                t.start()
                t.join(timeout=45)
                if err:
                    raise err[0]
            else:
                loop.run_until_complete(_gen())
        except RuntimeError:
            asyncio.run(_gen())
        return os.path.exists(dest) and os.path.getsize(dest) > 2048
    except Exception as e:
        logger.warning("edge-tts failed (%s): %s", voice, e)
        return False


def write_vietnamese_srt(dest: str, duration: float = 8.0) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    end = max(4.0, duration - 0.15)
    content = (
        "1\n00:00:00,200 --> 00:00:04,800\n"
        "Đêm đầu tiên ở chung với bạn, trời sấm chớp à?\n\n"
        "2\n00:00:04,800 --> "
        f"00:00:{int(end):02d},{int((end % 1) * 1000):03d}\n"
        "Mèo AI dễ thương, chữa lành từng khoảnh khắc.\n"
    )
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    return dest


def generate_vi_voice(dest_mp3: str, text: Optional[str] = None) -> bool:
    return _edge_tts_to_mp3(text or VI_SCRIPT, "vi-VN-NamMinhNeural", dest_mp3)


def generate_douyin_sample(output_path: str, duration: float = 8.0) -> bool:
    """Render a Douyin-like MP4 (logo + Chinese hardsub + speech + BGM)."""
    ffmpeg_bin = _ffmpeg()
    if not ffmpeg_bin:
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    work = os.path.dirname(os.path.abspath(output_path))
    stem = os.path.splitext(os.path.basename(output_path))[0]
    zh_mp3 = os.path.join(work, f"{stem}.zh.mp3")
    caption_file = os.path.join(work, f"{stem}.caption.txt")
    logo_file = os.path.join(work, f"{stem}.logo.txt")
    with open(caption_file, "w", encoding="utf-8") as f:
        f.write(ZH_CAPTION)
    with open(logo_file, "w", encoding="utf-8") as f:
        f.write(LOGO_TEXT)

    has_speech = _edge_tts_to_mp3(ZH_SCRIPT, "zh-CN-YunxiNeural", zh_mp3)
    font = _cjk_font()
    font_opt = f"fontfile={font}:" if font else ""

    dur = f"{duration:.2f}"
    vf = (
        f"format=yuv420p,"
        f"drawbox=x=iw-280:y=36:w=250:h=64:color=white@0.88:t=fill,"
        f"drawtext={font_opt}textfile='{logo_file}':x=iw-268:y=54:fontsize=22:fontcolor=0x111827,"
        f"drawbox=x=0:y=ih-118:w=iw:h=118:color=black@0.58:t=fill,"
        f"drawtext={font_opt}textfile='{caption_file}':x=(w-text_w)/2:y=h-78:"
        f"fontsize=36:fontcolor=white:borderw=2:bordercolor=black"
    )

    cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", f"color=c=0x0f172a:s=1280x720:r=30:d={dur}"]
    if has_speech:
        cmd += ["-i", zh_mp3]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency=196:duration={dur}"]

    if has_speech:
        fc = (
            f"[0:v]{vf}[v];"
            f"[2:a]volume=0.10[bg];"
            f"[1:a]volume=1.15[vo];"
            f"[bg][vo]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        cmd += [
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
            "-t", dur,
        ]
    else:
        cmd += [
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", dur,
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "4.0",
        "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        output_path,
    ]
    ok = _run(cmd, timeout=90)
    if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 20_000:
        logger.info("Seeded Douyin-style sample: %s (%s bytes)", output_path, os.path.getsize(output_path))
        write_vietnamese_srt(os.path.splitext(output_path)[0] + ".vi.srt", duration)
        return True
    return False


def is_playable_mp4(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) < 20_000:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(64)
        return b"ftyp" in header and b"END_OF_MP4_SAMPLE" not in header
    except OSError:
        return False


def seed_sample_videos(raw_input_dir: str, force: bool = False) -> None:
    """Ensure the three studio sample clips are real playable MP4s."""
    os.makedirs(raw_input_dir, exist_ok=True)
    ffmpeg_bin = _ffmpeg()
    primary = os.path.join(raw_input_dir, "douyin_123.mp4")

    real_src = None
    try:
        for fname in os.listdir(raw_input_dir):
            if not fname.endswith(".mp4"):
                continue
            if fname in {f"{sid}.mp4" for sid in SAMPLE_IDS}:
                continue
            cand = os.path.join(raw_input_dir, fname)
            if is_playable_mp4(cand) and os.path.getsize(cand) > 1_000_000:
                real_src = cand
                break
    except OSError:
        real_src = None

    need_primary = force or not is_playable_mp4(primary) or os.path.getsize(primary) < 400_000
    if need_primary and real_src and ffmpeg_bin:
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", real_src, "-t", "12",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "4.0",
            "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            primary,
        ]
        if _run(cmd, timeout=60) and is_playable_mp4(primary):
            write_vietnamese_srt(os.path.splitext(primary)[0] + ".vi.srt", 12.0)
            need_primary = False
            logger.info("Seeded sample from real Douyin clip: %s", real_src)

    if need_primary:
        if not generate_douyin_sample(primary, duration=8.0):
            if ffmpeg_bin:
                cmd = [
                    ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=duration=6:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                    "-profile:v", "main", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    primary,
                ]
                _run(cmd, timeout=40)

    if is_playable_mp4(primary):
        for sid in SAMPLE_IDS[1:]:
            dest = os.path.join(raw_input_dir, f"{sid}.mp4")
            if force or not is_playable_mp4(dest) or os.path.getsize(dest) < 400_000:
                try:
                    shutil.copy2(primary, dest)
                    srt_src = os.path.splitext(primary)[0] + ".vi.srt"
                    srt_dst = os.path.splitext(dest)[0] + ".vi.srt"
                    if os.path.exists(srt_src):
                        shutil.copy2(srt_src, srt_dst)
                except OSError:
                    pass

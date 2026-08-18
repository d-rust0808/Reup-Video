"""
MD5 Modification & Metadata Injection Engine.
==============================================
Target Path: app/services/md5_service.py
"""

import os
import hashlib
import shutil
import subprocess
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def calculate_file_md5(file_path: str, chunk_size: int = 65536) -> str:
    """Computes MD5 checksum of a file in binary chunks."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found for MD5 calculation: {file_path}")
    if os.path.getsize(file_path) == 0:
        raise ValueError("File is empty")

    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def modify_video_md5_fast(
    file_path: str,
    num_bytes: int = 64,
    compute_hash: bool = False
) -> Dict[str, Any]:
    """
    Appends random noise bytes (os.urandom) to binary trailer (<1ms execution).
    Alters binary hash instantaneously without breaking MP4 container atom structure.

    Args:
        file_path: Absolute or relative file path.
        num_bytes: Number of random noise bytes to append (default 64).
        compute_hash: If True, computes original and new MD5 via full file scan. Default False for instant O(1) time.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Target file not found: {file_path}")
    if os.path.getsize(file_path) == 0:
        raise ValueError("File is empty")

    orig_md5 = calculate_file_md5(file_path) if compute_hash else None

    random_bytes = b"_REUP_MODIFIED_TRAILER_BYTE_PAD" + os.urandom(num_bytes)
    with open(file_path, "ab") as f:
        f.write(random_bytes)

    new_md5 = calculate_file_md5(file_path) if compute_hash else None
    if compute_hash:
        logger.info(f"Fast MD5 modification completed for {file_path}: {orig_md5} -> {new_md5}")

    return {
        "status": "success",
        "original_md5": orig_md5,
        "new_md5": new_md5,
        "bytes_added": num_bytes,
        "method": "trailer_padding",
        "file_path": file_path
    }


def modify_md5(file_path: str, compute_hash: bool = True) -> str:
    """
    Appends random noise to alter MD5 hash and returns new MD5 string.
    Maintains compatibility with tests and direct helper callers.
    """
    res = modify_video_md5_fast(file_path, compute_hash=compute_hash)
    if res["new_md5"] is not None:
        return res["new_md5"]
    return calculate_file_md5(file_path)


# Alias for backward compatibility
modify_file_md5 = modify_md5


def modify_video_metadata(
    input_path: str,
    output_path: str,
    title: str = "Reup_Content",
    comment: str = "Rendered_v2",
    ffmpeg_bin: Optional[str] = None
) -> Dict[str, Any]:
    """
    Injects custom container metadata tags using FFmpeg stream remuxing (-c copy).
    Alters binary hash by rewriting MP4 header metadata atoms.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if os.path.getsize(input_path) == 0:
        raise ValueError("Input file is empty")

    resolved_ffmpeg = ffmpeg_bin or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not shutil.which(resolved_ffmpeg) and not os.path.exists(resolved_ffmpeg):
        raise RuntimeError("FFmpeg binary not found on system for metadata injection.")

    orig_md5 = calculate_file_md5(input_path)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cmd = [
        resolved_ffmpeg, "-y", "-i", input_path,
        "-c", "copy",
        "-metadata", f"title={title}",
        "-metadata", f"comment={comment}",
        "-metadata", "encoder=ReupVideoEngine_v1",
        output_path
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg metadata injection failed: {e.stderr}")
        raise RuntimeError(f"FFmpeg metadata injection failed: {e.stderr}") from e

    new_md5 = calculate_file_md5(output_path)
    return {
        "status": "success",
        "original_md5": orig_md5,
        "new_md5": new_md5,
        "method": "metadata_remux",
        "output_path": output_path
    }

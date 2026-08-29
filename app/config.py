"""
Application Configuration and Path Settings Module.
===================================================
Target Path: app/config.py
"""

import os
import json
import sys
from pathlib import Path
from typing import List, Union
from pydantic import BaseModel, Field


def _load_env_file():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k_str = k.strip()
                    v_str = v.strip()
                    val = os.environ.get(k_str)
                    if not val or val == "sk-...":
                        os.environ[k_str] = v_str

_load_env_file()

_reup_root = os.environ.get("REUP_ROOT")
if _reup_root and os.path.isdir(_reup_root):
    try:
        os.chdir(_reup_root)
    except OSError:
        pass


_DEEPSEEK_LEGACY_MODELS = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
    "deepseek-coder": "deepseek-v4-flash",
}


def normalize_deepseek_model(name: str) -> str:
    """Map retired DeepSeek ids onto the current v4 catalog."""
    raw = (name or "").strip() or "deepseek-v4-flash"
    return _DEEPSEEK_LEGACY_MODELS.get(raw.lower(), raw)


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return max(1, int(fallback))
    return max(1, int(raw))


def _cpu_count() -> int:
    return os.cpu_count() or 4


def _ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024 ** 3)
    except Exception:
        return 8.0


def _auto_max_concurrent_jobs() -> int:
    """56-thread / 64 GB workstation -> 16. Smaller PCs stay conservative."""
    cpus = _cpu_count()
    ram = _ram_gb()
    if cpus >= 48 and ram >= 48:
        return 16
    if cpus >= 16 and ram >= 16:
        return 8
    return max(2, min(4, cpus // 4 or 2))


def _auto_gpu_concurrency() -> int:
    """Keep 1 on 4 GB GPUs such as GTX 1050 Ti. Never raise this."""
    return 1


def _auto_onnx_threads() -> int:
    cpus = _cpu_count()
    if cpus >= 48:
        return 16
    return max(1, min(8, max(4, cpus // 7)))


def _auto_tts_concurrency() -> int:
    """10 VieNeu instances on a 56-thread / 64 GB server; conservative elsewhere."""
    cpus = _cpu_count()
    ram = _ram_gb()
    if cpus >= 48 and ram >= 48:
        return 10
    if cpus >= 16 and ram >= 16:
        return 2
    return 1


def _auto_tts_onnx_threads() -> int:
    cpus = _cpu_count()
    if cpus >= 48:
        return 6
    return max(1, min(8, max(4, cpus // 7)))


def _auto_stt_cpu_threads() -> int:
    cpus = _cpu_count()
    if cpus >= 48:
        return 16
    return max(1, min(8, max(4, cpus // 7)))


def _auto_stt_workers() -> int:
    cpus = _cpu_count()
    ram = _ram_gb()
    if cpus >= 48 and ram >= 48:
        return 4
    if ram >= 32:
        return 2
    return 1


class Settings(BaseModel):
    """System-wide configuration settings with environment variable fallbacks."""

    BASE_DIR: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent,
        description="Project root directory path"
    )
    DEEPSEEK_API_KEY: str = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""),
        description="DeepSeek LLM API Key"
    )
    DEEPSEEK_BASE_URL: str = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        description="DeepSeek Base URL"
    )
    DEEPSEEK_MODEL: str = Field(
        default_factory=lambda: normalize_deepseek_model(os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")),
        description="DeepSeek Model Name"
    )
    SUBTITLE_TRANSLATOR: str = Field(
        default_factory=lambda: (os.getenv("SUBTITLE_TRANSLATOR", "agy") or "agy").strip().lower(),
        description="Subtitle translator: agy only (Google Antigravity CLI). Other values are ignored.",
    )
    RAW_INPUT_DIR: str = Field(
        default_factory=lambda: os.getenv("RAW_INPUT_DIR", "data/input/raw"),
        description="Directory for raw downloaded videos"
    )
    OUTPUT_DIR: str = Field(
        default_factory=lambda: os.getenv("OUTPUT_DIR", "data/outputs"),
        description="Directory for processed reup output videos"
    )
    TTS_OUTPUT_DIR: str = Field(
        default_factory=lambda: os.getenv("TTS_OUTPUT_DIR", "data/outputs/tts"),
        description="Directory for generated TTS audio files"
    )
    MODELS_DIR: str = Field(
        default_factory=lambda: os.getenv("MODELS_DIR", "data/models"),
        description="Directory for AI weights and model artifacts"
    )
    CACHE_DIR: str = Field(
        default_factory=lambda: os.getenv("CACHE_DIR", "data/cache"),
        description="Directory for processing caches"
    )
    TEMP_DIR: str = Field(
        default_factory=lambda: os.getenv("TEMP_DIR", "data/temp"),
        description="Directory for temporary processing files"
    )
    PREVIEW_DIR: str = Field(
        default_factory=lambda: os.getenv("PREVIEW_DIR", "data/previews"),
        description="Directory for generated preview frame images"
    )
    CHANNELS_DIR: str = Field(
        default_factory=lambda: os.getenv("CHANNELS_DIR", "data/channels"),
        description="Directory for channel branding overlays (logos, frames)"
    )
    BGM_DIR: str = Field(
        default_factory=lambda: os.getenv("BGM_DIR", "data/bgm"),
        description="Directory for harvested background-music library"
    )
    JAMENDO_CLIENT_ID: str = Field(
        default_factory=lambda: os.getenv("JAMENDO_CLIENT_ID", ""),
        description="Jamendo API client_id for online BGM search (devportal.jamendo.com)"
    )
    DB_PATH: str = Field(
        default_factory=lambda: os.getenv("DB_PATH", os.getenv("DATABASE_PATH", "data/jobs.sqlite")),
        description="SQLite database file path"
    )
    HOST: str = Field(
        default_factory=lambda: os.getenv("HOST", "0.0.0.0"),
        description="FastAPI server bind host"
    )
    PORT: int = Field(
        default_factory=lambda: int(os.getenv("PORT", "6000")),
        description="FastAPI server bind port"
    )
    DEBUG: bool = Field(
        default_factory=lambda: os.getenv("DEBUG", "false").lower() in ("true", "1", "yes"),
        description="Debug mode flag"
    )
    CORS_ORIGINS: Union[List[str], str] = Field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "*"),
        description="Allowed CORS origins list or JSON/comma-separated string"
    )
    MAX_CONCURRENT_JOBS: int = Field(
        default_factory=lambda: _env_int("MAX_CONCURRENT_JOBS", _auto_max_concurrent_jobs()),
        description="Maximum concurrent video processing tasks. Keep this below the physical CPU capacity when GPU AI is enabled."
    )
    GPU_CONCURRENCY: int = Field(
        default_factory=lambda: _env_int("GPU_CONCURRENCY", _auto_gpu_concurrency()),
        description="Maximum concurrent GPU-heavy AI tasks; 1 is safest for 4 GB GPUs"
    )
    ONNX_INTRA_OP_THREADS: int = Field(
        default_factory=lambda: _env_int("ONNX_INTRA_OP_THREADS", _auto_onnx_threads()),
        description="CPU threads used by ONNX Runtime inpainting"
    )
    TTS_CONCURRENCY: int = Field(
        default_factory=lambda: _env_int("TTS_CONCURRENCY", _auto_tts_concurrency()),
        description="Number of independent local VieNeu models used for parallel TTS"
    )
    TTS_ONNX_THREADS: int = Field(
        default_factory=lambda: _env_int("TTS_ONNX_THREADS", _auto_tts_onnx_threads()),
        description="ONNX CPU threads allocated to each VieNeu TTS model"
    )
    STT_CPU_THREADS: int = Field(
        default_factory=lambda: _env_int("STT_CPU_THREADS", _auto_stt_cpu_threads()),
        description="CPU threads used by faster-whisper when CUDA is unavailable"
    )
    STT_WORKERS: int = Field(
        default_factory=lambda: _env_int("STT_WORKERS", _auto_stt_workers()),
        description="Parallel faster-whisper inference workers"
    )


    def get_cors_origins(self) -> List[str]:
        """Parses CORS_ORIGINS into a clean list of string origins."""
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        if isinstance(self.CORS_ORIGINS, str):
            s = self.CORS_ORIGINS.strip()
            if s.startswith("[") and s.endswith("]"):
                try:
                    return json.loads(s)
                except Exception:
                    pass
            if s == "*":
                return [
                    "http://127.0.0.1:6000",
                    "http://127.0.0.1:6001",
                    "http://localhost:6000",
                    "http://localhost:6001",
                    "null",
                ]
            return [item.strip() for item in s.split(",") if item.strip()]
        return ["*"]

    def ensure_directories(self) -> None:
        """Ensures all required storage and runtime directories exist on disk."""
        dirs_to_create = [
            self.RAW_INPUT_DIR,
            self.OUTPUT_DIR,
            self.TTS_OUTPUT_DIR,
            self.MODELS_DIR,
            self.CACHE_DIR,
            self.TEMP_DIR,
            self.PREVIEW_DIR,
            self.CHANNELS_DIR,
            os.path.dirname(os.path.abspath(self.DB_PATH))
        ]
        for d in dirs_to_create:
            if d:
                os.makedirs(d, exist_ok=True)


# Global Singleton Instance
settings = Settings()

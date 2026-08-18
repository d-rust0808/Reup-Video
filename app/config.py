"""
Application Configuration and Path Settings Module.
===================================================
Target Path: app/config.py
"""

import os
import json
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
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()

_load_env_file()


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
        default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        description="DeepSeek Model Name"
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
    DB_PATH: str = Field(
        default_factory=lambda: os.getenv("DB_PATH", os.getenv("DATABASE_PATH", "data/jobs.sqlite")),
        description="SQLite database file path"
    )
    HOST: str = Field(
        default_factory=lambda: os.getenv("HOST", "0.0.0.0"),
        description="FastAPI server bind host"
    )
    PORT: int = Field(
        default_factory=lambda: int(os.getenv("PORT", "8000")),
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
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_JOBS", "2")),
        description="Maximum concurrent video processing tasks"
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
                return ["*"]
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
            os.path.dirname(os.path.abspath(self.DB_PATH))
        ]
        for d in dirs_to_create:
            if d:
                os.makedirs(d, exist_ok=True)


# Global Singleton Instance
settings = Settings()

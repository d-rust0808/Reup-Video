"""Google Antigravity CLI (agy) wrapper — local Gemini, no DeepSeek API billing."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gemini-3.7-flash-high"
_CUE_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "required": ["index", "text"],
            },
        }
    },
    "required": ["lines"],
}


def _usable_bin(path: Optional[str]) -> bool:
    if not path:
        return False
    return os.path.isfile(path)


def resolve_agy_bin() -> Optional[str]:
    """Find `agy` on macOS or Windows. Ignore AGY_BIN if that path does not exist."""
    configured = (os.getenv("AGY_BIN") or "").strip().strip('"')
    if _usable_bin(configured):
        return configured

    for name in ("agy", "agy.exe", "agy.cmd"):
        found = shutil.which(name)
        if _usable_bin(found):
            return found

    home = Path.home()
    local_app = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    candidates = [
        home / ".local" / "bin" / "agy",
        home / ".local" / "bin" / "agy.exe",
        home / ".local" / "bin" / "agy.cmd",
        Path(local_app) / "agy" / "agy.exe",
        Path(local_app) / "Programs" / "agy" / "agy.exe",
        Path(local_app) / "Google" / "Antigravity" / "agy.exe",
        Path(local_app) / "Antigravity" / "cli" / "agy.exe",
        home / "AppData" / "Roaming" / "npm" / "agy.cmd",
        home / "AppData" / "Roaming" / "npm" / "agy.exe",
    ]
    for candidate in candidates:
        path = str(candidate)
        if _usable_bin(path):
            return path
    return None


def is_available() -> bool:
    return bool(resolve_agy_bin())


def default_model() -> str:
    return (os.getenv("AGY_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _model_and_effort(model: Optional[str]) -> tuple[str, Optional[str]]:
    """agy requires --effort when the slug is a bare flash/pro family name."""
    raw = (model or default_model()).strip()
    lower = raw.lower()
    for effort in ("high", "medium", "low"):
        suffix = f"-{effort}"
        if lower.endswith(suffix):
            return raw[: -len(suffix)], effort
    if re.search(r"flash$|pro$", lower):
        return raw, "high"
    return raw, None


def _parse_envelope(stdout: str) -> Dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("agy CLI returned empty stdout")
    # Print mode JSON is one object; ignore any leading log noise.
    last_obj = None
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx >= len(text):
            break
        if text[idx] != "{":
            nl = text.find("\n", idx)
            idx = len(text) if nl < 0 else nl + 1
            continue
        obj, end = decoder.raw_decode(text, idx)
        last_obj = obj
        idx = end
    if not isinstance(last_obj, dict):
        raise RuntimeError("agy CLI stdout was not JSON")
    return last_obj


_MCP_LOCK = threading.Lock()
_EMPTY_MCP = '{"mcpServers":{}}\n'


def _mcp_config_path() -> Path:
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def _settings_path() -> Path:
    return Path.home() / ".gemini" / "settings.json"


def _backup_for(path: Path) -> Path:
    return path.with_name(path.name + ".reup-backup")


def _config_targets() -> List[Path]:
    return [_mcp_config_path(), _settings_path()]


def _empty_mcp_bytes(path: Path, original: Optional[bytes]) -> Optional[bytes]:
    """Return a patched config that has no MCP servers, or None if this file should be left alone."""
    if path.name == "mcp_config.json":
        return _EMPTY_MCP.encode("utf-8")
    if original is None:
        return None
    try:
        data = json.loads(original.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or "mcpServers" not in data:
        return None
    patched = dict(data)
    patched["mcpServers"] = {}
    return (json.dumps(patched, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _restore_mcp_backup() -> None:
    for path in _config_targets():
        bak = _backup_for(path)
        if not bak.is_file():
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bak.read_bytes())
            bak.unlink()
            logger.info("Restored %s from Reup backup", path)
        except OSError as exc:
            logger.warning("Could not restore MCP config backup %s: %s", path, exc)


@contextmanager
def _silence_user_mcp():
    """Temporarily empty global MCP config so print-mode is not blocked by hung servers.

    Interactive `agy` keeps the user's gpm-mcp / IDE servers; this only wraps one
    headless translation process. Both `mcp_config.json` and `settings.json` are
    silenced because print-mode waits until every configured server connects.
    """
    _restore_mcp_backup()
    with _MCP_LOCK:
        snapshots: List[Tuple[Path, Optional[bytes], bool]] = []
        try:
            for path in _config_targets():
                original = path.read_bytes() if path.is_file() else None
                patch = _empty_mcp_bytes(path, original)
                if patch is None:
                    snapshots.append((path, original, False))
                    continue
                if original is not None:
                    _backup_for(path).write_bytes(original)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(patch)
                snapshots.append((path, original, True))
            yield
        finally:
            for path, original, wrote in snapshots:
                if not wrote:
                    continue
                try:
                    if original is None:
                        if path.is_file():
                            path.unlink()
                    else:
                        path.write_bytes(original)
                    bak = _backup_for(path)
                    if bak.is_file():
                        bak.unlink()
                except OSError as exc:
                    logger.warning("Could not restore MCP config %s: %s", path, exc)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _agy_env() -> Dict[str, str]:
    env = {**os.environ, "TERM": os.environ.get("TERM") or "dumb"}
    env["ANTIGRAVITY_BROWSER_TOOLS_ENABLED"] = "false"
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
    return env


def _agy_workdir() -> str:
    configured = (os.getenv("AGY_WORKDIR") or "").strip()
    path = Path(configured) if configured else (_ROOT / "data" / "agy_workspace")
    path.mkdir(parents=True, exist_ok=True)
    keep = path / ".keep"
    if not keep.exists():
        keep.write_text("agy print-mode workspace\n", encoding="utf-8")
    return str(path)


def _popen_communicate(
    cmd: List[str],
    timeout_sec: int,
    cwd: str,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    merged = _agy_env() if env is None else env
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "cwd": cwd,
        "text": True,
        "env": merged,
    }
    if os.name == "nt":
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        return proc.returncode or 0, stdout or "", stderr or ""
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        raise RuntimeError(f"agy CLI timed out after {timeout_sec}s") from exc


def complete(
    prompt: str,
    *,
    json_schema: Optional[Dict[str, Any]] = None,
    timeout_sec: int = 40,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Run `agy -p` and return the JSON envelope (status/response/structured_output)."""
    binary = resolve_agy_bin()
    if not binary:
        raise RuntimeError("agy CLI not found. Install with the Antigravity installer, then run `agy` once to sign in.")

    model_slug, effort = _model_and_effort(model)
    wait = max(20, int(timeout_sec))
    cmd = [
        binary,
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "--print-timeout",
        f"{wait}s",
        "--output-format",
        "json",
        "--model",
        model_slug,
    ]
    if effort:
        cmd.extend(["--effort", effort])
    cmd.extend(["-p", prompt])
    schema_path = None
    if json_schema:
        handle = tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False, encoding="utf-8")
        json.dump(json_schema, handle, ensure_ascii=False)
        handle.close()
        schema_path = handle.name
        cmd.extend(["--json-schema", schema_path])

    workdir = _agy_workdir()
    try:
        logger.info("Executing Antigravity CLI model=%s effort=%s timeout=%ss cwd=%s", model_slug, effort, wait, workdir)
        with _silence_user_mcp():
            code, stdout, stderr = _popen_communicate(cmd, wait + 15, workdir, env=_agy_env())
    finally:
        if schema_path:
            try:
                os.unlink(schema_path)
            except OSError:
                pass

    stderr = (stderr or "").strip()
    if code != 0 and not (stdout or "").strip():
        raise RuntimeError(stderr or f"agy CLI exited {code}")

    envelope = _parse_envelope(stdout)
    status = str(envelope.get("status") or "").upper()
    if status != "SUCCESS":
        err = envelope.get("error") or stderr or f"agy status={status or 'unknown'}"
        raise RuntimeError(str(err))
    if stderr:
        logger.debug("agy stderr: %s", stderr[:500])
    return envelope


def _lines_from_envelope(envelope: Dict[str, Any], expected: int) -> Optional[List[str]]:
    structured = envelope.get("structured_output")
    items = None
    if isinstance(structured, dict):
        items = structured.get("lines")
    if not isinstance(items, list):
        raw = envelope.get("response") or ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                items = parsed.get("lines")
        except Exception:
            items = None
    if not isinstance(items, list):
        return None
    by_index: Dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if idx >= 1:
            by_index[idx] = text
    if expected and all(i in by_index for i in range(1, expected + 1)):
        return [by_index[i] for i in range(1, expected + 1)]
    return None


_NUMBERED = re.compile(r"^(\d+)[\.\:\-\)\s]+(.*)$")


def _parse_numbered(content: str, expected: int) -> Optional[List[str]]:
    found: Dict[int, str] = {}
    for line in (content or "").splitlines():
        match = _NUMBERED.match(line.strip())
        if not match:
            continue
        found[int(match.group(1))] = match.group(2).strip()
    if expected and all(i in found for i in range(1, expected + 1)):
        return [found[i] for i in range(1, expected + 1)]
    return None


def translate_cues(
    texts: List[str],
    target_lang: str = "vi",
    *,
    title: str = "",
    on_status: Optional[Callable[[str], None]] = None,
    chunk_size: int = 24,
) -> List[str]:
    """Translate subtitle cues via local `agy` (Gemini 3.7 Flash). Raises on hard failure."""
    if not texts:
        return []
    lang = (target_lang or "vi").lower()
    lang_name = {"vi": "tiếng Việt", "en": "English", "zh": "Chinese"}.get(lang, lang)
    out: List[str] = []
    total = len(texts)
    model = default_model()

    def emit(message: str) -> None:
        if callable(on_status):
            try:
                on_status(message)
            except Exception:
                return

    emit(f"🌐 Google CLI (agy / {model}): dịch {total} câu sang {lang_name}...")
    size = max(4, int(chunk_size))
    for start in range(0, total, size):
        chunk = [str(t or "").strip() for t in texts[start:start + size]]
        numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(chunk, start=1))
        extra = f"Ngữ cảnh video: {title}\n" if title else ""
        prompt = (
            "Bạn là biên dịch phụ đề phim. Chỉ dịch, không giải thích, không dùng tool.\n"
            f"{extra}"
            f"Dịch sang {lang_name}. Giữ đúng {len(chunk)} câu, cùng thứ tự, cùng số thứ tự.\n"
            "Không để lại chữ Trung/Hàn/Nhật trong bản dịch tiếng Việt.\n"
            "Mỗi câu là lời thoại tự nhiên, đủ nghĩa, không chỉ dấu câu.\n"
            "Trả về JSON đúng schema: lines[{index, text}].\n\n"
            f"{numbered}"
        )
        emit(f"🌐 Google CLI đang dịch câu {start + 1}–{min(start + len(chunk), total)}/{total}...")
        parsed: Optional[List[str]] = None
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                envelope = complete(prompt, json_schema=_CUE_SCHEMA, timeout_sec=90, model=model)
                parsed = _lines_from_envelope(envelope, len(chunk))
                if not parsed:
                    parsed = _parse_numbered(str(envelope.get("response") or ""), len(chunk))
                if parsed and len(parsed) == len(chunk):
                    break
                last_error = RuntimeError(
                    f"agy returned {0 if not parsed else len(parsed)}/{len(chunk)} lines "
                    f"for chunk starting at {start + 1}"
                )
                parsed = None
            except Exception as exc:
                last_error = exc
                parsed = None
            if attempt < 2:
                emit(f"⚠️ Google CLI lỗi chunk {start + 1} (lần {attempt}): {last_error}. Thử lại...")
        if not parsed or len(parsed) != len(chunk):
            raise RuntimeError(str(last_error or "agy translation failed"))
        out.extend(parsed)
    emit(f"✅ Google CLI dịch xong {len(out)} câu ({model}).")
    return out

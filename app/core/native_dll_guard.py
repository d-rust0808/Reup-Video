"""
Native DLL initialization and runtime guard.
Ensures modern MSVC runtime (MSVCP140/VCRUNTIME140) and dependent native DLLs
are cleanly loaded, preventing WinError 1114 / 0xC0000005 crashes and guarding
against poisoned sys.modules state.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

_RUNTIME_INITIALIZED = False
_LOADED_HANDLES: list[int] = []


def init_native_runtime() -> bool:
    """Ensures modern VC++ runtime DLLs and package DLL directories are registered and preloaded."""
    global _RUNTIME_INITIALIZED
    if _RUNTIME_INITIALIZED:
        return True

    if sys.platform != "win32":
        _RUNTIME_INITIALIZED = True
        return True

    # 1. Locate x64_runtime DLL directory
    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "bin", "x64_runtime")),
        os.path.abspath(os.path.join(sys.prefix, "Scripts")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "venv", "Scripts")),
    ]

    dll_dir = None
    for cand in candidates:
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "msvcp140.dll")):
            dll_dir = cand
            break

    if dll_dir:
        # Prepend to PATH
        current_path = os.environ.get("PATH", "")
        if dll_dir not in current_path:
            os.environ["PATH"] = f"{dll_dir};{current_path}"

        # Register DLL directory for Python 3.8+ Windows loader
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(dll_dir)
            except Exception as e:
                logger.debug(f"add_dll_directory failed for {dll_dir}: {e}")

        # Pre-load modern MSVC CRT in dependency order
        ordered_dlls = [
            "vcruntime140.dll",
            "vcruntime140_1.dll",
            "msvcp140.dll",
            "msvcp140_1.dll",
            "msvcp140_2.dll",
            "concrt140.dll",
            "vccorlib140.dll",
        ]
        for dll_name in ordered_dlls:
            full_path = os.path.join(dll_dir, dll_name)
            if os.path.isfile(full_path):
                try:
                    h = ctypes.windll.kernel32.LoadLibraryW(full_path)
                    if h:
                        _LOADED_HANDLES.append(h)
                except Exception as e:
                    logger.debug(f"Pre-loading {full_path} failed: {e}")

    # 2. Add site-packages native directories (onnxruntime/capi, torch/lib)
    for sub in [
        os.path.join(sys.prefix, "Lib", "site-packages", "onnxruntime", "capi"),
        os.path.join(sys.prefix, "Lib", "site-packages", "torch", "lib"),
    ]:
        if os.path.isdir(sub) and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(sub)
            except Exception:
                pass

    _RUNTIME_INITIALIZED = True
    return True


def cleanup_poisoned_onnxruntime() -> None:
    """Removes broken or half-imported onnxruntime modules from sys.modules."""
    ort = sys.modules.get("onnxruntime")
    if ort is not None:
        if not hasattr(ort, "SessionOptions") or not hasattr(ort, "InferenceSession"):
            for mod_name in list(sys.modules.keys()):
                if mod_name == "onnxruntime" or mod_name.startswith("onnxruntime."):
                    sys.modules.pop(mod_name, None)


def cleanup_poisoned_torch() -> None:
    """Removes broken or half-imported torch modules from sys.modules."""
    tch = sys.modules.get("torch")
    if tch is not None:
        if not hasattr(tch, "__version__") or not hasattr(tch, "Tensor"):
            for mod_name in list(sys.modules.keys()):
                if mod_name == "torch" or mod_name.startswith("torch."):
                    sys.modules.pop(mod_name, None)


def is_onnx_available() -> bool:
    """Check if onnxruntime is functional without permanently poisoning sys.modules."""
    ort = sys.modules.get("onnxruntime")
    if ort is not None:
        return bool(
            getattr(ort, "SessionOptions", None)
            and getattr(ort, "InferenceSession", None)
        )

    init_native_runtime()
    try:
        import onnxruntime as _ort

        if hasattr(_ort, "SessionOptions") and hasattr(_ort, "InferenceSession"):
            return True
        cleanup_poisoned_onnxruntime()
        return False
    except Exception:
        cleanup_poisoned_onnxruntime()
        return False


def get_onnxruntime() -> Optional[Any]:
    """Safely import and return onnxruntime module, or None if unavailable."""
    if is_onnx_available():
        import onnxruntime

        return onnxruntime
    return None


def is_torch_available() -> bool:
    """Check if torch is functional without permanently poisoning sys.modules."""
    tch = sys.modules.get("torch")
    if tch is not None and hasattr(tch, "__version__") and hasattr(tch, "Tensor"):
        return True

    init_native_runtime()
    cleanup_poisoned_torch()
    try:
        import torch as _tch

        if hasattr(_tch, "__version__") and hasattr(_tch, "Tensor"):
            return True
        cleanup_poisoned_torch()
        return False
    except Exception:
        cleanup_poisoned_torch()
        return False


def get_torch() -> Optional[Any]:
    """Safely import and return torch module, or None if unavailable."""
    if is_torch_available():
        import torch

        return torch
    return None

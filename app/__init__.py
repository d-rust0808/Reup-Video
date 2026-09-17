"""
App package initialization.
"""

import os
import sys

_app_dir = os.path.dirname(os.path.abspath(__file__))
_modules_dir = os.path.join(_app_dir, "modules")
if _modules_dir not in sys.path:
    sys.path.insert(0, _modules_dir)

try:
    from app.core.native_dll_guard import init_native_runtime

    init_native_runtime()
except Exception:
    pass

__version__ = "0.1.0"


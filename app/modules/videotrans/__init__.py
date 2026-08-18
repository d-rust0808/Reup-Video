import importlib
import inspect
from dataclasses import dataclass
from typing import Optional

try:
    from .configure.config import logger
except ImportError:
    try:
        from videotrans.configure.config import logger
    except ImportError:
        try:
            _cfg_mod = importlib.import_module("configure.config")
            logger = getattr(_cfg_mod, "logger")
        except ImportError:
            import logging
            logger = logging.getLogger(__name__)

VERSION = "v4.09"
VERSION_NUM = 409
_loaded_modules = {}
winform = None

@dataclass
class ChannelProvider:
    name: str
    imp: str
    key_name: Optional[str] = None
    win: Optional[str] = None


# provider_type: TTS|STT|STS 配音，转录，翻译字幕
# _ID_NAME_DICT 渠道配置信息
def get_class(channel_id: int = 0, provider_type=None, _ID_NAME_DICT=None):
    _key = f'{provider_type}-{channel_id}'
    if _key in _loaded_modules:
        return _loaded_modules[_key]
    try:
        _module_map = _ID_NAME_DICT.get(channel_id)
        if not _module_map: raise RuntimeError(f'{provider_type} not exists Channel:{channel_id}')
        try:
            module = importlib.import_module(f'{provider_type}{_module_map.imp}', __name__)
        except ImportError:
            module = importlib.import_module(f'videotrans.{provider_type}{_module_map.imp}', __name__)
        for obj_name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__:
                _loaded_modules[_key] = obj
                return obj
    except Exception as e:
        logger.exception(f'懒加载渠道{provider_type}:{channel_id=}失败:{e}', exc_info=True)
        raise

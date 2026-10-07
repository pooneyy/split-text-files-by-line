"""
版本元信息

在 CI 阶段，可通过环境变量 `APP_VERSION` 覆盖默认的 `__version__`
"""

from __future__ import annotations

import os

__version__ = os.environ.get("APP_VERSION") or "0.1.0"

# 其他与发布相关的元数据
__app_name__ = "按行分割文本文件"
__app_name_en__ = "Split Text Files by Line"
__author__ = "pooneyy"

def get_version() -> str:
    """返回当前版本号。

    通过函数访问而不是直接引用 `__version__`，便于未来切换到动态来源
    (例如 `importlib.metadata.version`)
    """
    return __version__

def get_app_display_name(lang: str = "zh") -> str:
    """根据当前语言返回应用显示名"""
    return __app_name_en__ if lang == "en" else __app_name__

__all__ = [
    "__version__",
    "__app_name__",
    "__app_name_en__",
    "__author__",
    "__license__",
    "__release_date__",
    "get_version",
    "get_app_display_name",
]

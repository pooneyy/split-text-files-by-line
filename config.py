"""配置持久化: 读写与脚本同目录下的 config.json"""

from __future__ import annotations

import json
import os

from typing import Any, Dict

CONFIG_FILENAME = "config.json"

DEFAULTS: Dict[str, Any] = {
    "encoding": "utf-8",
    "newline_policy": "preserve",  # preserve | lf | crlf
    "conflict_policy": "rename",   # skip | overwrite | rename
    "output_dir": "",
    "input_files": [],
    "prefix": "",
    "suffix": ".txt",
    "index_start": 1,
    "plan": [500],
    "language": "zh",
    "window_size": [800, 600],
}

def config_path(base_dir: str) -> str:
    """返回 config.json 的完整路径"""
    return os.path.join(base_dir, CONFIG_FILENAME)

def load_config(base_dir: str) -> Dict[str, Any]:
    """读取配置；不存在则返回 defaults 的拷贝。"""
    path = config_path(base_dir)
    if not os.path.isfile(path):
        return dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)
    # 合并默认值，防止缺字段
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        merged.update(data)
    return merged

def save_config(base_dir: str, data: Dict[str, Any]) -> None:
    """保存配置"""
    path = config_path(base_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        # 写失败时清理临时文件
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

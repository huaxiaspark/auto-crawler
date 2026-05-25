"""
统一的"短 sleep"分档与抖动模块。

将散落在各模块中的 `time.sleep(<常量>)` 按用途归为 5 档：
    micro  - 极短：fill / press 等输入后让 UI 同步（约 0.05 ~ 0.1s）
    short  - 短：click / select 后等待响应（约 0.2 ~ 0.3s）
    medium - 中：下拉打开 / 选项点击后（约 0.5 ~ 0.8s）
    long   - 长：面板展开 / 简单动画 / 普通查询返回（约 1 ~ 1.5s）
    xlong  - 超长：导航切换 / 页面重载等（约 2 ~ 3s）

每档对应一个 `request.ui_*_delay` 配置项；另有两个全局开关：
    sleep_jitter - 抖动比例（0 表示无抖动），最终值 = base * (1 ± jitter)
    sleep_scale  - 全局缩放因子，方便整体放慢以降低被发现的风险

使用：
    from utils.timing import configure as configure_sleep, sleep as ui_sleep
    configure_sleep(config)        # 程序启动时调用一次
    ui_sleep("short")              # 在原本写 time.sleep(0.3) 的地方调用
"""

import random
import time
from typing import Dict

_DEFAULTS: Dict[str, float] = {
    "micro": 0.1,
    "short": 0.3,
    "medium": 0.6,
    "long": 1.5,
    "xlong": 3.0,
    "jitter": 0.3,
    "scale": 1.0,
}

_config: Dict[str, float] = dict(_DEFAULTS)


def configure(config: dict) -> None:
    """从 config 字典加载分档时长，未配置时回落到默认值。"""
    req = (config or {}).get("request", {}) or {}
    mapping = {
        "micro": "ui_micro_delay",
        "short": "ui_short_delay",
        "medium": "ui_medium_delay",
        "long": "ui_long_delay",
        "xlong": "ui_xlong_delay",
        "jitter": "sleep_jitter",
        "scale": "sleep_scale",
    }
    for key, cfg_name in mapping.items():
        if cfg_name in req and req[cfg_name] is not None:
            try:
                _config[key] = max(0.0, float(req[cfg_name]))
            except (TypeError, ValueError):
                _config[key] = _DEFAULTS[key]


def sleep(kind: str = "short") -> None:
    """按档位休眠，自动应用全局缩放与随机抖动。

    Args:
        kind: 档位名（micro/short/medium/long/xlong），未识别时按 short 处理。
    """
    base = _config.get(kind)
    if base is None:
        base = _config.get("short", _DEFAULTS["short"])

    scaled = base * _config.get("scale", 1.0)
    jitter = _config.get("jitter", 0.0)
    if jitter > 0 and scaled > 0:
        delta = scaled * jitter
        scaled = scaled + random.uniform(-delta, delta)

    if scaled > 0:
        time.sleep(scaled)


def get_delay(kind: str = "short") -> float:
    """返回某档位的当前基准时长（不含抖动），便于日志或对外展示。"""
    return float(_config.get(kind, _DEFAULTS.get(kind, 0.0)))

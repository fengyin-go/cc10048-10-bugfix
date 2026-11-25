"""时效口径解析：统一单独检查与监控交付两处的时效读取规则。

历史问题（两处口径不一致）：
- 单独检查入口用 ``int(...)`` 解析，遇到 ``1.5h`` 直接抛 ValueError 报错；
- 监控交付面板只剥离 ``h`` 不剥离 ``小时``，``2小时`` 解析失败后静默回退为 24 小时。

本模块统一按“到账数据的实际时效”解析为浮点小时数：
``1.5h`` / ``1.5小时`` → 1.5 小时（一个半小时），
``2小时`` / ``2h`` / ``2`` → 2.0 小时（两小时）。
"""
from __future__ import annotations

import re

# 数值（支持小数）+ 可选单位（h/小时/分钟/min）
_DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>小时|分钟|h|hour|hours|min|m)?\s*$",
    re.IGNORECASE,
)


def parse_duration_hours(raw: object, *, default: float | None = 24.0) -> float:
    """把时效配置文本解析为小时数（float，允许半小时等小数时效）。

    支持写法：``"1.5h"``、``"1.5小时"``、``"2小时"``、``"2h"``、``"2"``、
    ``"90分钟"``。无法解析时返回 ``default``（默认 24 小时），调用方需要
    严格报错时请改用 :func:`try_parse_duration_hours`。
    """
    parsed = try_parse_duration_hours(raw)
    if parsed is None:
        if default is None:
            raise ValueError(f"无法解析的时效配置：{raw!r}")
        return float(default)
    return parsed


def try_parse_duration_hours(raw: object) -> float | None:
    """严格解析时效配置，失败返回 None（不做静默回退）。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw >= 0 else None
    text = str(raw).strip()
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if not match:
        return None
    value = float(match.group("value"))
    unit = (match.group("unit") or "").lower()
    if unit in ("分钟", "min", "m"):
        return round(value / 60.0, 4)
    # 无单位、h、hour、hours、小时 一律按小时处理
    return value


def format_hours(hours: float) -> str:
    """把小时数格式化为人类可读的时效文案，例如 1.5 → '1.5小时'、2.0 → '2小时'。"""
    hours = float(hours)
    if hours.is_integer():
        return f"{int(hours)}小时"
    text = f"{hours:.2f}".rstrip("0").rstrip(".")
    return f"{text}小时"

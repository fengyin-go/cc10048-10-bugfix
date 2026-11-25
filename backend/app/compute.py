"""业务计算引擎：指标聚合、统计检验、采样、异常检测等通用计算工具。"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any


def aggregate(values: list[float], method: str = "sum") -> float:
    """按聚合方法计算统计值。"""
    if not values:
        return 0.0
    if method == "sum":
        return sum(values)
    if method == "avg" or method == "mean":
        return sum(values) / len(values)
    if method == "max":
        return max(values)
    if method == "min":
        return min(values)
    if method == "count":
        return float(len(values))
    if method == "count_distinct":
        return float(len(set(values)))
    if method == "median":
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        if n % 2 == 1:
            return sorted_vals[n // 2]
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    if method == "stddev":
        if len(values) < 2:
            return 0.0
        mean_val = sum(values) / len(values)
        variance = sum((v - mean_val) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)
    return sum(values)


def t_test(group_a: list[float], group_b: list[float], alpha: float = 0.05) -> dict[str, Any]:
    """双样本 t 检验（Welch 近似）。"""
    if len(group_a) < 2 or len(group_b) < 2:
        return {"method": "welch_t_test", "significant": False, "p_value": 1.0,
                "mean_a": 0.0, "mean_b": 0.0, "diff_pct": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
    n_a, n_b = len(group_a), len(group_b)
    mean_a = sum(group_a) / n_a
    mean_b = sum(group_b) / n_b
    var_a = sum((v - mean_a) ** 2 for v in group_a) / (n_a - 1) if n_a > 1 else 0.0
    var_b = sum((v - mean_b) ** 2 for v in group_b) / (n_b - 1) if n_b > 1 else 0.0
    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return {"method": "welch_t_test", "significant": False, "p_value": 1.0,
                "mean_a": mean_a, "mean_b": mean_b, "diff_pct": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
    t_stat = (mean_a - mean_b) / se
    df_num = (var_a / n_a + var_b / n_b) ** 2
    df_den = ((var_a / n_a) ** 2) / (n_a - 1) + ((var_b / n_b) ** 2) / (n_b - 1) if n_a > 1 and n_b > 1 else 1
    df = df_num / df_den if df_den > 0 else 1
    p_value = _t_distribution_p_value(abs(t_stat), df)
    diff_pct = ((mean_a - mean_b) / mean_b * 100) if mean_b != 0 else 0.0
    z_critical = 1.96
    ci_half = z_critical * se
    return {
        "method": "welch_t_test",
        "significant": p_value < alpha,
        "p_value": round(p_value, 6),
        "mean_a": round(mean_a, 4),
        "mean_b": round(mean_b, 4),
        "diff_pct": round(diff_pct, 2),
        "ci_lower": round((mean_a - mean_b) - ci_half, 4),
        "ci_upper": round((mean_a - mean_b) + ci_half, 4),
    }


def chi_square_test(observed: list[float], expected: list[float], alpha: float = 0.05) -> dict[str, Any]:
    """卡方拟合优度检验。"""
    if len(observed) != len(expected) or len(observed) == 0:
        return {"method": "chi_square", "significant": False, "p_value": 1.0, "statistic": 0.0, "df": 0}
    chi2 = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    df = len(observed) - 1
    p_value = _chi_square_p_value(chi2, df) if df > 0 else 1.0
    return {
        "method": "chi_square",
        "significant": p_value < alpha,
        "p_value": round(p_value, 6),
        "statistic": round(chi2, 4),
        "df": df,
    }


def random_sample(data: list[dict[str, Any]], method: str, ratio: float, seed: int | None = None) -> list[dict[str, Any]]:
    """数据采样：随机、分层、蓄水池。"""
    if not data:
        return []
    rng = random.Random(seed) if seed is not None else random.Random()
    k = max(1, int(len(data) * min(ratio, 1.0)))
    if k >= len(data):
        return list(data)
    if method == "stratified" or method == "分层":
        return _stratified_sample(data, k, rng)
    if method == "reservoir" or method == "蓄水池":
        return _reservoir_sample(data, k, rng)
    return rng.sample(data, k)


def anomaly_iqr(data: list[float], multiplier: float = 1.5) -> dict[str, Any]:
    """IQR 异常检测。"""
    if len(data) < 4:
        return {"method": "iqr", "anomaly_count": 0, "upper_bound": 0.0,
                "lower_bound": 0.0, "q1": 0.0, "q3": 0.0, "anomalies": []}
    sorted_data = sorted(data)
    q1_idx = len(sorted_data) // 4
    q3_idx = (3 * len(sorted_data)) // 4
    q1 = sorted_data[q1_idx]
    q3 = sorted_data[q3_idx]
    iqr_val = q3 - q1
    upper = q3 + multiplier * iqr_val
    lower = q1 - multiplier * iqr_val
    anomalies = [v for v in data if v > upper or v < lower]
    return {
        "method": "iqr",
        "anomaly_count": len(anomalies),
        "upper_bound": round(upper, 4),
        "lower_bound": round(lower, 4),
        "q1": round(q1, 4),
        "q3": round(q3, 4),
        "anomalies": sorted(anomalies),
    }


def anomaly_zscore(data: list[float], threshold: float = 3.0) -> dict[str, Any]:
    """Z-Score 异常检测。"""
    if len(data) < 3:
        return {"method": "zscore", "anomaly_count": 0, "mean": 0.0,
                "std": 0.0, "threshold": threshold, "anomalies": []}
    mean_val = sum(data) / len(data)
    std_val = math.sqrt(sum((v - mean_val) ** 2 for v in data) / len(data)) if len(data) > 1 else 0.0
    if std_val == 0:
        return {"method": "zscore", "anomaly_count": 0, "mean": round(mean_val, 4),
                "std": 0.0, "threshold": threshold, "anomalies": []}
    anomalies = [v for v in data if abs(v - mean_val) / std_val > threshold]
    return {
        "method": "zscore",
        "anomaly_count": len(anomalies),
        "mean": round(mean_val, 4),
        "std": round(std_val, 4),
        "threshold": threshold,
        "anomalies": sorted(anomalies),
    }


def compute_calibrate_bias(original: float, new_val: float) -> dict[str, Any]:
    """计算口径校准偏差。"""
    if original == 0:
        return {"original": original, "new": new_val, "bias_pct": 0.0 if new_val == 0 else float("inf"),
                "bias_abs": new_val, "direction": "up" if new_val > 0 else "down"}
    bias_pct = round((new_val - original) / abs(original) * 100, 4)
    bias_abs = round(new_val - original, 4)
    direction = "up" if bias_pct > 0 else ("down" if bias_pct < 0 else "flat")
    return {"original": original, "new": new_val, "bias_pct": bias_pct,
            "bias_abs": bias_abs, "direction": direction}


def drill_path_decompose(
    total: float, dimensions: list[dict[str, float]]
) -> list[dict[str, Any]]:
    """下钻分解：按维度拆解总量。"""
    results = []
    for dim in dimensions:
        contribution_pct = round(sum(dim.values()) / total * 100, 2) if total > 0 else 0.0
        results.append({
            "dimension": dim,
            "subtotal": round(sum(dim.values()), 4),
            "contribution_pct": contribution_pct,
        })
    return results


def generate_mock_metric_values(n: int = 30, base: float = 100.0, noise: float = 20.0) -> list[float]:
    """生成模拟指标时间序列。"""
    rng = random.Random(42)
    values = []
    trend = 0.0
    for _ in range(n):
        trend += rng.uniform(-1, 1.5)
        val = base + trend * 2 + rng.gauss(0, noise)
        values.append(round(val, 4))
    return values


def compute_freshness_delay(
    expected_interval: float, last_check: str | None, current_time: str | None = None
) -> dict[str, Any]:
    """计算数据时效延迟。

    ``expected_interval`` 为期望时效（小时，支持小数，如 1.5）。
    ``last_check`` 为来源数据的实际到数时间；无法解析时按恰好超期处理。
    返回结果同时给出实际到数时间（``actual_arrival_time``），供两处
    校验口径展示同一真实到数时间。
    """
    now = datetime.now()
    if current_time:
        try:
            now = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            now = datetime.now()
    expected_hours = float(expected_interval)
    arrival_raw = last_check or None
    last = None
    arrival_parse_ok = False
    if last_check:
        try:
            last = datetime.fromisoformat(str(last_check).replace("Z", "+00:00"))
            arrival_parse_ok = True
        except (ValueError, TypeError):
            last = None
    if last is None:
        last = now - timedelta(hours=expected_hours)
    actual_delay_hours = (now - last).total_seconds() / 3600.0
    status = "及时" if actual_delay_hours <= expected_hours else (
        "延迟" if actual_delay_hours <= expected_hours * 2 else "严重延迟"
    )
    return {
        "expected_interval_hours": expected_hours,
        "actual_delay_hours": round(actual_delay_hours, 2),
        "status": status,
        "seconds_since_last": round((now - last).total_seconds(), 0),
        "actual_arrival_time": last.strftime("%Y-%m-%d %H:%M:%S") if arrival_parse_ok else None,
        "arrival_time_raw": arrival_raw,
        "arrival_parse_ok": arrival_parse_ok,
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def cache_hit_simulation(
    query_key: str, ttl_seconds: int, last_refresh: str | None = None
) -> dict[str, Any]:
    """查询缓存命中模拟。"""
    cache_hit = False
    if last_refresh:
        try:
            last = datetime.fromisoformat(last_refresh.replace("Z", "+00:00"))
            if (datetime.now() - last).total_seconds() < ttl_seconds:
                cache_hit = True
        except (ValueError, TypeError):
            pass
    # 随机模拟部分缓存命中
    rng = random.Random(hash(query_key) % 10000)
    hit_rate = 0.75 if cache_hit else rng.uniform(0.1, 0.4)
    return {
        "cache_key": query_key,
        "ttl_seconds": ttl_seconds,
        "hit": cache_hit,
        "estimated_hit_rate": round(hit_rate, 2),
        "size_bytes": rng.randint(1024, 1048576),
    }


def segment_diff_report(seg_a: list[float], seg_b: list[float]) -> dict[str, Any]:
    """分群对比差异报告。"""
    result = t_test(seg_a, seg_b)
    result["count_a"] = len(seg_a)
    result["count_b"] = len(seg_b)
    result["std_a"] = round(float(math.sqrt(
        sum((v - result["mean_a"]) ** 2 for v in seg_a) / max(len(seg_a) - 1, 1)
    )) if len(seg_a) > 1 else 0.0, 4)
    result["std_b"] = round(float(math.sqrt(
        sum((v - result["mean_b"]) ** 2 for v in seg_b) / max(len(seg_b) - 1, 1)
    )) if len(seg_b) > 1 else 0.0, 4)
    return result


def _t_distribution_p_value(t: float, df: float) -> float:
    """近似 t 分布 p 值（基于 Beta 不完备函数近似）。"""
    if t < 0:
        return 1.0
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    # 不完备 Beta 函数近似
    a = df / 2
    b = 0.5
    if x <= 0:
        return 0.0
    if x >= 1:
        x = 0.9999
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _beta_cf(a, b, x) / a
    return 1 - bt * _beta_cf(b, a, 1 - x) / b


def _beta_cf(a: float, b: float, x: float) -> float:
    """连分式近似 Beta 不完备函数。"""
    qab = a + b
    qap = a + 1
    qam = a - 1
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, 101):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            return h
    return h


def _chi_square_p_value(chi2: float, df: int) -> float:
    """近似卡方分布 p 值。"""
    if df <= 0:
        return 1.0
    return _t_distribution_p_value(math.sqrt(chi2), df) / 2.0


def _stratified_sample(data: list[dict[str, Any]], k: int, rng: random.Random) -> list[dict[str, Any]]:
    """分层采样。"""
    if "status" in data[0] if data else False:
        strata: dict[str, list[dict[str, Any]]] = {}
        for row in data:
            key = str(row.get("status", "unknown"))
            strata.setdefault(key, []).append(row)
        result: list[dict[str, Any]] = []
        for key, items in strata.items():
            n = max(1, int(k * len(items) / len(data)))
            result.extend(rng.sample(items, min(n, len(items))))
        return result[:k]
    return rng.sample(data, min(k, len(data)))


def _reservoir_sample(data: list[dict[str, Any]], k: int, rng: random.Random) -> list[dict[str, Any]]:
    """蓄水池采样。"""
    reservoir = data[:k]
    for i in range(k, len(data)):
        j = rng.randint(0, i)
        if j < k:
            reservoir[j] = data[i]
    return reservoir
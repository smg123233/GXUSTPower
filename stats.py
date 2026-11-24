"""用量统计与「预计可用」估算（仅标准库）。

历史记录来自定时采样（默认 30 分钟一次），余额下降视为用电：
- 日/月/年用量：把每次下降按发生日期归档
- 预计可用：至少累计 7 天数据后，用近 7 天日用量的指数加权均值(EWMA)估算
  不同宿舍用电习惯不同，因此结果仅供参考
"""
from __future__ import annotations

import datetime as dt
import math
import time

MAX_GAP_SECONDS = 48 * 3600
MIN_ESTIMATE_DAYS = 7
EWMA_ALPHA = 0.45


def _local_date(ts: float) -> dt.date:
    return dt.date.fromtimestamp(ts)


def _drops(samples: list[dict]):
    """逐对相邻采样计算用电下降量（充值导致的上升忽略）。"""
    previous = None
    for sample in samples:
        if previous is not None:
            gap = sample["ts"] - previous["ts"]
            if 0 < gap <= MAX_GAP_SECONDS:
                drop = previous["balance"] - sample["balance"]
                if drop > 0:
                    yield sample["ts"], drop
        previous = sample


def usage_between(samples: list[dict], start_ts: float, end_ts: float) -> float:
    usage = 0.0
    previous = None
    for sample in samples:
        if (previous is not None and previous["ts"] >= start_ts
                and sample["ts"] <= end_ts):
            drop = previous["balance"] - sample["balance"]
            if drop > 0:
                usage += drop
        previous = sample
    return usage


def daily_series(samples: list[dict], now: float | None = None,
                 days: int = 30) -> list[dict]:
    now = now or time.time()
    end = _local_date(now)
    start = end - dt.timedelta(days=days - 1)
    buckets = {start + dt.timedelta(days=i): 0.0 for i in range(days)}
    for ts, drop in _drops(samples):
        day = _local_date(ts)
        if start <= day <= end:
            buckets[day] += drop
    return [{"key": day.isoformat(),
             "label": f"{day.month:02d}-{day.day:02d}",
             "value": round(value, 2)}
            for day, value in buckets.items()]


def monthly_series(samples: list[dict], now: float | None = None,
                   months: int = 12) -> list[dict]:
    now = now or time.time()
    end = _local_date(now)
    keys: list[tuple[int, int]] = []
    year, month = end.year, end.month
    for _ in range(months):
        keys.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    keys.reverse()
    buckets = {key: 0.0 for key in keys}
    for ts, drop in _drops(samples):
        day = _local_date(ts)
        key = (day.year, day.month)
        if key in buckets:
            buckets[key] += drop
    return [{"key": f"{y}-{m:02d}", "label": f"{y}-{m:02d}",
             "value": round(value, 2)}
            for (y, m), value in buckets.items()]


def yearly_series(samples: list[dict], now: float | None = None,
                  years: int = 5) -> list[dict]:
    now = now or time.time()
    end = _local_date(now)
    keys = [end.year - i for i in range(years - 1, -1, -1)]
    buckets = {key: 0.0 for key in keys}
    for ts, drop in _drops(samples):
        year = _local_date(ts).year
        if year in buckets:
            buckets[year] += drop
    return [{"key": str(y), "label": f"{y}年", "value": round(value, 2)}
            for y, value in buckets.items()]


def yesterday_usage(samples: list[dict], now: float | None = None) -> float:
    now = now or time.time()
    target = _local_date(now) - dt.timedelta(days=1)
    return round(sum(drop for ts, drop in _drops(samples)
                     if _local_date(ts) == target), 2)


def _ewma(values: list[float], alpha: float = EWMA_ALPHA) -> float:
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def estimate(samples: list[dict], now: float, balance: float | None) -> dict:
    """用近 7 天日用量估算可用天数；不足 7 天返回不可用原因。"""
    base = {"available": False, "dailyAvg": None, "daysLeft": None,
            "reason": "", "windowDays": MIN_ESTIMATE_DAYS,
            "daysWithData": 0, "note": "仅供参考"}
    if not samples or len(samples) < 2 or balance is None:
        base["reason"] = "样本不足"
        return base

    span_days = (samples[-1]["ts"] - samples[0]["ts"]) / 86400
    if span_days < MIN_ESTIMATE_DAYS:
        need = max(0, math.ceil(MIN_ESTIMATE_DAYS - span_days))
        base["reason"] = f"还需累计约 {need} 天数据"
        base["spanDays"] = round(span_days, 1)
        return base

    series = daily_series(samples, now, days=8)
    history = series[:-1]  # 去掉今天（不完整）
    values = [point["value"] for point in history]
    days_with_data = sum(1 for value in values if value > 0)
    base["daysWithData"] = days_with_data
    base["spanDays"] = round(span_days, 1)
    if sum(values) <= 0.01:
        base["reason"] = "近 7 天没有用电下降记录"
        return base

    expected = _ewma(values)
    if expected <= 0.02:
        base["reason"] = "日用量太低，无法估算"
        return base

    days_left = balance / expected
    base.update({
        "available": True,
        "dailyAvg": round(expected, 2),
        "daysLeft": round(min(days_left, 3650), 1),
        "windowDays": MIN_ESTIMATE_DAYS,
        "reason": "",
    })
    return base


def summarize(samples: list[dict], now: float, balance: float | None) -> dict:
    span_hours = ((samples[-1]["ts"] - samples[0]["ts"]) / 3600.0) if samples else 0.0
    est = estimate(samples, now, balance)
    return {
        "usage24h": round(usage_between(samples, now - 86400, now), 2),
        "yesterday": yesterday_usage(samples, now),
        "dailyAvg": est["dailyAvg"],
        "daysLeft": est["daysLeft"],
        "reliable": est["available"],
        "reason": est["reason"],
        "note": "仅供参考",
        "windowDays": est["windowDays"],
        "daysWithData": est["daysWithData"],
        "spanHours": round(span_hours, 1),
    }

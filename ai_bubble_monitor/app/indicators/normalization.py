from __future__ import annotations

import bisect
import math
from collections import deque
from datetime import date, timedelta
from statistics import median, stdev
from typing import Iterable

Point = tuple[date, float | None]
SUPPORTED_METHODS = {
    "rolling_percentile", "robust_z_score", "percentage_change", "rate_of_change",
    "moving_average_deviation", "volatility_adjusted_momentum",
}


def normalize_series(
    points: Iterable[Point],
    method: str,
    direction: str,
    lookback_days: int = 1825,
    minimum_observations: int = 60,
) -> list[float | None]:
    """Normalize an ordered date/value series to 0–100 without looking ahead."""
    ordered = list(points)
    if method not in SUPPORTED_METHODS:
        raise ValueError(f"Unsupported normalization method: {method}")
    if direction not in {"higher_is_worse", "lower_is_worse", "higher_is_better", "lower_is_better"}:
        raise ValueError(f"Unsupported risk direction: {direction}")
    if lookback_days < 1 or minimum_observations < 2:
        raise ValueError("lookback_days must be positive and minimum_observations at least 2")

    if method == "rolling_percentile":
        signals = [value for _, value in ordered]
        return _rolling_rank(ordered, signals, direction, lookback_days, minimum_observations)

    if method == "robust_z_score":
        result: list[float | None] = []
        for index, (observed_on, value) in enumerate(ordered):
            window = _window_values(ordered, index, observed_on, lookback_days)
            if value is None or len(window) < minimum_observations:
                result.append(None)
                continue
            center = median(window)
            mad = median([abs(item - center) for item in window])
            if mad == 0:
                result.append(50.0 if value == center else _orient(100.0 if value > center else 0.0, direction))
                continue
            z_score = (value - center) / (1.4826 * mad)
            # Logistic approximation to the normal CDF, bounded away from exact 0 and 100.
            risk_percentile = 100.0 / (1.0 + math.exp(-max(-20.0, min(20.0, 1.702 * z_score))))
            result.append(round(_orient(risk_percentile, direction), 4))
        return result

    signals = _derive_signals(ordered, method)
    return _rolling_rank(ordered, signals, direction, lookback_days, minimum_observations)


def _window_values(points: list[Point], index: int, observed_on: date, lookback_days: int) -> list[float]:
    floor = observed_on - timedelta(days=lookback_days)
    return [float(value) for point_date, value in points[: index + 1]
            if floor <= point_date <= observed_on and value is not None and math.isfinite(value)]


def _rolling_rank(
    points: list[Point], signals: list[float | None], direction: str,
    lookback_days: int, minimum_observations: int,
) -> list[float | None]:
    result: list[float | None] = []
    queue: deque[tuple[date, float]] = deque()
    sorted_values: list[float] = []
    previous_date: date | None = None
    for (observed_on, _), signal in zip(points, signals):
        if previous_date is not None and observed_on < previous_date:
            raise ValueError("points must be ordered by observation date")
        floor = observed_on - timedelta(days=lookback_days)
        while queue and queue[0][0] < floor:
            _, expired = queue.popleft()
            position = bisect.bisect_left(sorted_values, expired)
            sorted_values.pop(position)
        if signal is None or not math.isfinite(signal):
            result.append(None)
        else:
            position = bisect.bisect_left(sorted_values, signal)
            bisect.insort(sorted_values, signal)
            queue.append((observed_on, signal))
            if len(sorted_values) < minimum_observations:
                result.append(None)
            elif len(sorted_values) == 1:
                result.append(50.0)
            else:
                left = bisect.bisect_left(sorted_values, signal)
                right = bisect.bisect_right(sorted_values, signal)
                percentile = 100.0 * ((left + right - 1) / 2) / (len(sorted_values) - 1)
                result.append(round(_orient(percentile, direction), 4))
        previous_date = observed_on
    return result


def _derive_signals(points: list[Point], method: str) -> list[float | None]:
    if method in {"percentage_change", "rate_of_change"}:
        period = 30
        signals: list[float | None] = []
        for index, (observed_on, value) in enumerate(points):
            prior = next((prior_value for prior_date, prior_value in reversed(points[:index])
                          if prior_date <= observed_on - timedelta(days=period) and prior_value not in (None, 0)), None)
            if value is None or prior is None:
                signals.append(None)
                continue
            change = (float(value) - float(prior)) / abs(float(prior))
            signals.append(change if method == "percentage_change" else change * 100.0 / period)
        return signals

    if method == "moving_average_deviation":
        signals = []
        for index, (observed_on, value) in enumerate(points):
            floor = observed_on - timedelta(days=30)
            window = [float(item) for day, item in points[:index + 1]
                      if floor <= day <= observed_on and item is not None]
            average = sum(window) / len(window) if window else 0
            signals.append((float(value) / average) - 1 if value is not None and average else None)
        return signals

    # Volatility-adjusted momentum: trailing 30-day return divided by trailing 90-day
    # standard deviation of daily returns. Missing calendar days are not synthesized.
    returns: list[tuple[date, float]] = []
    signals = []
    for index, (observed_on, value) in enumerate(points):
        previous = next((float(prior_value) for _, prior_value in reversed(points[:index])
                         if prior_value not in (None, 0)), None)
        if value is not None and previous is not None:
            returns.append((observed_on, (float(value) - previous) / abs(previous)))
        floor_30 = observed_on - timedelta(days=30)
        floor_90 = observed_on - timedelta(days=90)
        old = next((float(prior_value) for prior_date, prior_value in reversed(points[:index])
                    if prior_date <= floor_30 and prior_value not in (None, 0)), None)
        recent_returns = [item for day, item in returns if floor_90 <= day <= observed_on]
        current_return = (float(value) - old) / abs(old) if value is not None and old is not None else None
        volatility = stdev(recent_returns) if len(recent_returns) >= 2 else None
        signals.append(current_return / volatility if current_return is not None and volatility else None)
    return signals


def _orient(value: float, direction: str) -> float:
    return 100.0 - value if direction in {"lower_is_worse", "higher_is_better"} else value

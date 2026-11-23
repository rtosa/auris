from datetime import date, timedelta

import pytest

from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.indicators.normalization import SUPPORTED_METHODS, normalize_series, transform_series
from app.services.scoring import recalculate_indicators
from app.services.collection import seed_catalog
from sqlalchemy import text


def sample_points(count: int = 140):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=index), float(index + 10)) for index in range(count)]


@pytest.mark.parametrize("method", sorted(SUPPORTED_METHODS))
def test_normalizers_return_bounded_values_without_lookahead(method):
    points = sample_points()
    full = normalize_series(points, method, "higher_is_worse", 1825, 30)
    prefix = normalize_series(points[:100], method, "higher_is_worse", 1825, 30)

    assert full[-1] is not None
    assert all(value is None or 0 <= value <= 100 for value in full)
    assert full[99] == prefix[-1]


def test_rolling_percentile_respects_risk_direction_and_missing_values():
    points = [(date(2026, 1, 1) + timedelta(days=i), float(i) if i != 2 else None) for i in range(70)]
    risk = normalize_series(points, "rolling_percentile", "higher_is_worse", 365, 30)
    benefit = normalize_series(points, "rolling_percentile", "higher_is_better", 365, 30)

    assert risk[2] is None
    assert risk[-1] == 100.0
    assert benefit[-1] == 0.0


def test_year_over_year_percent_transform_uses_prior_comparable_observation():
    points = [
        (date(2024, 1, 1), 100.0),
        (date(2025, 1, 1), 103.0),
        (date(2025, 4, 1), 104.0),
        (date(2026, 6, 1), 107.0),
    ]

    transformed = transform_series(points, "year_over_year_percent_change")

    assert transformed[0][1] is None
    assert transformed[1][1] == 3.0
    assert transformed[2][1] is None
    assert transformed[3][1] is None


def test_recalculate_persists_normalized_values_and_missing_quality(tmp_path):
    definition = next(item for item in load_definitions() if item.id == "vix")
    engine = make_engine(tmp_path / "monitor.db")
    upgrade_database(tmp_path / "monitor.db")
    seed_catalog(engine, [definition])
    rows = [
        ("fred", "vix", (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
         f"2026-06-01T00:00:{i:02d}+00:00", None if i == 12 else float(15 + i), "{}", "https://fred.stlouisfed.org/series/VIXCLS")
        for i in range(90)
    ]
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO raw_observations
            (source_id,indicator_id,observation_date,fetched_at,raw_value,raw_payload,source_reference)
            VALUES (:source_id,:indicator_id,:observation_date,:fetched_at,:raw_value,:raw_payload,:source_reference)"""), [
                dict(zip(("source_id", "indicator_id", "observation_date", "fetched_at", "raw_value", "raw_payload", "source_reference"), row))
                for row in rows
            ])

    first = recalculate_indicators(engine, [definition])
    second = recalculate_indicators(engine, [definition])
    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM indicator_values WHERE indicator_id='vix'")).scalar_one()
        missing = connection.execute(text("SELECT data_quality, normalized_value, confidence FROM indicator_values WHERE observation_date='2026-01-13'")).one()
        version_count = connection.execute(text("SELECT COUNT(*) FROM scoring_versions WHERE version='0.11.0'")).scalar_one()

    assert first["methodology_version"] == second["methodology_version"] == "0.11.0"
    assert count == 90
    assert missing.data_quality == "missing"
    assert missing.normalized_value is None
    assert missing.confidence == 0
    assert version_count == 1
    engine.dispose()

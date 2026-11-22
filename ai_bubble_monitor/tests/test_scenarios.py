from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.services.collection import seed_catalog
from app.services.scoring import _scenario_trends, recalculate_scenarios, scenario_catalog, scenario_history


def test_scenarios_apply_coverage_gates_and_keep_immutable_history(tmp_path):
    definitions = load_definitions()
    database_path = tmp_path / "scenario.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, definitions)
    current_date = date.today().isoformat()
    now = datetime.now(UTC).isoformat()
    values = {
        "sp500": 35.0, "nasdaq_composite": 30.0, "high_yield_oas": 70.0,
        "investment_grade_oas": 65.0, "us_2y_yield": 75.0, "us_10y_yield": 70.0,
        "fed_funds_rate": 72.0, "nfci": 80.0, "financial_stress_index": 77.0,
        "vix": 90.0, "bank_lending_tightening": 82.0, "industrial_production": 35.0,
        "initial_jobless_claims": 65.0, "wti_crude_oil": 75.0, "henry_hub_natural_gas": 68.0,
    }
    definitions_by_id = {item.id: item for item in definitions}
    with engine.begin() as connection:
        for indicator_id, normalized in values.items():
            definition = definitions_by_id[indicator_id]
            connection.execute(text("""INSERT INTO raw_observations
                (source_id, indicator_id, observation_date, fetched_at, raw_value, raw_payload, source_reference)
                VALUES (:source, :indicator, :day, :now, :raw, '{}', :url)"""), {
                "source": definition.source, "indicator": indicator_id, "day": current_date,
                "now": now, "raw": 1.0, "url": "https://example.test/source",
            })
            connection.execute(text("""INSERT INTO indicator_values
                (indicator_id, observation_date, raw_value, normalized_value, confidence, data_quality, calculated_at)
                VALUES (:indicator, :day, 1, :normalized, .9, 'available', :now)"""), {
                "indicator": indicator_id, "day": current_date, "normalized": normalized, "now": now,
            })

    first = recalculate_scenarios(engine, definitions)
    second = recalculate_scenarios(engine, definitions)
    catalog = {item["id"]: item for item in scenario_catalog(engine)}
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT scenario_id, COUNT(*) AS count FROM scenario_scores GROUP BY scenario_id")).all()

    assert first["scores_saved"] == second["scores_saved"] == 6
    assert {row.scenario_id: row.count for row in rows} == {
        "boom_continuation": 2, "prolonged_repricing": 2, "credit_crunch": 2,
        "macro_crisis": 2, "fragility_index": 2, "current_stress_index": 2,
    }
    assert catalog["macro_crisis"]["status"] == "low_confidence"
    assert catalog["macro_crisis"]["coverage"] > 0.60
    assert catalog["macro_crisis"]["score"] is not None
    assert catalog["credit_crunch"]["status"] == "unavailable"
    assert catalog["fragility_index"]["score"] is None
    assert catalog["current_stress_index"]["status"] == "low_confidence"
    assert catalog["current_stress_index"]["score"] is not None
    assert len(scenario_history(engine, "macro_crisis", 30)) == 1
    engine.dispose()


def test_scenario_trends_report_changes_and_persistence_without_filling_gaps(tmp_path):
    database_path = tmp_path / "trends.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    now = datetime.now(UTC).replace(microsecond=0)
    with engine.begin() as connection:
        for age in range(40, -1, -1):
            calculated_at = (now - timedelta(days=age)).isoformat()
            score = 40.0 if age > 25 else 65.0
            connection.execute(text("""INSERT INTO scenario_scores
                (scenario_id, score, confidence, coverage, methodology_version, calculated_at, status)
                VALUES ('macro_crisis', :score, .8, .9, '0.4.0', :calculated_at, 'available')"""), {
                "score": score, "calculated_at": calculated_at,
            })
    latest = {"score": 65.0, "calculated_at": now.isoformat()}
    with engine.connect() as connection:
        trends = _scenario_trends(connection, "macro_crisis", latest, {
            "persistence_threshold": 60, "persistence_lookback_days": 30,
            "persistence_required_days": 20,
        })
        unavailable = _scenario_trends(connection, "macro_crisis", {
            "score": None, "calculated_at": now.isoformat(),
        }, {"persistence_threshold": 60, "persistence_lookback_days": 30,
            "persistence_required_days": 20})
    assert trends["score_changes"]["7d"]["value"] == 0.0
    assert trends["score_changes"]["30d"]["value"] == 25.0
    assert trends["score_changes"]["90d"] is None
    assert trends["persistence"]["days_above_threshold"] == 26
    assert trends["persistence"]["current_streak_days"] == 26
    assert trends["persistence"]["persistent"] is True
    assert len(trends["history"]) == 41
    assert unavailable["persistence"]["current_streak_days"] == 0
    engine.dispose()

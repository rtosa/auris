from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import text

from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.services.collection import seed_catalog
from app.services.scoring import (_analyst_revision_inputs, _reported_ai_revenue_growth_inputs,
                                  _scenario_trends, _score_scenario_inputs,
                                  _score_components, load_scoring_config, recalculate_scenarios, scenario_catalog,
                                  scenario_history)


def test_all_scenario_and_composite_weights_are_normalized():
    config = load_scoring_config()
    definitions = {**config["scenarios"], **config["composites"]}
    for scenario_id, spec in definitions.items():
        weighted_items = spec.get("components", spec.get("inputs", []))
        assert weighted_items, scenario_id
        assert abs(sum(float(item["weight"]) for item in weighted_items) - 1.0) < 1e-9, scenario_id
    company_config = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "company_scoring.yaml").read_text(encoding="utf-8"))
    assert abs(sum(float(weight) for weight in company_config["weights"].values()) - 1.0) < 1e-9


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
    assert catalog["macro_crisis"]["status"] == "partial_coverage"
    assert catalog["macro_crisis"]["coverage"] > 0.60
    assert catalog["macro_crisis"]["confidence"] >= 0.90
    assert catalog["macro_crisis"]["score"] is not None
    assert catalog["credit_crunch"]["status"] == "unavailable"
    assert catalog["fragility_index"]["score"] is None
    assert catalog["current_stress_index"]["status"] == "partial_coverage"
    assert catalog["current_stress_index"]["score"] is not None
    assert len(scenario_history(engine, "macro_crisis", 30)) == 1
    engine.dispose()


def test_coverage_and_confidence_measure_different_things():
    result = _score_components([{"id": "test", "name": "Test", "weight": 1.0,
                                 "direction": "risk", "indicators": ["half-covered"]}],
        {"half-covered": {"latest_normalized_value": 40, "normalized_confidence": 0.9,
                          "input_coverage": 0.5, "data_status": "fresh"}},
        {"minimum_confidence": 0.6, "status_coverage_threshold": 0.8}, 0.5, 2.0)
    assert result["coverage"] == 0.5
    assert result["confidence"] == 0.9
    assert result["status"] == "partial_coverage"

    low_quality = _score_components([{"id": "test", "name": "Test", "weight": 1.0,
                                      "direction": "risk", "indicators": ["weak-source"]}],
        {"weak-source": {"latest_normalized_value": 40, "normalized_confidence": 0.4,
                          "input_coverage": 1.0, "data_status": "fresh"}},
        {"minimum_confidence": 0.6, "status_coverage_threshold": 0.8}, 0.5, 2.0)
    assert low_quality["coverage"] == 1.0
    assert low_quality["status"] == "low_confidence"


def test_composite_preserves_underlying_scenario_coverage():
    result = _score_scenario_inputs({"inputs": [
        {"id": "a", "name": "A", "weight": 0.4},
        {"id": "b", "name": "B", "weight": 0.6},
    ]}, {
        "a": {"score": 40, "confidence": 0.8, "coverage": 0.5, "status": "partial_coverage"},
        "b": {"score": 60, "confidence": 0.9, "coverage": 1.0, "status": "available"},
    }, {"minimum_coverage": 0.5, "maximum_reweight_factor": 2.0,
        "minimum_confidence": 0.6, "status_coverage_threshold": 0.8})
    assert result["coverage"] == 0.8
    assert result["confidence"] == 0.86
    assert [row["weight"] for row in result["component_rows"]] == [0.2, 0.6]


def test_analyst_revisions_are_point_in_time_and_period_matched(tmp_path):
    database_path = tmp_path / "revisions.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    today = date.today()
    now = datetime.now(UTC).isoformat()
    with engine.begin() as connection:
        for company_id in ("msft", "nvda", "amzn", "googl"):
            for age, value in ((100, 10.0), (31, 10.0), (0, 9.0)):
                snapshot = (today - timedelta(days=age)).isoformat()
                for metric in ("analyst_eps", "analyst_revenue"):
                    connection.execute(text("""INSERT INTO analyst_estimates
                        (company_id, period_id, metric, as_of, period_type, report_date, mean, num_estimates, fetched_at)
                        VALUES (:company, 'Q1-FY27', :metric, :as_of, 'quarterly', :report, :mean, 5, :fetched)"""), {
                        "company": company_id, "metric": metric, "as_of": snapshot,
                        "report": (today + timedelta(days=30)).isoformat(), "mean": value, "fetched": now,
                    })
            # A different quarter has a very different consensus level; it must
            # not be used as the prior snapshot for Q1.
            connection.execute(text("""INSERT INTO analyst_estimates
                (company_id, period_id, metric, as_of, period_type, report_date, mean, num_estimates, fetched_at)
                VALUES (:company, 'Q2-FY27', 'analyst_eps', :as_of, 'quarterly', :report, 999, 5, :fetched)"""), {
                "company": company_id, "as_of": today.isoformat(),
                "report": (today + timedelta(days=90)).isoformat(), "fetched": now,
            })
    result = _analyst_revision_inputs(engine, {"source_confidence": {"fiscalai": 0.75}})["analyst_revisions_risk"]
    assert result["data_status"] == "fresh"
    assert result["input_coverage"] == 0.5
    assert result["latest_normalized_value"] == 100.0
    engine.dispose()


def test_reported_ai_revenue_growth_requires_comparable_snapshots(tmp_path):
    database_path = tmp_path / "ai-revenue.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    now = datetime.now(UTC)
    with engine.begin() as connection:
        for age, amount, period in ((90, 80.0, "Q1"), (0, 100.0, "Q2")):
            connection.execute(text("""INSERT INTO events
                (title, summary, company_id, category, published_at, source_url, confidence,
                 guidance_period, guidance_lower, guidance_upper, guidance_unit)
                VALUES ('NVIDIA DC revenue', '', 'nvda', 'ai_compute_revenue', :published,
                 'https://example.test/' || :period, .85, :period, :amount, :amount, 'USD_reported_period')"""), {
                "published": (now.date() - timedelta(days=age)).isoformat(), "period": period, "amount": amount,
            })
        connection.execute(text("""INSERT INTO events
            (title, summary, company_id, category, published_at, source_url, confidence,
             guidance_period, guidance_lower, guidance_upper, guidance_unit)
            VALUES ('Amazon AI revenue', '', 'amzn', 'ai_monetization', :published,
             'https://example.test/amzn', .85, 'Q2', 25, 25, 'USD_annualized')"""), {
            "published": now.date().isoformat(),
        })
    values = _reported_ai_revenue_growth_inputs(engine, {"source_confidence": {"sec_event": 0.85}})
    compute = values["ai_compute_revenue_growth_risk"]
    monetization = values["ai_monetization_growth_risk"]
    assert compute["latest_normalized_value"] == 25.0
    assert compute["input_coverage"] == 0.5
    assert compute["data_status"] == "fresh"
    assert monetization["latest_normalized_value"] is None
    assert monetization["data_status"] == "unavailable"
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

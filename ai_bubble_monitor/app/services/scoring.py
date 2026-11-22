from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Engine, text

from app.collectors.definitions import IndicatorDefinition
from app.indicators.normalization import normalize_series
from app.services.collection import indicator_catalog
from app.services.companies import load_company_universe

SCORING_CONFIG = Path(__file__).resolve().parents[2] / "config" / "scoring.yaml"


def load_scoring_config() -> dict[str, Any]:
    config = yaml.safe_load(SCORING_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("defaults"), dict):
        raise ValueError("Invalid scoring configuration")
    return config


def recalculate_indicators(engine: Engine, definitions: list[IndicatorDefinition]) -> dict[str, Any]:
    config = load_scoring_config()
    version = str(config["version"])
    defaults = config["defaults"]
    calculated_at = datetime.now(UTC).isoformat()
    normalized_count = 0
    insufficient_count = 0
    missing_count = 0

    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO scoring_versions (version, configuration, created_at, description)
            VALUES (:version, :configuration, :created_at, :description) ON CONFLICT(version) DO NOTHING"""), {
            "version": version,
            "configuration": json.dumps(config, sort_keys=True),
            "created_at": calculated_at,
            "description": str(config.get("description", "Indicator normalization")),
        })

        for definition in definitions:
            if not definition.enabled:
                continue
            observations = connection.execute(text("""SELECT observation_date, raw_value FROM raw_observations
                WHERE indicator_id=:indicator_id AND source_id=:source_id ORDER BY observation_date"""), {
                "indicator_id": definition.id, "source_id": definition.source,
            }).mappings().all()
            if not observations:
                continue
            points = [(date.fromisoformat(str(row["observation_date"])), row["raw_value"]) for row in observations]
            normalized = normalize_series(
                points,
                definition.normalization_method or str(defaults["method"]),
                definition.direction,
                definition.lookback_days or int(defaults["lookback_days"]),
                definition.minimum_observations or int(defaults["minimum_observations"]),
            )
            source_confidence = float(defaults["source_confidence"].get(definition.source, 0.75))
            expected = _expected_observations(definition)
            rows = []
            valid_history = 0
            for observation, (observation_date, _), normalized_value in zip(observations, points, normalized):
                raw_value = observation["raw_value"]
                if raw_value is not None:
                    valid_history += 1
                if raw_value is None:
                    quality, confidence = "missing", 0.0
                    missing_count += 1
                elif normalized_value is None:
                    quality, confidence = "insufficient_history", 0.0
                    insufficient_count += 1
                else:
                    quality = "available"
                    confidence = source_confidence * min(valid_history / expected, 1.0)
                    normalized_count += 1
                rows.append({
                    "indicator_id": definition.id,
                    "observation_date": observation_date.isoformat(),
                    "raw_value": raw_value,
                    "normalized_value": normalized_value,
                    "confidence": round(confidence, 4),
                    "data_quality": quality,
                    "calculated_at": calculated_at,
                })
            connection.execute(text("""INSERT INTO indicator_values
                (indicator_id, observation_date, raw_value, normalized_value, confidence, data_quality, calculated_at)
                VALUES (:indicator_id, :observation_date, :raw_value, :normalized_value, :confidence, :data_quality, :calculated_at)
                ON CONFLICT(indicator_id, observation_date) DO UPDATE SET raw_value=excluded.raw_value,
                normalized_value=excluded.normalized_value, confidence=excluded.confidence,
                data_quality=excluded.data_quality, calculated_at=excluded.calculated_at"""), rows)

    return {
        "methodology_version": version,
        "indicators_processed": sum(1 for definition in definitions if definition.enabled),
        "normalized_observations": normalized_count,
        "insufficient_history": insufficient_count,
        "missing_observations": missing_count,
        "completed_at": calculated_at,
        "scenario_scores": "calculated_separately",
    }


def recalculate_scenarios(
    engine: Engine,
    definitions: list[IndicatorDefinition],
    scenario_id: str | None = None,
) -> dict[str, Any]:
    config = load_scoring_config()
    version = str(config["version"])
    defaults = config["defaults"]
    current_indicators = {item["id"]: item for item in indicator_catalog(engine)}
    current_indicators.update(_company_group_scores(engine))
    calculated_at = datetime.now(UTC).isoformat()
    configured = {**config.get("scenarios", {}), **config.get("composites", {})}
    if scenario_id and scenario_id not in configured:
        raise KeyError(scenario_id)
    if scenario_id is None:
        scenario_targets = list(config.get("scenarios", {}))
        composite_targets = list(config.get("composites", {}))
    elif scenario_id in config.get("scenarios", {}):
        scenario_targets = [scenario_id]
        composite_targets = []
    elif scenario_id == "fragility_index":
        # The fragility composite depends on the three scenario snapshots calculated now.
        scenario_targets = list(config.get("scenarios", {}))
        composite_targets = [scenario_id]
    else:
        scenario_targets = []
        composite_targets = [scenario_id]
    computed: dict[str, dict[str, Any]] = {}
    saved = 0

    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO scoring_versions (version, configuration, created_at, description)
            VALUES (:version, :configuration, :created_at, :description) ON CONFLICT(version) DO NOTHING"""), {
            "version": version,
            "configuration": json.dumps(config, sort_keys=True),
            "created_at": calculated_at,
            "description": str(config.get("description", "Scenario scoring")),
        })

        for target_id in scenario_targets:
            spec = config["scenarios"][target_id]
            result = _score_indicator_components(spec["components"], current_indicators, defaults)
            score_id = _append_score(connection, target_id, result, version, calculated_at)
            _append_components(connection, score_id, result["component_rows"])
            result.update({"id": target_id, "name": spec["name"], "calculated_at": calculated_at})
            computed[target_id] = result
            saved += 1

        for target_id in composite_targets:
            spec = config["composites"][target_id]
            if target_id == "fragility_index":
                result = _score_scenario_inputs(spec, computed, defaults)
            else:
                result = _score_indicator_components(spec["components"], current_indicators, defaults,
                                                     minimum_coverage=float(spec.get("min_coverage", defaults["minimum_coverage"])),
                                                     maximum_reweight_factor=float(spec.get("max_reweight_factor", defaults["maximum_reweight_factor"])))
            score_id = _append_score(connection, target_id, result, version, calculated_at)
            _append_components(connection, score_id, result["component_rows"])
            result.update({"id": target_id, "name": spec["name"], "calculated_at": calculated_at})
            computed[target_id] = result
            saved += 1

    return {"methodology_version": version, "scores_saved": saved, "completed_at": calculated_at,
            "items": [{key: value for key, value in item.items() if key != "component_rows"}
                      for item in computed.values()]}


def _score_indicator_components(
    components: list[dict[str, Any]],
    indicator_values: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
    minimum_coverage: float | None = None,
    maximum_reweight_factor: float | None = None,
) -> dict[str, Any]:
    return _score_components(components, indicator_values, defaults,
                             float(minimum_coverage if minimum_coverage is not None else defaults["minimum_coverage"]),
                             float(maximum_reweight_factor if maximum_reweight_factor is not None else defaults["maximum_reweight_factor"]))


def _company_group_scores(engine: Engine) -> dict[str, dict[str, Any]]:
    """Build slow-moving, coverage-aware company-group inputs from SEC scores."""
    companies = load_company_universe()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for company in companies:
        grouped.setdefault(company["group"], []).append(company)
    result: dict[str, dict[str, Any]] = {}
    with engine.connect() as connection:
        rows = connection.execute(text("""SELECT s.company_id, s.score, s.coverage, s.end_date
                FROM company_scores s JOIN (SELECT company_id, MAX(id) AS id FROM company_scores GROUP BY company_id) latest
                ON latest.id=s.id""")).mappings().all()
        by_id = {row["company_id"]: row for row in rows}
        for group, members in grouped.items():
            usable = []
            for company in members:
                row = by_id.get(company["id"])
                if not row or row["score"] is None or not row["end_date"]:
                    continue
                try:
                    age_days = (date.today() - date.fromisoformat(str(row["end_date"]))).days
                except ValueError:
                    continue
                if 0 <= age_days <= 550:
                    usable.append(row)
            key = f"company_group:{group}"
            if usable:
                share = len(usable) / len(members)
                result[key] = {"id": key,
                    "latest_normalized_value": sum(float(row["score"]) for row in usable) / len(usable),
                    "normalized_confidence": sum(float(row["coverage"] or 0) for row in usable) / len(usable),
                    "input_coverage": share,
                    "data_status": "fresh" if share >= 0.5 else "stale",
                    "company_count": len(usable), "group_size": len(members),
                    "source_label": f"SEC company scores: {group} ({len(usable)}/{len(members)} recent)"}
            else:
                result[key] = {"id": key, "latest_normalized_value": None,
                    "normalized_confidence": 0.0, "data_status": "unavailable",
                    "company_count": 0, "group_size": len(members)}
    result.update(_company_group_metric_scores(engine, grouped))
    return result


def _company_group_metric_scores(
    engine: Engine, grouped: dict[str, list[dict[str, str]]],
) -> dict[str, dict[str, Any]]:
    """Calculate transparent group proxies from matching-period SEC facts."""
    config = load_scoring_config().get("company_group_metrics", {})
    max_age = int(config.get("max_reporting_age_days", 550))
    minimum_coverage = float(config.get("minimum_group_coverage", 0.5))
    source_confidence = float(config.get("source_confidence", 0.9))
    maturity_cap = float(config.get("debt_maturity_ratio_cap", 1.0))
    commitment_cap = float(config.get("purchase_obligation_to_assets_ratio_cap", 0.5))
    needed = ("current_debt", "long_term_debt", "total_assets", "debt_maturities_next_12m", "purchase_obligations")
    metric_list = ", ".join(f"'{metric}'" for metric in needed)
    with engine.connect() as connection:
        rows = connection.execute(text(f"""SELECT company_id, metric, value, end_date
            FROM company_financials WHERE metric IN ({metric_list}) AND value IS NOT NULL AND end_date IS NOT NULL
            ORDER BY company_id, end_date DESC, filing_date DESC, id DESC""")).mappings().all()
    facts: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        facts.setdefault(str(row["company_id"]), {}).setdefault(str(row["end_date"]), {}).setdefault(
            str(row["metric"]), float(row["value"]))

    outputs: dict[str, dict[str, Any]] = {}
    metric_specs = (
        ("company_maturity_risk", "Debt due in next 12 months / total debt", maturity_cap),
        ("company_commitment_risk", "Unrecorded purchase obligations / assets", commitment_cap),
    )
    today = date.today()
    for group, members in grouped.items():
        for prefix, description, ratio_cap in metric_specs:
            scores = []
            for company in members:
                periods = facts.get(company["id"], {})
                for end_date in sorted(periods, reverse=True):
                    values = periods[end_date]
                    try:
                        age = (today - date.fromisoformat(end_date)).days
                    except ValueError:
                        continue
                    if age < 0 or age > max_age:
                        continue
                    if prefix == "company_maturity_risk":
                        if not {"current_debt", "long_term_debt", "debt_maturities_next_12m"} <= values.keys():
                            continue
                        denominator = values["current_debt"] + values["long_term_debt"]
                        numerator = values["debt_maturities_next_12m"]
                    else:
                        if not {"total_assets", "purchase_obligations"} <= values.keys():
                            continue
                        denominator = values["total_assets"]
                        numerator = values["purchase_obligations"]
                    if denominator < 0 or numerator < 0 or (denominator == 0 and numerator > 0):
                        continue
                    ratio = 0.0 if denominator == 0 else numerator / denominator
                    score = max(0.0, min(100.0, ratio / ratio_cap * 100.0)) if ratio_cap > 0 else 0.0
                    scores.append((company["id"], score))
                    break
            share = len(scores) / len(members) if members else 0.0
            key = f"{prefix}:{group}"
            label = f"SEC {description}: {group} ({len(scores)}/{len(members)} recent)"
            outputs[key] = {
                "id": key,
                "latest_normalized_value": sum(score for _, score in scores) / len(scores) if scores else None,
                "normalized_confidence": source_confidence if scores else 0.0,
                "input_coverage": share,
                "data_status": "fresh" if share >= minimum_coverage else "stale" if scores else "unavailable",
                "company_count": len(scores), "group_size": len(members), "source_label": label,
            }
    return outputs


def _score_components(
    components: list[dict[str, Any]], indicator_values: dict[str, dict[str, Any]],
    defaults: dict[str, Any], minimum_coverage: float, maximum_reweight_factor: float,
) -> dict[str, Any]:
    total_weight = sum(float(component["weight"]) for component in components)
    if not components or not 0.999 <= total_weight <= 1.001:
        raise ValueError("Configured component weights must sum to 1.0")
    weighted_sum = 0.0
    available_weight = 0.0
    confidence_sum = 0.0
    coverage = 0.0
    component_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for component in components:
        component_weight = float(component["weight"])
        coverage_cap = max(0.0, min(float(component.get("coverage_cap", 1.0)), 1.0))
        effective_weight = component_weight * coverage_cap
        indicator_ids = list(component.get("indicators", []))
        component_sum = 0.0
        component_available = 0.0
        component_confidence = 0.0
        if not indicator_ids:
            component_rows.append({"component_id": component["id"], "indicator_id": None,
                                   "input_ref": None, "normalized_value": None, "base_weight": component_weight})
        for indicator_id in indicator_ids:
            value = indicator_values.get(indicator_id)
            base_weight = effective_weight / len(indicator_ids)
            if value is not None and value.get("input_coverage") is not None:
                base_weight *= max(0.0, min(float(value["input_coverage"]), 1.0))
            persisted_indicator_id = None if indicator_id.startswith((
                "company_group:", "company_maturity_risk:", "company_commitment_risk:",
            )) else indicator_id
            raw_score = value.get("latest_normalized_value") if value else None
            usable = value is not None and value.get("data_status") == "fresh" and raw_score is not None
            if not usable:
                component_rows.append({"component_id": component["id"], "indicator_id": persisted_indicator_id,
                                       "input_ref": value.get("source_label", indicator_id) if value else indicator_id,
                                       "normalized_value": None, "base_weight": base_weight})
                continue
            score_value = float(raw_score)
            if component.get("direction") == "favorable":
                score_value = 100.0 - score_value
            confidence = float(value.get("normalized_confidence") or 0)
            component_sum += score_value * base_weight
            component_available += base_weight
            component_confidence += confidence * base_weight
            weighted_sum += score_value * base_weight
            confidence_sum += confidence * base_weight
            available_weight += base_weight
            component_rows.append({"component_id": component["id"], "indicator_id": persisted_indicator_id,
                                   "input_ref": value.get("source_label", indicator_id),
                                   "normalized_value": score_value, "base_weight": base_weight})
        coverage += component_available
        component_score = component_sum / component_available if component_available else None
        component_coverage = component_available / component_weight if component_weight else 0.0
        summaries.append({
            "id": component["id"], "name": component["name"], "weight": component_weight,
            "score": round(component_score, 2) if component_score is not None else None,
            "coverage": round(component_coverage, 4),
            "status": "available" if component_coverage == 1 else "partial" if component_coverage else "unavailable",
            "confidence": round(component_confidence / component_available, 4) if component_available else 0.0,
        })

    coverage = min(coverage / total_weight, 1.0)
    reweight = 1.0 / coverage if coverage else float("inf")
    score = weighted_sum / available_weight if available_weight else None
    confidence = (confidence_sum / available_weight) * coverage if available_weight else 0.0
    can_score = score is not None and coverage >= minimum_coverage and reweight <= maximum_reweight_factor
    status = "unavailable" if not can_score else "available" if coverage == 1.0 and confidence >= float(defaults["minimum_confidence"]) else "low_confidence"
    if not can_score:
        score = None
    for row in component_rows:
        base_weight = float(row.pop("base_weight"))
        row["weight"] = round(base_weight, 8)
        row["contribution"] = round(float(row["normalized_value"]) * base_weight / available_weight, 6) if can_score and row["normalized_value"] is not None else None
    return {"score": round(score, 2) if score is not None else None, "confidence": round(confidence, 4),
            "coverage": round(coverage, 4), "status": status, "components": summaries,
            "component_rows": component_rows, "reweight_factor": round(reweight, 4) if coverage else None}


def _score_scenario_inputs(spec: dict[str, Any], computed: dict[str, dict[str, Any]], defaults: dict[str, Any]) -> dict[str, Any]:
    components = []
    for item in spec["inputs"]:
        source = computed.get(item["id"])
        score = source.get("score") if source else None
        confidence = float(source.get("confidence", 0)) if source else 0.0
        components.append({"id": item["id"], "name": item["name"], "weight": float(item["weight"]),
                           "score": score, "confidence": confidence,
                           "coverage": 1.0 if score is not None else 0.0,
                           "status": source.get("status", "unavailable") if source else "unavailable"})
    total_weight = sum(item["weight"] for item in components)
    available = [item for item in components if item["score"] is not None]
    coverage = sum(item["weight"] for item in available) / total_weight if total_weight else 0.0
    available_weight = sum(item["weight"] for item in available)
    reweight = 1 / coverage if coverage else float("inf")
    can_score = bool(available) and coverage >= float(spec.get("min_coverage", defaults["minimum_coverage"])) and reweight <= float(spec.get("max_reweight_factor", defaults["maximum_reweight_factor"]))
    score = sum(item["score"] * item["weight"] for item in available) / available_weight if can_score else None
    confidence = (sum(item["confidence"] * item["weight"] for item in available) / available_weight) * coverage if available else 0.0
    status = "unavailable" if not can_score else "available" if coverage == 1 and confidence >= float(defaults["minimum_confidence"]) else "low_confidence"
    rows = [{"component_id": item["id"], "indicator_id": None, "input_ref": item["id"], "normalized_value": item["score"],
             "base_weight": item["weight"], "weight": item["weight"],
             "contribution": round(item["score"] * item["weight"] / available_weight, 6) if can_score and item["score"] is not None else None}
            for item in components]
    return {"score": round(score, 2) if score is not None else None, "confidence": round(confidence, 4),
            "coverage": round(coverage, 4), "status": status, "components": components,
            "component_rows": rows, "reweight_factor": round(reweight, 4) if coverage else None}


def _append_score(connection, scenario_id: str, result: dict[str, Any], version: str, calculated_at: str) -> int:
    return int(connection.execute(text("""INSERT INTO scenario_scores
        (scenario_id, score, confidence, coverage, methodology_version, calculated_at, status)
        VALUES (:scenario_id, :score, :confidence, :coverage, :version, :calculated_at, :status)"""), {
        "scenario_id": scenario_id, "score": result["score"], "confidence": result["confidence"],
        "coverage": result["coverage"], "version": version, "calculated_at": calculated_at,
        "status": result["status"],
    }).lastrowid)


def _append_components(connection, score_id: int, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    connection.execute(text("""INSERT INTO scenario_components
        (scenario_score_id, component_id, indicator_id, input_ref, normalized_value, weight, contribution)
        VALUES (:score_id, :component_id, :indicator_id, :input_ref, :normalized_value, :weight, :contribution)"""), [
        {"score_id": score_id, **row} for row in rows
    ])


def scenario_catalog(engine: Engine) -> list[dict[str, Any]]:
    config = load_scoring_config()
    definitions = {**config.get("scenarios", {}), **config.get("composites", {})}
    output = []
    with engine.connect() as connection:
        for scenario_id, spec in definitions.items():
            score = connection.execute(text("""SELECT id AS score_row_id, score, confidence, coverage,
                methodology_version, calculated_at, status FROM scenario_scores
                WHERE scenario_id=:scenario_id ORDER BY id DESC LIMIT 1"""), {"scenario_id": scenario_id}).mappings().first()
            item = {"id": scenario_id, "name": spec["name"], "score": None,
                    "confidence": 0.0, "coverage": 0.0, "status": "pending",
                    "methodology_version": str(config["version"]), "calculated_at": None,
                    "classification": None, "top_contributors": [], "components": []}
            if score:
                item.update(dict(score))
                item.update(_scenario_trends(connection, scenario_id, score, config["defaults"]))
                classification = next((entry["label"] for entry in config["defaults"]["classifications"]
                                       if score["score"] is not None and float(score["score"]) <= float(entry["max"])), None)
                item["classification"] = classification
                components = connection.execute(text("""SELECT component_id, indicator_id, input_ref,
                    normalized_value, weight, contribution FROM scenario_components
                    WHERE scenario_score_id=:score_id ORDER BY id"""), {"score_id": score["score_row_id"]}).mappings().all()
                grouped: dict[str, dict[str, Any]] = {}
                for component in components:
                    group = grouped.setdefault(component["component_id"], {"id": component["component_id"], "score_numerator": 0.0, "available_weight": 0.0, "contribution": 0.0, "weight": 0.0, "inputs": []})
                    group["weight"] += float(component["weight"] or 0)
                    if component["contribution"] is not None:
                        group["contribution"] += float(component["contribution"])
                    if component["normalized_value"] is not None:
                        group["score_numerator"] += float(component["normalized_value"]) * float(component["weight"] or 0)
                        group["available_weight"] += float(component["weight"] or 0)
                    group["inputs"].append({"indicator_id": component["indicator_id"], "source": component["input_ref"], "value": component["normalized_value"], "weight": component["weight"]})
                for group in grouped.values():
                    group["score"] = round(group["score_numerator"] / group["available_weight"], 2) if group["available_weight"] else None
                    group["contribution"] = round(group["contribution"], 2)
                    definition = next((entry for entry in (spec.get("components", []) + spec.get("inputs", [])) if entry["id"] == group["id"]), None)
                    group["name"] = definition.get("name", group["id"]) if definition else group["id"]
                    group["configured_weight"] = definition.get("weight", 0) if definition else 0
                    group["coverage"] = round(group["available_weight"] / float(group["configured_weight"]), 4) if group["configured_weight"] else 0.0
                    group["status"] = "available" if group["coverage"] == 1 else "partial" if group["coverage"] else "unavailable"
                    del group["score_numerator"]
                item["components"] = list(grouped.values())
                item["top_contributors"] = sorted((group for group in grouped.values() if group["contribution"] > 0),
                                                   key=lambda value: value["contribution"], reverse=True)[:3]
            output.append(item)
    return output


def _scenario_trends(connection, scenario_id: str, latest: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    """Summarize daily snapshots without interpolating missing days or scores."""
    rows = connection.execute(text("""SELECT id, score, status, coverage, calculated_at FROM (
          SELECT s.*, ROW_NUMBER() OVER (PARTITION BY date(calculated_at) ORDER BY id DESC) AS day_rank
          FROM scenario_scores s WHERE scenario_id=:scenario_id
            AND calculated_at >= datetime('now', '-90 days')
        ) WHERE day_rank=1 ORDER BY calculated_at"""), {"scenario_id": scenario_id}).mappings().all()
    dated = [(datetime.fromisoformat(str(row["calculated_at"]).replace("Z", "+00:00")), row)
             for row in rows if row["score"] is not None]
    latest_time = datetime.fromisoformat(str(latest["calculated_at"]).replace("Z", "+00:00"))
    latest_value = float(latest["score"]) if latest["score"] is not None else None
    changes: dict[str, Any] = {}
    for horizon in (7, 30, 90):
        target = latest_time.timestamp() - horizon * 86400
        prior = [(when, row) for when, row in dated if when.timestamp() <= target]
        reference = prior[-1] if prior else None
        # Avoid presenting a distant observation as a fixed-period comparison.
        if reference and (latest_time - reference[0]).days <= horizon + 3 and latest_value is not None:
            changes[f"{horizon}d"] = {
                "value": round(latest_value - float(reference[1]["score"]), 2),
                "reference_at": reference[1]["calculated_at"],
            }
        else:
            changes[f"{horizon}d"] = None

    threshold = float(defaults.get("persistence_threshold", 60))
    lookback = int(defaults.get("persistence_lookback_days", 30))
    required = int(defaults.get("persistence_required_days", 20))
    cutoff = latest_time.timestamp() - lookback * 86400
    window = [(when, row) for when, row in dated if when.timestamp() >= cutoff]
    above = [(when, row) for when, row in window if float(row["score"]) >= threshold]
    streak = 0
    previous_day = None
    if latest_value is not None and latest_value >= threshold:
        for when, row in reversed(window):
            day = when.date()
            if float(row["score"]) < threshold or (previous_day is not None and (previous_day - day).days != 1):
                break
            streak += 1
            previous_day = day
    return {
        "score_changes": changes,
        "persistence": {
            "threshold": threshold,
            "lookback_days": lookback,
            "required_days": required,
            "days_above_threshold": len(above),
            "current_streak_days": streak,
            "persistent": len(above) >= required,
        },
        "history": [{"date": when.date().isoformat(), "score": float(row["score"]),
                     "status": row["status"]} for when, row in dated],
    }


def scenario_history(engine: Engine, scenario_id: str, days: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(text("""SELECT id, scenario_id, score, confidence, coverage,
            methodology_version, calculated_at, status FROM (
              SELECT s.*, ROW_NUMBER() OVER (PARTITION BY date(calculated_at) ORDER BY id DESC) AS day_rank
              FROM scenario_scores s WHERE scenario_id=:scenario_id
                AND calculated_at >= datetime('now', :date_shift)
            ) WHERE day_rank=1 ORDER BY calculated_at"""), {
            "scenario_id": scenario_id, "date_shift": f"-{days} days",
        }).mappings().all()
    return [dict(row) for row in rows]


def indicator_history(engine: Engine, indicator_id: str, days: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(text("""SELECT r.observation_date, r.raw_value,
            i.normalized_value, i.confidence, i.data_quality, r.source_id, r.source_reference
            FROM raw_observations r LEFT JOIN indicator_values i
              ON i.indicator_id=r.indicator_id AND i.observation_date=r.observation_date
            WHERE r.indicator_id=:indicator_id
              AND r.observation_date >= date('now', :date_shift)
            ORDER BY r.observation_date"""), {
            "indicator_id": indicator_id, "date_shift": f"-{days} days",
        }).mappings().all()
    return [dict(row) for row in rows]


def _expected_observations(definition: IndicatorDefinition) -> int:
    days = definition.lookback_days
    if definition.frequency == "daily":
        return max(definition.minimum_observations, min(days, 365))
    if definition.frequency == "weekly":
        return max(definition.minimum_observations, min(days // 7, 52))
    if definition.frequency == "monthly":
        return max(definition.minimum_observations, min(days // 30, 60))
    return definition.minimum_observations

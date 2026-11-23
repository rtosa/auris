from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, text

from app.collectors.definitions import IndicatorDefinition
from app.collectors.coinpaprika import CoinPaprikaCollector
from app.collectors.fred import CollectorError, FredCollector, Observation
from app.collectors.proskauer import ProskauerCollector
from app.collectors.fiscalai import COMPANY_KEYS, FiscalAICollector, collect_fiscalai

LOGGER = logging.getLogger(__name__)
FRED_SOURCE = {
    "id": "fred",
    "name": "Federal Reserve Economic Data (FRED)",
    "source_type": "economic_api",
    "endpoint": "https://api.stlouisfed.org/fred/series/observations",
    "enabled": True,
}
COINPAPRIKA_SOURCE = {
    "id": "coinpaprika", "name": "CoinPaprika", "source_type": "market_data_api",
    "endpoint": "https://api.coinpaprika.com/v1/tickers", "enabled": True,
}
SEC_SOURCE = {
    "id": "sec", "name": "SEC EDGAR", "source_type": "financial_filings_api",
    "endpoint": "https://data.sec.gov/submissions/", "enabled": True,
}
PROSKAUER_SOURCE = {
    "id": "proskauer", "name": "Proskauer Private Credit Default Index",
    "source_type": "public_html_release", "endpoint": "https://www.proskauer.com/browse-insights?primary_type=8", "enabled": True,
}
FISCALAI_SOURCE = {
    "id": "fiscalai", "name": "Fiscal.ai Street Consensus (Zacks)",
    "source_type": "analyst_estimates_api", "endpoint": "https://api.fiscal.ai/v1/company/estimates/street", "enabled": True,
}


def seed_catalog(engine: Engine, definitions: list[IndicatorDefinition]) -> None:
    source_configurations = {
        "fred": ({**FRED_SOURCE}, {"series_count": sum(item.enabled and item.source == "fred" for item in definitions)}),
        "coinpaprika": ({**COINPAPRIKA_SOURCE}, {"series_count": sum(item.enabled and item.source == "coinpaprika" for item in definitions)}),
        "sec": ({**SEC_SOURCE}, {"company_count": 17}),
        "proskauer": ({**PROSKAUER_SOURCE}, {"series_count": sum(item.enabled and item.source == "proskauer" for item in definitions)}),
        "fiscalai": ({**FISCALAI_SOURCE}, {"company_count": len(COMPANY_KEYS), "history": "current_calendar_year_on_free_plan"}),
    }
    with engine.begin() as connection:
        for source_id, (source, configuration) in source_configurations.items():
            connection.execute(text("""INSERT INTO data_sources (id, name, source_type, endpoint, enabled, configuration)
                VALUES (:id, :name, :source_type, :endpoint, :enabled, :configuration)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, source_type=excluded.source_type,
                  endpoint=excluded.endpoint, enabled=excluded.enabled, configuration=excluded.configuration"""),
                {**source, "configuration": json.dumps(configuration)})
        for item in definitions:
            connection.execute(
                text(
                    """INSERT INTO indicators
                       (id, name, category, unit, frequency, direction, normalization_method, configuration)
                       VALUES (:id, :name, :category, :unit, :frequency, :direction,
                         :normalization_method, :configuration)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, category=excluded.category,
                         unit=excluded.unit, frequency=excluded.frequency, direction=excluded.direction,
                         normalization_method=excluded.normalization_method,
                         configuration=excluded.configuration"""
                ),
                {
                    "id": item.id,
                    "name": item.name,
                    "category": item.category,
                    "unit": item.unit,
                    "frequency": item.frequency,
                    "direction": item.direction,
                    "normalization_method": item.normalization_method,
                    "configuration": json.dumps(item.as_configuration()),
                },
            )


async def collect_fred(
    engine: Engine,
    api_key: str,
    definitions: list[IndicatorDefinition],
    observation_start: date,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    collector = FredCollector(api_key=api_key, client=client)
    observations: list[Observation] = []
    succeeded: list[str] = []
    errors: list[dict[str, str]] = []

    for definition in definitions:
        if definition.source != "fred" or not definition.enabled:
            continue
        try:
            observations.extend(await collector.fetch_series(definition, observation_start))
            succeeded.append(definition.source_id)
        except CollectorError as exc:
            LOGGER.warning("FRED collection failed for %s: %s", definition.source_id, exc)
            errors.append({"series_id": definition.source_id, "message": str(exc)})

    now = datetime.now(UTC)
    missing_count = sum(item.value is None for item in observations)
    if observations:
        rows = [
            {
                "source_id": "fred",
                "indicator_id": item.indicator_id,
                "observation_date": item.observation_date.isoformat(),
                "fetched_at": now.isoformat(),
                "raw_value": item.value,
                "raw_payload": json.dumps(item.raw_payload),
                "source_reference": item.source_reference,
            }
            for item in observations
        ]
        with engine.begin() as connection:
            connection.execute(
                text(
                    """INSERT INTO raw_observations
                       (source_id, indicator_id, observation_date, fetched_at, raw_value, raw_payload, source_reference)
                       VALUES (:source_id, :indicator_id, :observation_date, :fetched_at, :raw_value, :raw_payload, :source_reference)
                       ON CONFLICT(source_id, indicator_id, observation_date) DO UPDATE SET
                         fetched_at=excluded.fetched_at, raw_value=excluded.raw_value,
                         raw_payload=excluded.raw_payload, source_reference=excluded.source_reference"""
                ),
                rows,
            )

    error_summary = "; ".join(
        f"{item['series_id']}: {item['message']}" for item in errors
    ) or None
    with engine.begin() as connection:
        if succeeded:
            connection.execute(
                text("UPDATE data_sources SET last_success=:now, last_error=:error WHERE id='fred'"),
                {"now": now.isoformat(), "error": error_summary},
            )
        else:
            connection.execute(
                text("UPDATE data_sources SET last_error=:error WHERE id='fred'"),
                {"error": error_summary or "No FRED series were requested"},
            )

    return {
        "source_id": "fred",
        "status": "failed" if not succeeded else "degraded" if errors else "ok",
        "series_requested": len(succeeded) + len(errors),
        "series_succeeded": len(succeeded),
        "observations_received": len(observations),
        "missing_observations": missing_count,
        "observation_start": observation_start.isoformat(),
        "completed_at": now.isoformat(),
        "errors": errors,
    }


async def collect_coinpaprika(
    engine: Engine, definitions: list[IndicatorDefinition], observation_start: date,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    collector = CoinPaprikaCollector(client=client)
    observations: list[Observation] = []
    succeeded: list[str] = []
    errors: list[dict[str, str]] = []
    for definition in definitions:
        if definition.source != "coinpaprika" or not definition.enabled:
            continue
        try:
            observations.extend(await collector.fetch_series(definition, observation_start))
            succeeded.append(definition.source_id)
        except CollectorError as exc:
            LOGGER.warning("CoinPaprika collection failed for %s: %s", definition.source_id, exc)
            errors.append({"series_id": definition.source_id, "message": str(exc)})
    now = datetime.now(UTC)
    if observations:
        _upsert_observations(engine, "coinpaprika", observations, now)
    error_summary = "; ".join(f"{item['series_id']}: {item['message']}" for item in errors) or None
    with engine.begin() as connection:
        if succeeded:
            connection.execute(text("UPDATE data_sources SET last_success=:now, last_error=:error WHERE id='coinpaprika'"),
                               {"now": now.isoformat(), "error": error_summary})
        else:
            connection.execute(text("UPDATE data_sources SET last_error=:error WHERE id='coinpaprika'"),
                               {"error": error_summary or "No CoinPaprika indicators were requested"})
    return {"source_id": "coinpaprika", "status": "failed" if not succeeded else "degraded" if errors else "ok",
            "series_requested": len(succeeded) + len(errors), "series_succeeded": len(succeeded),
            "observations_received": len(observations), "observation_start": observation_start.isoformat(),
            "completed_at": now.isoformat(), "errors": errors}


async def collect_proskauer(
    engine: Engine, definitions: list[IndicatorDefinition], observation_start: date,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    collector = ProskauerCollector(client=client)
    observations: list[Observation] = []
    errors: list[dict[str, str]] = []
    requested = [item for item in definitions if item.source == "proskauer" and item.enabled]
    for definition in requested:
        try:
            observations.extend(await collector.fetch_series(definition, observation_start))
        except CollectorError as exc:
            errors.append({"series_id": definition.source_id, "message": str(exc)})
    now = datetime.now(UTC)
    if observations:
        _upsert_observations(engine, "proskauer", observations, now)
    succeeded = len(requested) - len(errors)
    error_summary = "; ".join(f"{item['series_id']}: {item['message']}" for item in errors) or None
    with engine.begin() as connection:
        if succeeded:
            connection.execute(text("UPDATE data_sources SET last_success=:now, last_error=:error WHERE id='proskauer'"),
                               {"now": now.isoformat(), "error": error_summary})
        else:
            connection.execute(text("UPDATE data_sources SET last_error=:error WHERE id='proskauer'"),
                               {"error": error_summary or "No Proskauer series were requested"})
    return {"source_id": "proskauer", "status": "failed" if not succeeded else "degraded" if errors else "ok",
            "series_requested": len(requested), "series_succeeded": succeeded,
            "observations_received": len(observations), "observation_start": observation_start.isoformat(),
            "completed_at": now.isoformat(), "errors": errors}


def _upsert_observations(engine: Engine, source_id: str, observations: list[Observation], now: datetime) -> None:
    rows = [{"source_id": source_id, "indicator_id": item.indicator_id,
             "observation_date": item.observation_date.isoformat(), "fetched_at": now.isoformat(),
             "raw_value": item.value, "raw_payload": json.dumps(item.raw_payload),
             "source_reference": item.source_reference} for item in observations]
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO raw_observations
            (source_id, indicator_id, observation_date, fetched_at, raw_value, raw_payload, source_reference)
            VALUES (:source_id, :indicator_id, :observation_date, :fetched_at, :raw_value, :raw_payload, :source_reference)
            ON CONFLICT(source_id, indicator_id, observation_date) DO UPDATE SET fetched_at=excluded.fetched_at,
            raw_value=excluded.raw_value, raw_payload=excluded.raw_payload, source_reference=excluded.source_reference"""), rows)


def collection_start(days: int) -> date:
    return (datetime.now(UTC) - timedelta(days=days)).date()


def source_last_success(engine: Engine, source_id: str) -> datetime | None:
    with engine.connect() as connection:
        value = connection.execute(text("SELECT last_success FROM data_sources WHERE id=:id"), {"id": source_id}).scalar_one_or_none()
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def next_collection_start(
    engine: Engine, history_days: int, definitions: list[IndicatorDefinition] | None = None,
) -> date:
    with engine.connect() as connection:
        if definitions:
            missing_ids = [item.id for item in definitions if item.enabled and item.source == "fred"]
            if missing_ids:
                existing = set(connection.execute(
                    text("SELECT DISTINCT indicator_id FROM raw_observations WHERE source_id='fred' AND raw_value IS NOT NULL")
                ).scalars().all())
                if any(item_id not in existing for item_id in missing_ids):
                    return collection_start(history_days)
        oldest_latest = connection.execute(
            text(
                """SELECT MIN(latest_date) FROM (
                     SELECT MAX(observation_date) AS latest_date FROM raw_observations
                     WHERE source_id IN ('fred','coinpaprika') AND raw_value IS NOT NULL GROUP BY indicator_id
                   )"""
            )
        ).scalar_one_or_none()
    if oldest_latest is None:
        return collection_start(history_days)
    return max(date.fromisoformat(str(oldest_latest)) - timedelta(days=7), collection_start(history_days))


def next_coinpaprika_start(engine: Engine) -> date:
    # CoinPaprika's free daily history window is rolling; stay inside its date cutoff.
    history_start = collection_start(364)
    with engine.connect() as connection:
        coverage = connection.execute(text("""SELECT MAX(earliest_date), MIN(latest_date) FROM (
            SELECT MIN(observation_date) AS earliest_date, MAX(observation_date) AS latest_date FROM raw_observations
            WHERE source_id='coinpaprika' AND raw_value IS NOT NULL GROUP BY indicator_id
        )""")).one()
    earliest, latest = coverage
    if latest is None:
        return history_start
    if date.fromisoformat(str(earliest)) > history_start:
        return history_start
    return max(date.fromisoformat(str(latest)) - timedelta(days=7), history_start)


def indicator_catalog(engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """SELECT i.id, i.name, i.category, i.unit, i.frequency, i.direction,
                  i.normalization_method, i.configuration,
                  (SELECT MAX(r.observation_date) FROM raw_observations r
                    WHERE r.indicator_id=i.id AND r.raw_value IS NOT NULL) AS latest_observation_date,
                  (SELECT CASE WHEN v.indicator_id IS NOT NULL THEN v.raw_value ELSE r.raw_value END
                    FROM raw_observations r LEFT JOIN indicator_values v
                      ON v.indicator_id=r.indicator_id AND v.observation_date=r.observation_date
                    WHERE r.indicator_id=i.id AND r.raw_value IS NOT NULL
                    ORDER BY r.observation_date DESC LIMIT 1) AS latest_value,
                  (SELECT v.normalized_value FROM indicator_values v
                    WHERE v.indicator_id=i.id AND v.observation_date=(SELECT MAX(r.observation_date) FROM raw_observations r
                      WHERE r.indicator_id=i.id AND r.raw_value IS NOT NULL)
                    LIMIT 1) AS latest_normalized_value,
                  (SELECT v.confidence FROM indicator_values v
                    WHERE v.indicator_id=i.id AND v.observation_date=(SELECT MAX(r.observation_date) FROM raw_observations r
                      WHERE r.indicator_id=i.id AND r.raw_value IS NOT NULL)
                    LIMIT 1) AS normalized_confidence,
                  (SELECT v.data_quality FROM indicator_values v
                    WHERE v.indicator_id=i.id AND v.observation_date=(SELECT MAX(r.observation_date) FROM raw_observations r
                      WHERE r.indicator_id=i.id AND r.raw_value IS NOT NULL)
                    LIMIT 1) AS normalized_data_quality
           FROM indicators i ORDER BY i.category, i.name"""
    )
    with engine.connect() as connection:
        rows = connection.execute(query).mappings().all()
    result = []
    today = datetime.now(UTC).date()
    for row in rows:
        item = dict(row)
        configuration = json.loads(item["configuration"] or "{}")
        item["configuration"] = configuration
        item["source_id"] = configuration.get("source_id")
        item["enabled"] = bool(configuration.get("enabled", True))
        item["availability_note"] = configuration.get("availability_note")
        latest = item["latest_observation_date"]
        if not item["enabled"]:
            item["data_status"] = "retired"
            item["normalized_confidence"] = 0.0
        elif latest is None or item["latest_value"] is None:
            item["data_status"] = "unavailable"
            item["normalized_confidence"] = 0.0
        elif (today - date.fromisoformat(str(latest))).days > int(configuration.get("staleness_days", 7)):
            item["data_status"] = "stale"
            age = (today - date.fromisoformat(str(latest))).days
            threshold = int(configuration.get("staleness_days", 7))
            freshness = max(0.0, 1.0 - ((age - threshold) / max(threshold, 1)))
            item["normalized_confidence"] = round(float(item["normalized_confidence"] or 0) * freshness, 4)
        else:
            item["data_status"] = "fresh"
        result.append(item)
    return result


def data_sources(engine: Engine, fred_configured: bool, sec_configured: bool = False, fiscalai_configured: bool = False) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, name, source_type, endpoint, enabled, last_success, last_error FROM data_sources ORDER BY id")
        ).mappings().all()
    results = []
    for row in rows:
        source = dict(row)
        if source["id"] == "fred" and not fred_configured or source["id"] == "sec" and not sec_configured or source["id"] == "fiscalai" and not fiscalai_configured:
            source["status"] = "unconfigured"
        elif source["last_error"] and source["last_success"]:
            source["status"] = "degraded"
        elif source["last_error"]:
            source["status"] = "failed"
        elif source["last_success"]:
            source["status"] = "ready"
        else:
            source["status"] = "pending"
        source["configured"] = fred_configured if source["id"] == "fred" else sec_configured if source["id"] == "sec" else fiscalai_configured if source["id"] == "fiscalai" else True
        results.append(source)
    return results


def data_health(engine: Engine, definitions: list[IndicatorDefinition]) -> dict[str, Any]:
    today = date.today()
    indicators = indicator_catalog(engine)
    retired = sum(not item["enabled"] for item in indicators)
    active = len(indicators) - retired
    fresh = 0
    stale = 0
    unavailable = 0
    for item in indicators:
        if not item["enabled"]:
            continue
        latest = item["latest_observation_date"]
        if latest is None or item["latest_value"] is None:
            unavailable += 1
            continue
        configured = next((d for d in definitions if d.id == item["id"]), None)
        staleness = configured.staleness_days if configured else 7
        observed_date = date.fromisoformat(str(latest))
        if (today - observed_date).days <= staleness:
            fresh += 1
        else:
            stale += 1
    with engine.connect() as connection:
        source_rows = connection.execute(
            text("SELECT COUNT(*) AS total, SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS failed, MAX(last_success) AS last_success FROM data_sources")
        ).mappings().one()
        last_scoring = connection.execute(text("""SELECT MAX(calculated_at) FROM (
            SELECT calculated_at FROM indicator_values UNION ALL SELECT calculated_at FROM scenario_scores
        )""")).scalar_one_or_none()
    return {
        "active_indicators": active,
        "fresh_indicators": fresh,
        "stale_indicators": stale,
        "unavailable_indicators": unavailable,
        "retired_indicators": retired,
        "failed_sources": int(source_rows["failed"] or 0),
        "last_successful_collection": source_rows["last_success"],
        "last_scoring_execution": last_scoring,
    }

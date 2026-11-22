from __future__ import annotations

import logging
import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import Engine, text

LOGGER = logging.getLogger(__name__)
ENDPOINT = "https://api.fiscal.ai/v1/company/estimates/street"
COMPANY_KEYS = {
    "msft": "NASDAQ_MSFT", "nvda": "NASDAQ_NVDA", "amzn": "NASDAQ_AMZN",
    "googl": "NASDAQ_GOOG", "avgo": "NASDAQ_AVGO", "eqix": "NASDAQ_EQIX",
    "meta": "NASDAQ_META", "orcl": "NYSE_ORCL",
}
METRICS = ("analyst_eps", "analyst_revenue", "analyst_ebitda", "analyst_ebit", "analyst_pretax_income")


class FiscalAIError(Exception):
    pass


class FiscalAICollector:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.client = client

    async def fetch_company(self, company_key: str) -> dict[str, Any]:
        exchange, ticker = company_key.split("_", 1)
        params = {"exchange": exchange, "ticker": ticker, "history": "true"}
        if self.client is not None:
            response = await self.client.get(ENDPOINT, params=params, headers={"X-Api-Key": self.api_key})
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(ENDPOINT, params=params, headers={"X-Api-Key": self.api_key})
        if response.status_code != 200:
            # Do not log response bodies: upstream error payloads are outside our control.
            raise FiscalAIError(f"Fiscal.ai returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise FiscalAIError("Fiscal.ai returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise FiscalAIError("Fiscal.ai response did not contain a data array")
        return payload


async def collect_fiscalai(engine: Engine, api_key: str, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    collector = FiscalAICollector(api_key, client)
    now = datetime.now(UTC)
    succeeded: list[str] = []
    errors: list[dict[str, str]] = []
    observations = 0
    for company_id, company_key in COMPANY_KEYS.items():
        try:
            payload = await collector.fetch_company(company_key)
            rows = _estimate_rows(company_id, payload, now)
            if rows:
                with engine.begin() as connection:
                    connection.execute(text("""INSERT INTO analyst_estimates
                        (company_id, currency, period_id, metric, as_of, period_type, report_date, mean, median, high, low,
                         num_estimates, fetched_at, raw_payload)
                        VALUES (:company_id, :currency, :period_id, :metric, :as_of, :period_type, :report_date, :mean,
                         :median, :high, :low, :num_estimates, :fetched_at, :raw_payload)
                        ON CONFLICT(company_id, period_id, metric, as_of) DO UPDATE SET
                         currency=excluded.currency, period_type=excluded.period_type, report_date=excluded.report_date, mean=excluded.mean,
                         median=excluded.median, high=excluded.high, low=excluded.low,
                         num_estimates=excluded.num_estimates, fetched_at=excluded.fetched_at,
                         raw_payload=excluded.raw_payload"""), rows)
            succeeded.append(company_id)
            observations += len(rows)
        except (FiscalAIError, httpx.HTTPError) as exc:
            LOGGER.warning("Fiscal.ai collection failed for %s: %s", company_id, exc)
            errors.append({"company_id": company_id, "message": str(exc)})
    summary = "; ".join(f"{item['company_id']}: {item['message']}" for item in errors) or None
    with engine.begin() as connection:
        if succeeded:
            connection.execute(text("UPDATE data_sources SET last_success=:now, last_error=:error WHERE id='fiscalai'"),
                               {"now": now.isoformat(), "error": summary})
        else:
            connection.execute(text("UPDATE data_sources SET last_error=:error WHERE id='fiscalai'"),
                               {"error": summary or "No company estimates were received"})
    return {"source_id": "fiscalai", "status": "failed" if not succeeded else "degraded" if errors else "ok",
            "series_requested": len(COMPANY_KEYS), "series_succeeded": len(succeeded),
            "observations_received": observations, "completed_at": now.isoformat(), "errors": errors}


def _estimate_rows(company_id: str, payload: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for period in payload.get("data", []):
        if not isinstance(period, dict):
            continue
        period_id = period.get("periodId")
        if not isinstance(period_id, str) or not period_id:
            continue
        values = period.get("metricsValues") or {}
        if not isinstance(values, dict):
            continue
        for metric in METRICS:
            stats = values.get(metric)
            if isinstance(stats, dict):
                _append(rows, company_id, period, period_id, metric, stats, now, payload.get("currency"))
    for item in payload.get("history", []):
        if isinstance(item, dict) and item.get("metricId") in METRICS and item.get("periodId"):
            _append(rows, company_id, item, item["periodId"], item["metricId"], item, now, payload.get("currency"))
    return list(rows.values())


def _append(rows: dict, company_id: str, period: dict, period_id: str, metric: str, stats: dict, now: datetime, currency: str | None) -> None:
    raw_as_of = stats.get("asOf")
    if not isinstance(raw_as_of, str):
        return
    try:
        as_of = date.fromisoformat(raw_as_of[:10])
    except ValueError:
        return
    raw_report_date = period.get("reportDate")
    try:
        report_date = date.fromisoformat(raw_report_date[:10]) if isinstance(raw_report_date, str) else None
    except ValueError:
        report_date = None
    key = (period_id, metric, as_of.isoformat())
    rows[key] = {"company_id": company_id, "currency": currency, "period_id": period_id, "metric": metric, "as_of": as_of.isoformat(),
                 "period_type": period.get("periodType"), "report_date": report_date.isoformat() if report_date else None,
                 "mean": _number(stats.get("mean")), "median": _number(stats.get("median")),
                 "high": _number(stats.get("high")), "low": _number(stats.get("low")),
                 "num_estimates": _integer(stats.get("numEstimates")), "fetched_at": now.isoformat(),
                 "raw_payload": json.dumps(stats)}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

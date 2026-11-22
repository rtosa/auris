import asyncio

import httpx
import pytest

from app.collectors.fiscalai import FiscalAICollector, FiscalAIError, _estimate_rows


def test_parses_current_and_history_snapshots_and_deduplicates():
    payload = {
        "currency": "USD",
        "data": [{"periodId": "FY2027", "periodType": "Annual", "reportDate": "2027-06-30",
                  "metricsValues": {"analyst_eps": {"mean": 12.4, "median": 12.1, "high": 13.0,
                                                       "low": 11.0, "numEstimates": 40, "asOf": "2026-11-01"}}}],
        "history": [{"metricId": "analyst_eps", "periodId": "FY2027", "periodType": "Annual",
                     "reportDate": "2027-06-30", "mean": 12.4, "median": 12.1, "high": 13.0,
                     "low": 11.0, "numEstimates": 40, "asOf": "2026-11-01"},
                    {"metricId": "analyst_eps", "periodId": "FY2027", "mean": 11.8,
                     "numEstimates": 38, "asOf": "2026-10-01"}],
    }
    rows = _estimate_rows("msft", payload, __import__("datetime").datetime.now(__import__("datetime").UTC))
    assert len(rows) == 2
    latest = next(row for row in rows if row["as_of"] == "2026-11-01")
    assert latest["mean"] == 12.4
    assert latest["num_estimates"] == 40
    assert latest["currency"] == "USD"


def test_api_key_is_sent_only_as_header_and_http_errors_are_sanitized():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["header"] = request.headers.get("X-Api-Key")
        captured["ticker"] = request.url.params.get("ticker")
        captured["exchange"] = request.url.params.get("exchange")
        return httpx.Response(403, text="private upstream error")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(FiscalAIError) as exc:
                await FiscalAICollector("secret-token", client).fetch_company("NASDAQ_MSFT")
            assert "secret-token" not in str(exc.value)
        finally:
            await client.aclose()

    asyncio.run(run())
    assert "secret-token" not in captured["url"]
    assert captured["header"] == "secret-token"
    assert captured["ticker"] == "MSFT"
    assert captured["exchange"] == "NASDAQ"

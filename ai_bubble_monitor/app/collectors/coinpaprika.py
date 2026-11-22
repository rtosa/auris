from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime

import httpx

from app.collectors.definitions import IndicatorDefinition
from app.collectors.fred import CollectorError, Observation

TICKERS_URL = "https://api.coinpaprika.com/v1/tickers/"
HISTORY_URL = "https://api.coinpaprika.com/v1/tickers/{coin_id}/historical"


class CoinPaprikaCollector:
    """Public daily market data collector. CoinPaprika's free endpoint needs no key."""

    def __init__(self, client: httpx.AsyncClient | None = None, timeout_seconds: float = 20.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    async def fetch_series(self, definition: IndicatorDefinition, observation_start: date) -> list[Observation]:
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            rows: list[Observation] = []
            # Historical endpoint returns daily points, with a one-year free-tier limit.
            response = await self._get(client, HISTORY_URL.format(coin_id=definition.source_id), {
                "start": observation_start.isoformat(), "interval": "1d",
            }, definition.source_id)
            payload = response.json()
            if not isinstance(payload, list):
                raise CollectorError(f"CoinPaprika returned invalid history for {definition.source_id}")
            for row in payload:
                if not isinstance(row, dict):
                    continue
                rows.append(self._parse_row(definition, row))

            # Current ticker refreshes today's partial daily value between history updates.
            ticker_response = await self._get(client, TICKERS_URL + definition.source_id, {}, definition.source_id)
            ticker = ticker_response.json()
            if not isinstance(ticker, dict):
                raise CollectorError(f"CoinPaprika returned invalid ticker for {definition.source_id}")
            quote = ticker.get("quotes", {}).get("USD", {}) if isinstance(ticker.get("quotes"), dict) else {}
            price = quote.get("price") if isinstance(quote, dict) else None
            updated_at = ticker.get("last_updated")
            if price is not None and updated_at:
                ticker_row = {"timestamp": updated_at, "price": price, "ticker": ticker}
                rows.append(self._parse_row(definition, ticker_row))
            return rows
        except CollectorError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise CollectorError(f"CoinPaprika returned malformed data for {definition.source_id}") from exc
        finally:
            if own_client:
                await client.aclose()

    def _parse_row(self, definition: IndicatorDefinition, row: dict[str, object]) -> Observation:
        try:
            timestamp = str(row.get("timestamp", row.get("last_updated", "")))
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            observed_date = observed.astimezone(UTC).date()
            raw_value = row.get("price")
            value = float(raw_value)  # type: ignore[arg-type]
            if not math.isfinite(value) or value <= 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise CollectorError(f"CoinPaprika returned an invalid price/date for {definition.source_id}") from exc
        return Observation(
            indicator_id=definition.id,
            observation_date=observed_date,
            value=value,
            raw_payload={str(key): val for key, val in row.items()},
            source_reference=f"https://coinpaprika.com/coin/{definition.source_id}/",
        )

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict[str, str], coin_id: str) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await client.get(url, params=params)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise CollectorError(f"CoinPaprika request failed for {coin_id}") from exc
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if not response.is_success:
                raise CollectorError(f"CoinPaprika returned HTTP {response.status_code} for {coin_id}")
            return response
        raise CollectorError(f"CoinPaprika request failed for {coin_id}")

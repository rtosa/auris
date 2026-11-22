from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import date

import httpx

from app.collectors.definitions import IndicatorDefinition

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/"


class CollectorError(RuntimeError):
    """Sanitized source failure that never includes request credentials."""


@dataclass(frozen=True)
class Observation:
    indicator_id: str
    observation_date: date
    value: float | None
    raw_payload: dict[str, object]
    source_reference: str


class FredCollector:
    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("FRED API key is required")
        self._api_key = api_key.strip()
        self._client = client
        self._timeout = timeout_seconds

    async def fetch_series(
        self,
        definition: IndicatorDefinition,
        observation_start: date,
    ) -> list[Observation]:
        params = {
            "series_id": definition.source_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": observation_start.isoformat(),
            "sort_order": "asc",
            "limit": 100000,
        }
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await self._request(client, params, definition.source_id)
            payload = response.json()
        except CollectorError:
            raise
        except (ValueError, TypeError) as exc:
            raise CollectorError(f"FRED returned invalid JSON for {definition.source_id}") from exc
        finally:
            if own_client:
                await client.aclose()

        observations = payload.get("observations") if isinstance(payload, dict) else None
        if not isinstance(observations, list):
            raise CollectorError(f"FRED response has no observations for {definition.source_id}")

        parsed: list[Observation] = []
        for row in observations:
            if not isinstance(row, dict):
                raise CollectorError(f"FRED returned a malformed row for {definition.source_id}")
            try:
                row_date = date.fromisoformat(str(row["date"]))
            except (KeyError, ValueError) as exc:
                raise CollectorError(f"FRED returned an invalid date for {definition.source_id}") from exc

            raw_value = row.get("value")
            value: float | None
            if raw_value in (None, ".", ""):
                value = None
            else:
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise CollectorError(
                        f"FRED returned a non-numeric value for {definition.source_id}"
                    ) from exc
                if not math.isfinite(value):
                    raise CollectorError(f"FRED returned a non-finite value for {definition.source_id}")

            parsed.append(
                Observation(
                    indicator_id=definition.id,
                    observation_date=row_date,
                    value=value,
                    raw_payload={str(key): val for key, val in row.items()},
                    source_reference=f"{FRED_SERIES_URL}{definition.source_id}",
                )
            )
        return parsed

    async def _request(
        self,
        client: httpx.AsyncClient,
        params: dict[str, object],
        series_id: str,
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await client.get(FRED_OBSERVATIONS_URL, params=params)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise CollectorError(f"FRED request failed for {series_id}") from exc
                await asyncio.sleep(0.5 * (2**attempt))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
            if not response.is_success:
                raise CollectorError(f"FRED returned HTTP {response.status_code} for {series_id}")
            return response
        raise CollectorError(f"FRED request failed for {series_id}")

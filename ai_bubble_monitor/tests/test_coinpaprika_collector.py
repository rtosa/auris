import asyncio
from datetime import date

import httpx
from sqlalchemy import text

from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.services.collection import collect_coinpaprika, indicator_catalog, next_coinpaprika_start, seed_catalog


def test_coinpaprika_parses_history_and_current_ticker_and_upserts(tmp_path):
    definition = next(item for item in load_definitions() if item.id == "bitcoin_usd")
    database_path = tmp_path / "monitor.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, [definition])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/historical"):
            assert request.url.params["interval"] == "1d"
            return httpx.Response(200, json=[{"timestamp": "2026-11-20T00:00:00Z", "price": 80000.0}], request=request)
        return httpx.Response(200, json={
            "last_updated": "2026-11-21T12:00:00Z", "quotes": {"USD": {"price": 81000.25}},
        }, request=request)

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_coinpaprika(engine, [definition], date(2026, 11, 20), client)

    result = asyncio.run(collect())
    assert result["status"] == "ok"
    assert result["observations_received"] == 2
    assert next(row for row in indicator_catalog(engine) if row["id"] == "bitcoin_usd")["latest_value"] == 81000.25

    async def recollect():
        def updated(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/historical"):
                return httpx.Response(200, json=[], request=request)
            return httpx.Response(200, json={"last_updated": "2026-11-21T13:00:00Z", "quotes": {"USD": {"price": 81500.0}}}, request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(updated)) as client:
            return await collect_coinpaprika(engine, [definition], date(2026, 11, 21), client)

    asyncio.run(recollect())
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT COUNT(*), MAX(raw_value) FROM raw_observations WHERE indicator_id='bitcoin_usd'")).one()
    assert rows == (2, 81500.0)
    assert next_coinpaprika_start(engine) < date(2026, 1, 1)
    engine.dispose()


def test_coinpaprika_errors_are_sanitized(tmp_path, caplog):
    definition = next(item for item in load_definitions() if item.id == "bitcoin_usd")
    database_path = tmp_path / "monitor.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, [definition])

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))) as client:
            return await collect_coinpaprika(engine, [definition], date(2026, 11, 21), client)

    result = asyncio.run(collect())
    assert result["status"] == "failed"
    assert "api_key" not in str(result)
    assert "api_key" not in caplog.text
    engine.dispose()

import asyncio
from datetime import date

import httpx
from sqlalchemy import text

from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.services.collection import collect_fred, indicator_catalog, next_collection_start, seed_catalog


def test_fred_parser_preserves_missing_values_and_source_dates(tmp_path):
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["series_id"] == "VIXCLS"
            assert request.url.params["observation_start"] == "2026-01-01"
            return httpx.Response(
                200,
                json={
                    "observations": [
                        {"date": "2026-01-02", "value": "16.42", "realtime_start": "2026-01-05"},
                        {"date": "2026-01-03", "value": ".", "realtime_start": "2026-01-05"},
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await collect_fred(
                engine,
                "test-secret",
                [definition],
                date(2026, 1, 1),
                client,
            )
        return result

    definition = next(item for item in load_definitions() if item.id == "vix")
    engine = make_engine(tmp_path / "monitor.db")
    upgrade_database(tmp_path / "monitor.db")
    seed_catalog(engine, [definition])

    result = asyncio.run(run())

    assert result["status"] == "ok"
    assert result["observations_received"] == 2
    assert result["missing_observations"] == 1
    rows = indicator_catalog(engine)
    assert rows[0]["latest_observation_date"] == "2026-01-02"
    assert rows[0]["latest_value"] == 16.42
    with engine.connect() as connection:
        missing = connection.execute(
            text("SELECT raw_value, raw_payload FROM raw_observations WHERE observation_date='2026-01-03'")
        ).one()
    assert missing.raw_value is None
    assert '"value": "."' in missing.raw_payload
    engine.dispose()


def test_fred_recollection_updates_existing_observation(tmp_path):
    async def run(value: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"observations": [{"date": "2026-01-02", "value": value}]},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_fred(engine, "test-secret", [definition], date(2026, 1, 1), client)

    definition = next(item for item in load_definitions() if item.id == "vix")
    database_path = tmp_path / "monitor.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, [definition])
    asyncio.run(run("16.42"))
    asyncio.run(run("18.00"))

    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM raw_observations")).scalar_one()
        latest_value = connection.execute(text("SELECT raw_value FROM raw_observations")).scalar_one()
    assert count == 1
    assert latest_value == 18.0
    assert next_collection_start(engine, 1825) == date(2025, 12, 26)
    engine.dispose()


def test_new_fred_indicator_triggers_history_backfill(tmp_path):
    definitions = [item for item in load_definitions() if item.source == "fred" and item.enabled]
    database_path = tmp_path / "monitor.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, definitions)
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO raw_observations
            (source_id, indicator_id, observation_date, fetched_at, raw_value, raw_payload, source_reference)
            VALUES ('fred','vix','2026-11-20','2026-11-20T00:00:00Z',15,'{}','https://example.test')"""))
    assert next_collection_start(engine, 1825, definitions) < date(2025, 12, 1)
    engine.dispose()


def test_failed_fred_request_does_not_return_or_log_api_key(tmp_path, caplog):
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error_message": "Invalid API key"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collect_fred(engine, secret, [definition], date(2026, 1, 1), client)

    definition = next(item for item in load_definitions() if item.id == "vix")
    secret = "private-test-api-key"
    database_path = tmp_path / "monitor.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    seed_catalog(engine, [definition])

    result = asyncio.run(run())

    assert result["status"] == "failed"
    assert secret not in str(result)
    assert secret not in caplog.text
    engine.dispose()

import asyncio

import httpx

from app.main import create_app
from app.settings import Settings


def request_with_lifespan(
    app,
    path: str,
    remote: tuple[str, int] = ("testclient", 50000),
    headers: dict[str, str] | None = None,
    method: str = "GET",
):
    async def perform_request():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=remote)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, headers=headers)

    return asyncio.run(perform_request())


def test_health_reports_database_and_migration(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))

    response = request_with_lifespan(app, "/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["database"]["migration_version"] == "0009_analyst_currency"
    assert body["timestamp"]


def test_openapi_docs_are_available(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))

    response = request_with_lifespan(app, "/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "AI Bubble Risk Monitor"


def test_events_endpoint_returns_only_sourced_capex_guidance(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    response = request_with_lifespan(app, "/api/v1/events")
    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_analyst_estimates_endpoint_reports_free_coverage_and_attribution(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    response = request_with_lifespan(app, "/api/v1/analyst-estimates")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["covered_company_count"] == 8
    assert body["attribution"]["url"] == "https://www.zacksdata.com"
    assert "googl" in body["supported_company_ids"]


def test_indicator_catalog_and_data_health_are_seeded(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    catalog = request_with_lifespan(app, "/api/v1/indicators")
    health = request_with_lifespan(app, "/api/v1/data-health")
    sources = request_with_lifespan(app, "/api/v1/data-sources")

    assert catalog.status_code == health.status_code == sources.status_code == 200
    assert catalog.json()["count"] == 25
    assert catalog.json()["active_count"] == 24
    retired = next(row for row in catalog.json()["items"] if row["id"] == "russell_2000")
    assert retired["data_status"] == "retired"
    assert {row["id"] for row in catalog.json()["items"]} >= {"vix", "high_yield_oas", "us_10y_yield", "wti_crude_oil", "henry_hub_natural_gas", "bank_lending_tightening", "initial_jobless_claims", "industrial_production"}
    definitions = {row["id"]: row["configuration"] for row in catalog.json()["items"]}
    assert definitions["cpi_index"]["minimum_observations"] == 24
    assert definitions["cpi_index"]["transformation"] == "year_over_year_percent_change"
    assert definitions["unemployment_rate"]["minimum_observations"] == 48
    assert definitions["bank_lending_tightening"]["minimum_observations"] == 16
    assert health.json()["active_indicators"] == 24
    assert health.json()["unavailable_indicators"] == 24
    assert health.json()["retired_indicators"] == 1
    fred = next(row for row in sources.json()["items"] if row["id"] == "fred")
    assert fred["status"] == "unconfigured"
    assert {row["id"] for row in sources.json()["items"]} == {"fred", "coinpaprika", "sec", "proskauer", "fiscalai"}
    sec = next(row for row in sources.json()["items"] if row["id"] == "sec")
    assert sec["status"] == "unconfigured"


def test_manual_collection_runs_public_sources_without_api_keys(tmp_path, monkeypatch):
    async def fake_coinpaprika(*_args, **_kwargs):
        return {"source_id": "coinpaprika", "status": "ok", "series_requested": 2, "series_succeeded": 2, "observations_received": 0}

    async def fake_proskauer(*_args, **_kwargs):
        return {"source_id": "proskauer", "status": "ok", "series_requested": 1, "series_succeeded": 1, "observations_received": 0}

    monkeypatch.setattr("app.main.collect_coinpaprika", fake_coinpaprika)
    monkeypatch.setattr("app.main.collect_proskauer", fake_proskauer)
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    response = request_with_lifespan(app, "/api/v1/admin/collect", method="POST")

    assert response.status_code == 200
    assert {item["source_id"] for item in response.json()["sources"]} == {"coinpaprika", "proskauer"}
    assert response.json()["status"] == "ok"


def test_automatic_collection_starts_public_feeds_without_credentials(tmp_path, monkeypatch):
    started = set()

    async def fake_coinpaprika(*_args, **_kwargs):
        started.add("coinpaprika")
        return {"source_id": "coinpaprika", "status": "ok", "series_requested": 2, "series_succeeded": 2, "observations_received": 0}

    async def fake_proskauer(*_args, **_kwargs):
        started.add("proskauer")
        return {"source_id": "proskauer", "status": "ok", "series_requested": 1, "series_succeeded": 1, "observations_received": 0}

    monkeypatch.setattr("app.main.collect_coinpaprika", fake_coinpaprika)
    monkeypatch.setattr("app.main.collect_proskauer", fake_proskauer)
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False, auto_collection_enabled=True))

    async def perform():
        async with app.router.lifespan_context(app):
            for _ in range(20):
                if started == {"coinpaprika", "proskauer"}:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(perform())
    assert started == {"coinpaprika", "proskauer"}


def test_company_universe_is_visible_before_sec_is_configured(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    response = request_with_lifespan(app, "/api/v1/companies")
    invalid = request_with_lifespan(app, "/api/v1/companies/not-a-company")
    assert response.status_code == 200
    assert response.json()["count"] == 17
    assert response.json()["available_count"] == 0
    assert response.json()["items"][0]["status"] == "unavailable"
    assert request_with_lifespan(app, "/api/v1/companies/nvda/history").json()["items"] == []
    assert invalid.status_code == 404


def test_manual_recalculation_and_indicator_history_endpoints(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    result = request_with_lifespan(app, "/api/v1/admin/recalculate", method="POST")
    history = request_with_lifespan(app, "/api/v1/indicators/vix/history?days=30")
    missing = request_with_lifespan(app, "/api/v1/indicators/not-real/history")

    assert result.status_code == 200
    assert result.json()["normalization"]["methodology_version"] == "0.11.0"
    assert result.json()["scoring"]["scores_saved"] == 6
    scenarios = request_with_lifespan(app, "/api/v1/scenarios")
    macro = next(row for row in scenarios.json()["items"] if row["id"] == "macro_crisis")
    macro_history = request_with_lifespan(app, "/api/v1/scenarios/macro_crisis/history?days=30")
    fragility = request_with_lifespan(app, "/api/v1/fragility")
    stress = request_with_lifespan(app, "/api/v1/stress")
    assert macro["status"] == "unavailable"
    assert macro["score"] is None
    assert scenarios.json()["count"] == 6
    assert len(macro_history.json()["items"]) == 1
    assert fragility.json()["id"] == "fragility_index"
    assert stress.json()["id"] == "current_stress_index"
    assert history.status_code == 200
    assert history.json()["items"] == []
    assert missing.status_code == 404


def test_ingress_mode_rejects_non_home_assistant_sources(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=True, allow_direct_access=False))

    response = request_with_lifespan(app, "/api/v1/health", ("192.0.2.10", 1234))

    assert response.status_code == 403


def test_direct_access_can_be_enabled_for_lan_or_tunnel(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=True, allow_direct_access=True))
    health = request_with_lifespan(app, "/api/v1/health", ("192.0.2.10", 1234))
    allowed = request_with_lifespan(app, "/api/v1/companies", ("192.0.2.10", 1234))

    assert health.status_code == 200
    assert allowed.status_code == 200


def test_ingress_mode_allows_loopback_health_probe_only(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=True, allow_direct_access=False))
    health = request_with_lifespan(app, "/api/v1/health", ("127.0.0.1", 54321))
    other = request_with_lifespan(app, "/api/v1/companies", ("127.0.0.1", 54321))
    assert health.status_code == 200
    assert other.status_code == 403


def test_ingress_mode_allows_home_assistant_proxy(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=True))

    response = request_with_lifespan(app, "/api/v1/health", ("172.30.32.2", 1234))

    assert response.status_code == 200


def test_ingress_path_prefix_is_normalized(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=True))
    prefix = "/api/hassio_ingress/test-token"

    response = request_with_lifespan(
        app,
        f"{prefix}/api/v1/health",
        ("172.30.32.2", 1234),
        {"X-Ingress-Path": prefix},
    )

    assert response.status_code == 200


def test_database_survives_app_recreation_and_uses_wal(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    first = request_with_lifespan(app, "/api/v1/health")
    app_after_restart = create_app(Settings(data_dir=tmp_path, ingress_mode=False))
    second = request_with_lifespan(app_after_restart, "/api/v1/health")

    assert first.status_code == second.status_code == 200
    assert second.json()["database"]["migration_version"] == "0009_analyst_currency"

    import sqlite3

    with sqlite3.connect(tmp_path / "ai_bubble_monitor.db") as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"

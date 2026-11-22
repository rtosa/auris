from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import Request
from fastapi.staticfiles import StaticFiles

from sqlalchemy import Engine, text

from app.database import database_check, make_engine, upgrade_database
from app.collectors.definitions import load_definitions
from app.collectors.sec import collect_sec_financials, valid_sec_user_agent
from app.services.collection import collect_coinpaprika
from app.services.collection import (
    collect_fred,
    data_health,
    data_sources,
    indicator_catalog,
    next_collection_start,
    next_coinpaprika_start,
    seed_catalog,
)
from app.settings import Settings
from app.services.companies import company_catalog, company_history, load_company_universe
from app.services.events import event_catalog
from app.services.scoring import (
    indicator_history,
    load_scoring_config,
    recalculate_indicators,
    recalculate_scenarios,
    scenario_catalog,
    scenario_history,
)

APP_VERSION = "0.17.0"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.load()
    engine: Engine | None = None
    definitions = load_definitions()
    collection_lock = asyncio.Lock()
    collection_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal engine, collection_task
        logging.basicConfig(
            level=app_settings.log_level.upper(),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        upgrade_database(app_settings.database_path)
        engine = make_engine(app_settings.database_path)
        seed_catalog(engine, definitions)
        application.state.engine = engine
        if app_settings.fred_api_key or valid_sec_user_agent(app_settings.sec_user_agent):
            collection_task = asyncio.create_task(periodic_collection())
        yield
        if collection_task is not None:
            collection_task.cancel()
            try:
                await collection_task
            except asyncio.CancelledError:
                pass
        if engine is not None:
            engine.dispose()
            application.state.engine = None

    application = FastAPI(
        title="AI Bubble Risk Monitor",
        version=APP_VERSION,
        description="Local-first foundation for explainable AI ecosystem risk monitoring.",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.engine = engine
    application.state.indicator_definitions = definitions

    async def run_collection() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        if not app_settings.fred_api_key and not valid_sec_user_agent(app_settings.sec_user_agent):
            raise HTTPException(status_code=409, detail="Configure FRED API key or a descriptive SEC user agent with contact email.")
        async with collection_lock:
            start = next_collection_start(engine, app_settings.historical_window_days, definitions)
            source_results = []
            if app_settings.fred_api_key:
                source_results.append(await collect_fred(
                    engine, app_settings.fred_api_key, definitions, start,
                ))
            crypto_definitions = [item for item in definitions if item.source == "coinpaprika" and item.enabled]
            if crypto_definitions:
                source_results.append(await collect_coinpaprika(
                    engine, definitions, next_coinpaprika_start(engine),
                ))
            if valid_sec_user_agent(app_settings.sec_user_agent):
                source_results.append(await collect_sec_financials(engine, app_settings.sec_user_agent))
            normalization_result = recalculate_indicators(engine, definitions)
            scoring_result = recalculate_scenarios(engine, definitions)
            return {
            "status": "ok" if source_results and all(item["status"] == "ok" for item in source_results)
                          else "degraded" if source_results and any(item["status"] in {"ok", "degraded"} for item in source_results)
                          else "failed",
                "sources": source_results,
            "companies_succeeded": sum(int(item.get("series_succeeded", 0)) for item in source_results if item.get("source_id") == "sec"),
                "series_requested": sum(int(item["series_requested"]) for item in source_results),
                "series_succeeded": sum(int(item["series_succeeded"]) for item in source_results),
                "observations_received": sum(int(item["observations_received"]) for item in source_results),
                "normalization": normalization_result,
                "scoring": scoring_result,
            }

    async def periodic_collection() -> None:
        while True:
            try:
                result = await run_collection()
                logging.getLogger(__name__).info(
                    "Scheduled market data collection finished: %s/%s series, %s observations",
                    result["series_succeeded"], result["series_requested"], result["observations_received"],
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Scheduled FRED collection failed")
            await asyncio.sleep(app_settings.collection_interval_minutes * 60)

    @application.middleware("http")
    async def ingress_security_and_path(request: Request, call_next):
        if app_settings.ingress_mode:
            remote_host = request.client.host if request.client else None
            if remote_host != "172.30.32.2":
                return JSONResponse(status_code=403, content={"detail": "Ingress access only"})

            ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
            if ingress_path:
                request.scope["root_path"] = ingress_path
                current_path = request.scope.get("path", "/")
                if current_path == ingress_path or current_path.startswith(ingress_path + "/"):
                    request.scope["path"] = current_path[len(ingress_path):] or "/"
                    raw_path = request.scope.get("raw_path", b"")
                    raw_prefix = ingress_path.encode("utf-8")
                    if raw_path.startswith(raw_prefix):
                        request.scope["raw_path"] = raw_path[len(raw_prefix):] or b"/"
        return await call_next(request)

    @application.get("/api/v1/health", tags=["system"])
    async def health() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        try:
            db = database_check(engine)
        except Exception as exc:
            logging.getLogger(__name__).exception("Health check could not reach SQLite")
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {
            "status": "ok",
            "version": APP_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "database": db,
        }

    @application.get("/api/v1/indicators", tags=["indicators"])
    async def list_indicators() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        items = indicator_catalog(engine)
        active_count = sum(item.enabled for item in definitions)
        return {"items": items, "count": len(items), "active_count": active_count}

    @application.get("/api/v1/indicators/{indicator_id}/history", tags=["indicators"])
    async def get_indicator_history(
        indicator_id: str,
        days: int = Query(default=90, ge=1, le=3650),
    ) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        if not any(item.id == indicator_id for item in definitions):
            raise HTTPException(status_code=404, detail="indicator not found")
        return {"indicator_id": indicator_id, "days": days, "items": indicator_history(engine, indicator_id, days)}

    @application.post("/api/v1/admin/recalculate", tags=["admin"])
    async def recalculate_now() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        async with collection_lock:
            normalization = recalculate_indicators(engine, definitions)
            scoring = recalculate_scenarios(engine, definitions)
        return {"normalization": normalization, "scoring": scoring}

    @application.post("/api/v1/admin/recalculate/{scenario_id}", tags=["admin"])
    async def recalculate_one_scenario(scenario_id: str) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        async with collection_lock:
            try:
                return recalculate_scenarios(engine, definitions, scenario_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="scenario not found") from exc

    @application.get("/api/v1/scenarios", tags=["scenarios"])
    async def list_scenarios() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        items = scenario_catalog(engine)
        return {"items": items, "count": len(items)}

    @application.get("/api/v1/fragility", tags=["scenarios"])
    async def get_fragility_index() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        return next(row for row in scenario_catalog(engine) if row["id"] == "fragility_index")

    @application.get("/api/v1/stress", tags=["scenarios"])
    async def get_current_stress_index() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        return next(row for row in scenario_catalog(engine) if row["id"] == "current_stress_index")

    @application.get("/api/v1/scenarios/{scenario_id}/history", tags=["scenarios"])
    async def get_scenario_history(
        scenario_id: str,
        days: int = Query(default=90, ge=1, le=3650),
    ) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        scoring_config = load_scoring_config()
        configured = {**scoring_config.get("scenarios", {}), **scoring_config.get("composites", {})}
        if scenario_id not in configured:
            raise HTTPException(status_code=404, detail="scenario not found")
        return {"scenario_id": scenario_id, "days": days, "items": scenario_history(engine, scenario_id, days)}

    @application.get("/api/v1/scenarios/{scenario_id}", tags=["scenarios"])
    async def get_scenario(scenario_id: str) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        item = next((row for row in scenario_catalog(engine) if row["id"] == scenario_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        return item

    @application.get("/api/v1/data-sources", tags=["data"])
    async def list_data_sources() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        return {
            "items": data_sources(engine, bool(app_settings.fred_api_key), valid_sec_user_agent(app_settings.sec_user_agent)),
        }

    @application.get("/api/v1/companies", tags=["companies"])
    async def list_companies() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        items = company_catalog(engine)
        return {"items": items, "count": len(items), "available_count": sum(item["status"] == "available" for item in items)}

    @application.get("/api/v1/events", tags=["events"])
    async def list_events(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        return event_catalog(engine, limit)

    @application.get("/api/v1/companies/{company_id}", tags=["companies"])
    async def get_company(company_id: str) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        item = next(iter(company_catalog(engine, company_id)), None)
        if item is None:
            raise HTTPException(status_code=404, detail="company not found")
        return item

    @application.get("/api/v1/companies/{company_id}/history", tags=["companies"])
    async def get_company_history(
        company_id: str,
        days: int = Query(default=365, ge=1, le=3650),
    ) -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        if company_id not in {item["id"] for item in load_company_universe()}:
            raise HTTPException(status_code=404, detail="company not found")
        return {"company_id": company_id, "days": days, "items": company_history(engine, company_id, days)}

    @application.get("/api/v1/data-health", tags=["data"])
    async def get_data_health() -> dict[str, object]:
        if engine is None:
            raise HTTPException(status_code=503, detail="database not initialized")
        return data_health(engine, definitions)

    @application.post("/api/v1/admin/collect", tags=["admin"])
    async def collect_now() -> dict[str, object]:
        return await run_collection()

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="dashboard assets unavailable")
        return FileResponse(index_path)

    if STATIC_DIR.is_dir():
        application.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    return application


app = create_app()


if __name__ == "__main__":
    settings = app.state.settings
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)

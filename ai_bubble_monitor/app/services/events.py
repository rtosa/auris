from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text


def event_catalog(engine: Engine, limit: int = 20) -> dict[str, Any]:
    """Return sourced CAPEX events with same-company, same-period revisions."""
    with engine.connect() as connection:
        rows = connection.execute(text("""SELECT id, title, summary, company_id, category,
            published_at, source_url, confidence, guidance_period, guidance_lower, guidance_upper,
            guidance_unit, source_quote, source_accession FROM events
            WHERE category='capex_guidance'
            ORDER BY published_at DESC, id DESC""")).mappings().all()
    items = [dict(row) for row in rows]
    by_scope: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in items:
        if item["guidance_period"] and item["guidance_unit"]:
            key = (str(item["company_id"]), str(item["guidance_period"]), str(item["guidance_unit"]))
            by_scope.setdefault(key, []).append(item)
    for item in items:
        key = (str(item["company_id"]), str(item["guidance_period"]), str(item["guidance_unit"]))
        scoped = by_scope.get(key, [])
        previous = next((candidate for candidate in scoped if candidate["id"] != item["id"]
                         and (str(candidate["published_at"]), int(candidate["id"]))
                         < (str(item["published_at"]), int(item["id"]))), None)
        if previous is None:
            item["revision"] = None
            continue
        lower_change = _percent_change(item["guidance_lower"], previous["guidance_lower"])
        upper_change = _percent_change(item["guidance_upper"], previous["guidance_upper"])
        item["revision"] = {
            "previous_published_at": previous["published_at"],
            "previous_source_url": previous["source_url"],
            "previous_source_accession": previous["source_accession"],
            "previous_guidance_lower": previous["guidance_lower"],
            "previous_guidance_upper": previous["guidance_upper"],
            "lower_change_percent": lower_change,
            "upper_change_percent": upper_change,
        }
    selected = items[:limit]
    return {"items": selected, "count": len(selected)}


def _percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((float(current) - float(previous)) / abs(float(previous)) * 100, 2)

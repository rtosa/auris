from __future__ import annotations

import yaml
import json
from pathlib import Path
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, text

COMPANIES_FILE = Path(__file__).resolve().parents[2] / "config" / "companies.yaml"
SCORING_FILE = Path(__file__).resolve().parents[2] / "config" / "company_scoring.yaml"
REQUIRED_METRICS = ("revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditures",
                    "cash", "current_debt", "long_term_debt", "lease_liabilities", "total_assets",
                    "accounts_receivable", "stock_based_compensation", "interest_expense",
                    "debt_maturities_next_12m", "purchase_obligations")


def load_company_universe() -> list[dict[str, str]]:
    document = yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8"))
    companies = document.get("companies", []) if isinstance(document, dict) else []
    return [{key: str(company[key]) for key in ("id", "name", "ticker", "cik", "group")} for company in companies]


def load_company_scoring() -> dict[str, Any]:
    document = yaml.safe_load(SCORING_FILE.read_text(encoding="utf-8"))
    weights = document.get("weights", {}) if isinstance(document, dict) else {}
    if not weights or not 0.999 <= sum(float(value) for value in weights.values()) <= 1.001:
        raise ValueError("Company score weights must sum to 1.0")
    return document


def recalculate_company_scores(engine: Engine) -> dict[str, Any]:
    saved = 0
    config = load_company_scoring()
    weights = {key: float(value) for key, value in config["weights"].items()}
    minimum_components = int(config.get("minimum_components", 2))
    minimum_coverage = float(config.get("minimum_coverage", 0.4))
    with engine.begin() as connection:
        for company in load_company_universe():
            rows = connection.execute(text("""SELECT metric, value, reporting_period, end_date FROM company_financials
                WHERE company_id=:company_id ORDER BY end_date DESC, filing_date DESC, id DESC"""),
                                      {"company_id": company["id"]}).mappings().all()
            by_end: dict[str, dict[str, dict[str, Any]]] = {}
            for row in rows:
                if row["end_date"] and row["value"] is not None:
                    by_end.setdefault(str(row["end_date"]), {}).setdefault(row["metric"], dict(row))
            selected = None
            for end_date, period_rows in by_end.items():
                metrics = {key: float(row["value"]) for key, row in period_rows.items()}
                components: dict[str, tuple[float, list[str]]] = {}
                cash = metrics.get("cash")
                debt_complete = "current_debt" in metrics and "long_term_debt" in metrics
                debt = metrics.get("current_debt", 0.0) + metrics.get("long_term_debt", 0.0)
                assets = metrics.get("total_assets")
                if cash is not None and debt_complete:
                    components["cash_to_debt"] = (0.0 if debt <= 0 else max(0.0, min(100.0, 100.0 * (1 - cash / debt))),
                                                   ["cash", "current_debt", "long_term_debt"])
                if assets and assets > 0 and debt_complete:
                    components["debt_to_assets"] = (max(0.0, min(100.0, 100.0 * debt / assets)),
                                                    ["current_debt", "long_term_debt", "total_assets"])
                leases = metrics.get("lease_liabilities")
                if assets and assets > 0 and leases is not None:
                    components["lease_to_assets"] = (max(0.0, min(100.0, 100.0 * leases / assets)),
                                                      ["lease_liabilities", "total_assets"])
                operating_cash = metrics.get("operating_cash_flow")
                capex = metrics.get("capital_expenditures")
                if operating_cash is not None and capex is not None:
                    capex_risk = 100.0 if operating_cash <= 0 else min(100.0, max(0.0, 50.0 * capex / operating_cash))
                    components["capex_to_operating_cash_flow"] = (capex_risk, ["capital_expenditures", "operating_cash_flow"])
                    components["free_cash_flow"] = (80.0 if operating_cash - capex < 0 else 20.0,
                                                     ["operating_cash_flow", "capital_expenditures"])
                operating_income = metrics.get("operating_income")
                interest = metrics.get("interest_expense")
                if operating_income is not None and interest is not None:
                    coverage_ratio = float("inf") if interest <= 0 else operating_income / interest
                    risk = 0.0 if coverage_ratio >= 5 else 100.0 if coverage_ratio <= 1 else (5 - coverage_ratio) * 25
                    components["interest_coverage"] = (max(0.0, min(100.0, risk)),
                                                        ["operating_income", "interest_expense"])
                available_weight = sum(weights[key] for key in components if key in weights)
                coverage = available_weight / sum(weights.values())
                if len(components) >= minimum_components and coverage >= minimum_coverage:
                    selected = (end_date, period_rows, components, coverage, available_weight)
                    break
            if selected is None:
                continue
            end_date, period_rows, components, coverage, available_weight = selected
            score = sum(value * weights[key] for key, (value, _) in components.items()) / available_weight
            confidence = coverage
            representative = next((row for row in period_rows.values() if str(row["reporting_period"]).startswith("FY")),
                                  next(iter(period_rows.values())))
            score_id = connection.execute(text("""INSERT INTO company_scores
                (company_id, score, confidence, coverage, methodology_version, calculated_at, reporting_period, end_date)
                VALUES (:company_id, :score, :confidence, :coverage, :methodology_version, :calculated_at, :reporting_period, :end_date)"""), {
                "company_id": company["id"], "score": round(score, 2), "confidence": round(confidence, 4),
                "coverage": round(coverage, 4), "methodology_version": str(config["version"]),
                "calculated_at": datetime.now(UTC).isoformat(),
                "reporting_period": representative["reporting_period"], "end_date": end_date,
            }).lastrowid
            connection.execute(text("""INSERT INTO company_score_components
                (company_score_id, component_id, value, contribution, weight, source_metrics)
                VALUES (:score_id, :component_id, :value, :contribution, :weight, :source_metrics)"""), [
                {"score_id": score_id, "component_id": key, "value": value,
                 "contribution": round(value * weights[key] / available_weight, 4),
                 "weight": weights[key], "source_metrics": json.dumps(source_metrics)}
                for key, (value, source_metrics) in components.items()
            ])
            saved += 1
    return {"companies_scored": saved}


def company_catalog(engine: Engine, company_id: str | None = None) -> list[dict[str, Any]]:
    output = []
    with engine.connect() as connection:
        for company in load_company_universe():
            if company_id and company["id"] != company_id:
                continue
            rows = connection.execute(text("""SELECT metric, value, unit, reporting_period, filing_date, source_url,
                    accession_number, filing_form, fiscal_year, fiscal_period, start_date, end_date, fact_tag
                FROM company_financials WHERE company_id=:company_id
                ORDER BY end_date DESC, filing_date DESC, id DESC"""), {"company_id": company["id"]}).mappings().all()
            latest: dict[str, dict[str, Any]] = {}
            for row in rows:
                latest.setdefault(row["metric"], dict(row))
            for metric, current in latest.items():
                if not (metric.startswith("cloud_revenue_") or metric == "rpo_reported") or not current.get("end_date"):
                    continue
                current_end = date.fromisoformat(str(current["end_date"]))
                prior = []
                for candidate in rows:
                    if candidate["metric"] != metric or candidate["value"] is None or not candidate["end_date"]:
                        continue
                    candidate_end = date.fromisoformat(str(candidate["end_date"]))
                    delta_days = (current_end - candidate_end).days
                    if 320 <= delta_days <= 400:
                        prior.append((candidate_end, candidate))
                if prior:
                    prior_end, prior_row = max(prior, key=lambda item: item[0])
                    prior_value = float(prior_row["value"])
                    current_value = float(current["value"])
                    current["year_over_year"] = {
                        "prior_value": prior_value,
                        "prior_period": prior_end.isoformat(),
                        "change_percent": round((current_value / prior_value - 1) * 100, 2) if prior_value else None,
                        "source_url": prior_row["source_url"],
                        "accession_number": prior_row["accession_number"],
                    }
                else:
                    current["year_over_year"] = None
            financials = connection.execute(text("""SELECT id, score, confidence, coverage, methodology_version, calculated_at, reporting_period, end_date FROM company_scores
                WHERE company_id=:company_id ORDER BY id DESC LIMIT 1"""), {"company_id": company["id"]}).mappings().first()
            if financials:
                score_components = connection.execute(text("""SELECT component_id, value, contribution, weight, source_metrics
                    FROM company_score_components WHERE company_score_id=:score_id ORDER BY id"""),
                    {"score_id": financials["id"]}).mappings().all()
                financials = {**dict(financials), "components": [
                    {**dict(item), "source_metrics": json.loads(item["source_metrics"] or "[]")}
                    for item in score_components]}
            period_values = {row["metric"]: row["value"] for row in latest.values()}
            operating_cash = period_values.get("operating_cash_flow")
            capex = period_values.get("capital_expenditures")
            ocf_row = latest.get("operating_cash_flow")
            capex_row = latest.get("capital_expenditures")
            same_period = bool(ocf_row and capex_row and ocf_row.get("end_date") == capex_row.get("end_date")
                               and ocf_row.get("reporting_period") == capex_row.get("reporting_period"))
            if operating_cash is not None and capex is not None and same_period:
                fcf = float(operating_cash) - float(capex)
                latest["free_cash_flow"] = {**latest["operating_cash_flow"], "metric": "free_cash_flow", "value": fcf}
                period_values["free_cash_flow"] = fcf
            output.append({**company, "status": "available" if latest else "unavailable",
                           "metrics": latest,
                           "data_coverage": round(sum(metric in latest for metric in REQUIRED_METRICS) / len(REQUIRED_METRICS), 4),
                           "missing_metrics": [metric for metric in REQUIRED_METRICS if metric not in latest],
                           "score": dict(financials) if financials else None,
                           "history": company_history(engine, company["id"], 90, connection)})
    return output


def company_history(engine: Engine, company_id: str, days: int = 90, connection=None) -> list[dict[str, Any]]:
    query = text("""SELECT date(calculated_at) AS date, score, confidence, coverage, methodology_version
        FROM (
          SELECT s.*, ROW_NUMBER() OVER (PARTITION BY date(calculated_at) ORDER BY id DESC) AS day_rank
          FROM company_scores s WHERE company_id=:company_id
            AND calculated_at >= datetime('now', :date_shift)
        ) WHERE day_rank=1 ORDER BY date""")
    params = {"company_id": company_id, "date_shift": f"-{days} days"}
    if connection is not None:
        rows = connection.execute(query, params).mappings().all()
    else:
        with engine.connect() as local_connection:
            rows = local_connection.execute(query, params).mappings().all()
    return [dict(row) for row in rows]

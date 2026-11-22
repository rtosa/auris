from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import Engine, text

from app.collectors.sec_segments import (
    extract_customer_concentration_metrics,
    extract_filing_metrics,
    load_filing_configuration,
    load_filing_metrics,
)
from app.collectors.sec_guidance import extract_capex_guidance
from app.collectors.sec_ai_metrics import extract_ai_revenue_evidence

LOGGER = logging.getLogger(__name__)
COMPANIES_FILE = Path(__file__).resolve().parents[2] / "config" / "companies.yaml"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "Revenue"),
    "operating_income": ("OperatingIncomeLoss", "OperatingProfitLoss"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"),
    "current_debt": ("ShortTermBorrowings", "LongTermDebtCurrent", "BorrowingsCurrent", "DebtCurrent", "ConvertibleDebtCurrent", "ShorttermBorrowings"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt", "BorrowingsNoncurrent"),
    "debt_maturities_next_12m": ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",),
    "purchase_obligations": ("UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount",),
    "lease_liabilities": ("OperatingLeaseLiability", "FinanceLeaseLiability", "LeaseLiabilities", "LeaseLiability", "OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"),
    "interest_expense": ("InterestExpense", "InterestExpenseDebt", "InterestExpenseDebtExcludingAmortization", "InterestExpenseNonoperating", "InterestExpenseNonOperating", "InterestAndDebtExpense", "FinanceCosts"),
    "total_assets": ("Assets",),
    "accounts_receivable": ("AccountsReceivableNetCurrent", "TradeAndOtherCurrentReceivables"),
    "stock_based_compensation": ("ShareBasedCompensation", "SharebasedPaymentArrangementExpense"),
}
FLOW_METRICS = {"revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditures", "interest_expense", "stock_based_compensation"}
ALLOWED_FORMS = {"10-Q", "10-K", "20-F", "6-K"}
RECENT_FORMS = ALLOWED_FORMS | {"8-K"}
GUIDANCE_SCANNER = "capex_guidance_v4"
GUIDANCE_FILINGS_PER_ISSUER = 6
GUIDANCE_BATCH_PER_ISSUER = 2
GUIDANCE_FORMS = {"8-K", "6-K"}


def valid_sec_user_agent(value: str | None) -> bool:
    return bool(value and re.search(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b", value))


def load_companies() -> list[dict[str, str]]:
    payload = yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8"))
    companies = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(companies, list):
        raise ValueError("Invalid SEC company universe")
    return [{key: str(company[key]) for key in ("id", "name", "ticker", "cik", "group")} for company in companies]


class SecCollector:
    def __init__(self, user_agent: str, client: httpx.AsyncClient | None = None):
        if not valid_sec_user_agent(user_agent):
            raise ValueError("SEC_USER_AGENT must identify this app and include a contact email address")
        self.user_agent = user_agent
        self.client = client

    async def fetch_company(self, company: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.client is not None:
            return await self._fetch_with(self.client, company)
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                                     timeout=timeout, follow_redirects=True) as client:
            return await self._fetch_with(client, company)

    async def _fetch_with(self, client: httpx.AsyncClient, company: dict[str, str]):
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        submissions_response = await client.get(SUBMISSIONS_URL.format(cik=company["cik"]), headers=headers)
        submissions_response.raise_for_status()
        submissions = submissions_response.json()
        if str(int(str(submissions.get("cik", 0)))) != str(int(company["cik"])):
            raise ValueError(f"SEC CIK verification failed for {company['ticker']}")
        await asyncio.sleep(0.12)
        facts_response = await client.get(FACTS_URL.format(cik=company["cik"]), headers=headers)
        facts_response.raise_for_status()
        return submissions, facts_response.json()

    async def fetch_filing(self, source_url: str) -> str:
        await asyncio.sleep(0.12)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        if self.client is not None:
            response = await self.client.get(source_url, headers=headers)
            response.raise_for_status()
            return response.text
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            response = await client.get(source_url)
            response.raise_for_status()
            return response.text

    async def fetch_filing_index(self, cik: str, accession: str) -> dict[str, Any]:
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json"
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        await asyncio.sleep(0.12)
        if self.client is not None:
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        timeout = httpx.Timeout(30.0, connect=10.0)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()


async def collect_sec_financials(
    engine: Engine,
    user_agent: str,
    client: httpx.AsyncClient | None = None,
    historical_filing_limit: int | None = None,
) -> dict[str, Any]:
    collector = SecCollector(user_agent, client)
    companies = load_companies()
    successes, errors, warnings, fact_count, filing_metric_count, filing_metric_skips = 0, [], [], 0, 0, 0
    metadata_notices: list[dict[str, str]] = []
    guidance_filings_scanned = guidance_events_found = guidance_filings_pending = 0
    filing_configuration = load_filing_configuration()
    filing_metric_definitions = load_filing_metrics()
    concentration_definitions = filing_configuration.get("customer_concentration", {})
    concentration_default = filing_configuration.get("customer_concentration_default")
    history_config = filing_configuration.get("history", {})
    history_years = max(1, int(history_config.get("lookback_years", 4)))
    history_limit = max(0, int(historical_filing_limit if historical_filing_limit is not None
                                else history_config.get("max_filings_per_collection", 4)))
    company_filings: dict[str, list[dict[str, Any]]] = {}
    latest_filings: dict[str, dict[str, Any]] = {}
    now = datetime.now(UTC).isoformat()
    for company in companies:
        try:
            submissions, facts = await collector.fetch_company(company)
            reported_tickers = {str(value).upper() for value in submissions.get("tickers", [])}
            if reported_tickers and company["ticker"].upper() not in reported_tickers:
                notice = {"ticker": company["ticker"],
                          "message": f"Company Facts accepted by verified CIK; SEC metadata lists {', '.join(sorted(reported_tickers))} instead of the configured common-stock ticker."}
                metadata_notices.append(notice)
                LOGGER.info("SEC ticker metadata differs for %s: %s", company["ticker"], notice["message"])
            filings = _recent_filings(submissions)
            company_filings[company["id"]] = filings
            records = extract_company_facts(facts, company)
            _store_company_records(engine, company, records)
            _store_filings(engine, company, filings)
            fact_count += len(records)
            latest_filing = next((row for row in filings if row["form"] in {"10-Q", "10-K", "20-F"}), None)
            try:
                guidance_result = await _scan_capex_guidance_filings(engine, collector, company, filings)
                guidance_filings_scanned += int(guidance_result["scanned"])
                guidance_events_found += int(guidance_result["events"])
                guidance_filings_pending += int(guidance_result["pending"])
                if guidance_result.get("warning"):
                    warnings.append({"ticker": company["ticker"], "message": guidance_result["warning"]})
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                LOGGER.warning("SEC capex guidance extraction failed for %s: %s", company["ticker"], exc)
                warnings.append({"ticker": company["ticker"], "message": f"CAPEX guidance scan: {str(exc)[:250]}"})
            if company["id"] in filing_metric_definitions:
                if latest_filing:
                    latest_filings[company["id"]] = latest_filing
                metric_definitions = filing_metric_definitions[company["id"]]
                if latest_filing and latest_filing.get("primary_document"):
                    saved_counts = _stored_filing_metric_counts(engine, company["id"], latest_filing["accession"])
                    if all(saved_counts.get(str(item["metric"]), 0) >= int(item.get("minimum_facts", 1))
                           for item in metric_definitions):
                        filing_metric_skips += 1
                    else:
                        try:
                            document = await collector.fetch_filing(latest_filing["source_url"])
                            pilot_records = extract_filing_metrics(document, company, latest_filing,
                                                                   filing_metric_definitions[company["id"]])
                            _store_company_records(engine, company, pilot_records)
                            filing_metric_count += len(pilot_records)
                            if not pilot_records:
                                warnings.append({"ticker": company["ticker"], "message": "No configured filing-level XBRL facts found"})
                        except (httpx.HTTPError, ValueError) as exc:
                            LOGGER.warning("SEC filing-level metric pilot failed for %s: %s", company["ticker"], exc)
                            warnings.append({"ticker": company["ticker"], "message": f"Filing-level metric pilot: {str(exc)[:250]}"})
                    for definition in (item for item in metric_definitions if item.get("collect_prior_year_comparative")):
                        prior_filing = _prior_year_filing(filings, latest_filing)
                        if prior_filing is None:
                            warnings.append({"ticker": company["ticker"], "message": f"No prior-year filing found for {definition['metric']}"})
                            continue
                        target_period = prior_filing.get("report_date")
                        if not target_period:
                            continue
                        if _company_metric_period_exists(engine, company["id"], str(definition["metric"]), target_period):
                            filing_metric_skips += 1
                            continue
                        try:
                            document = await collector.fetch_filing(prior_filing["source_url"])
                            prior_records = extract_filing_metrics(document, company, prior_filing, [definition])
                            prior_records = [row for row in prior_records if row["period"] == target_period]
                            if prior_records:
                                _store_company_records(engine, company, prior_records)
                                filing_metric_count += len(prior_records)
                            else:
                                warnings.append({"ticker": company["ticker"], "message": f"No {definition['metric']} fact found for {target_period}"})
                        except (httpx.HTTPError, ValueError) as exc:
                            LOGGER.warning("SEC prior-year filing metric failed for %s: %s", company["ticker"], exc)
                            warnings.append({"ticker": company["ticker"], "message": f"Prior-year {definition['metric']} filing: {str(exc)[:250]}"})
            concentration_definition = concentration_definitions.get(company["id"]) or concentration_default
            if concentration_definition and latest_filing and latest_filing.get("primary_document"):
                concentration_scanner = "customer_concentration_v1"
                with engine.connect() as connection:
                    concentration_scanned = connection.execute(text("""SELECT 1 FROM sec_filing_scans
                        WHERE company_id=:company_id AND accession_number=:accession AND scanner=:scanner"""), {
                        "company_id": company["id"], "accession": latest_filing["accession"],
                        "scanner": concentration_scanner,
                    }).first() is not None
                concentration_metric = str(concentration_definition.get("metric", "customer_revenue_concentration_percent"))
                saved_counts = _stored_filing_metric_counts(engine, company["id"], latest_filing["accession"])
                if concentration_scanned or saved_counts.get(concentration_metric, 0) > 0:
                    filing_metric_skips += 1
                    if not concentration_scanned:
                        _mark_sec_filing_scanned(engine, company["id"], latest_filing["accession"], concentration_scanner)
                else:
                    try:
                        document = await collector.fetch_filing(latest_filing["source_url"])
                        concentration_records = extract_customer_concentration_metrics(
                            document, company, latest_filing, concentration_definition,
                        )
                        if concentration_records:
                            _store_company_records(engine, company, concentration_records)
                            filing_metric_count += len(concentration_records)
                        _mark_sec_filing_scanned(engine, company["id"], latest_filing["accession"], concentration_scanner)
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        LOGGER.warning("SEC customer-concentration extraction failed for %s: %s", company["ticker"], exc)
                        warnings.append({"ticker": company["ticker"],
                                         "message": f"Customer concentration filing {latest_filing['accession']}: {str(exc)[:250]}"})
            successes += 1
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("SEC collection failed for %s: %s", company["ticker"], exc)
            errors.append({"ticker": company["ticker"], "message": str(exc)[:300]})
    history_result = await _collect_historical_filing_metrics(
        engine, collector, companies, filing_metric_definitions, company_filings,
        latest_filings, history_years, history_limit,
    ) if history_limit else {"processed": 0, "skipped": 0, "pending": 0, "metrics_received": 0, "warnings": []}
    filing_metric_count += history_result["metrics_received"]
    warnings.extend(history_result["warnings"])
    notices = errors + warnings
    error_summary = "; ".join(f"{row['ticker']}: {row['message']}" for row in notices) or None
    with engine.begin() as connection:
        if successes:
            connection.execute(text("UPDATE data_sources SET last_success=:now, last_error=:error WHERE id='sec'"),
                               {"now": now, "error": error_summary})
        else:
            connection.execute(text("UPDATE data_sources SET last_error=:error WHERE id='sec'"),
                               {"error": error_summary or "No company facts were collected"})
    if successes:
        from app.services.companies import recalculate_company_scores
        score_result = recalculate_company_scores(engine)
    else:
        score_result = {"companies_scored": 0}
    return {"source_id": "sec", "status": "failed" if not successes else "degraded" if notices else "ok",
            "series_requested": len(companies), "series_succeeded": successes,
            "observations_received": fact_count, "filing_metrics_received": filing_metric_count,
            "filing_metric_documents_skipped": filing_metric_skips,
            "historical_filings_processed": history_result["processed"],
            "historical_filings_skipped": history_result["skipped"],
            "historical_filings_pending": history_result["pending"],
            "guidance_filings_scanned": guidance_filings_scanned,
            "guidance_events_found": guidance_events_found,
            "guidance_filings_pending": guidance_filings_pending,
            "company_scoring": score_result, "errors": errors,
            "warnings": warnings, "metadata_notices": metadata_notices}


def _mark_sec_filing_scanned(engine: Engine, company_id: str, accession: str, scanner: str) -> None:
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO sec_filing_scans (company_id, accession_number, scanner, scanned_at)
            VALUES (:company_id, :accession, :scanner, :scanned_at)
            ON CONFLICT(company_id, accession_number, scanner) DO NOTHING"""), {
            "company_id": company_id, "accession": accession, "scanner": scanner,
            "scanned_at": datetime.now(UTC).isoformat(),
        })


async def _collect_historical_filing_metrics(
    engine: Engine,
    collector: SecCollector,
    companies: list[dict[str, str]],
    definitions_by_company: dict[str, list[dict[str, Any]]],
    filings_by_company: dict[str, list[dict[str, Any]]],
    latest_filings: dict[str, dict[str, Any]],
    lookback_years: int,
    batch_limit: int,
) -> dict[str, Any]:
    lookback_candidates: list[tuple[date, str, dict[str, Any], dict[str, str], list[dict[str, Any]]]] = []
    skipped = 0
    for company in companies:
        company_id = company["id"]
        definitions = definitions_by_company.get(company_id, [])
        latest = latest_filings.get(company_id)
        if not definitions or not latest or not latest.get("report_date"):
            continue
        latest_date = date.fromisoformat(latest["report_date"])
        try:
            cutoff = latest_date.replace(year=latest_date.year - lookback_years)
        except ValueError:
            cutoff = latest_date.replace(year=latest_date.year - lookback_years, day=28)
        for filing in filings_by_company.get(company_id, []):
            if filing.get("form") != "10-Q" or not filing.get("report_date"):
                continue
            period_end = date.fromisoformat(filing["report_date"])
            if not cutoff <= period_end <= latest_date or filing["accession"] == latest["accession"]:
                continue
            period_definitions = [item for item in definitions
                                  if not item.get("historical_from")
                                  or period_end >= date.fromisoformat(str(item["historical_from"]))]
            if not period_definitions:
                skipped += 1
                continue
            stored = _stored_filing_metric_counts(engine, company_id, filing["accession"])
            complete = all(stored.get(str(item["metric"]), 0) >= int(item.get("minimum_facts", 1))
                           for item in period_definitions)
            if complete:
                skipped += 1
            else:
                lookback_candidates.append((period_end, company_id, filing, company, period_definitions))

    lookback_candidates.sort(key=lambda item: (item[0], item[1]))
    selected = lookback_candidates[:batch_limit]
    warnings: list[dict[str, str]] = []
    processed = metrics_received = 0
    for _period_end, company_id, filing, company, definitions in selected:
        try:
            document = await collector.fetch_filing(filing["source_url"])
            records = extract_filing_metrics(document, company, filing, definitions)
            if records:
                _store_company_records(engine, company, records)
                metrics_received += len(records)
            else:
                warnings.append({"ticker": company["ticker"],
                                 "message": f"No configured XBRL facts found in historical filing {filing['accession']}"})
            processed += 1
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("SEC historical filing collection failed for %s: %s", company["ticker"], exc)
            warnings.append({"ticker": company["ticker"], "message": f"Historical filing {filing['accession']}: {str(exc)[:250]}"})
    pending = 0
    for _period_end, company_id, filing, _company, definitions in lookback_candidates:
        stored = _stored_filing_metric_counts(engine, company_id, filing["accession"])
        if not all(stored.get(str(item["metric"]), 0) >= int(item.get("minimum_facts", 1))
                   for item in definitions):
            pending += 1
    return {"processed": processed, "skipped": skipped, "pending": pending,
            "metrics_received": metrics_received, "warnings": warnings}


def _stored_filing_metric_counts(engine: Engine, company_id: str, accession: str) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(text("""SELECT metric, COUNT(DISTINCT reporting_period) AS period_count
            FROM company_financials WHERE company_id=:company_id AND accession_number=:accession GROUP BY metric"""),
                                  {"company_id": company_id, "accession": accession}).all()
    return {str(row.metric): int(row.period_count) for row in rows}


def _company_metric_period_exists(engine: Engine, company_id: str, metric: str, period: str) -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text("""SELECT 1 FROM company_financials
            WHERE company_id=:company_id AND metric=:metric AND reporting_period=:period LIMIT 1"""),
                                        {"company_id": company_id, "metric": metric, "period": period}).first())


def _prior_year_filing(filings: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    if not current.get("report_date"):
        return None
    current_date = date.fromisoformat(current["report_date"])
    try:
        target = current_date.replace(year=current_date.year - 1)
    except ValueError:
        target = current_date.replace(year=current_date.year - 1, day=28)
    candidates = []
    for filing in filings:
        if filing.get("form") != current.get("form") or not filing.get("report_date"):
            continue
        filed_period = date.fromisoformat(filing["report_date"])
        distance = abs((filed_period - target).days)
        if distance <= 7 and filing["accession"] != current["accession"]:
            candidates.append((distance, filed_period, filing))
    return min(candidates, key=lambda item: (item[0], -item[1].toordinal()))[2] if candidates else None


def _recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    if not recent:
        return []
    rows = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in RECENT_FORMS:
            continue
        filed = recent.get("filingDate", [])[index]
        accession = recent.get("accessionNumber", [])[index]
        primary = recent.get("primaryDocument", [""] * (index + 1))[index]
        report_date = recent.get("reportDate", [None] * len(recent.get("form", [])))[index]
        rows.append({"form": form, "filed": filed, "report_date": report_date, "accession": accession,
                     "primary_document": primary, "source_url": f"https://www.sec.gov/Archives/edgar/data/{submissions.get('cik')}/{accession.replace('-', '')}/{primary}"})
    return rows


def extract_company_facts(payload: dict[str, Any], company: dict[str, str]) -> list[dict[str, Any]]:
    """Extract statement facts; convert cumulative Q2/Q3 flows to discrete quarters."""
    namespaces = payload.get("facts", {})
    facts = {**namespaces.get("ifrs-full", {}), **namespaces.get("us-gaap", {})}
    output: list[dict[str, Any]] = []
    for metric, aliases in FACT_TAGS.items():
        candidates = []
        for alias_index, tag in enumerate(aliases):
            concept = facts.get(tag)
            if not concept or not concept.get("units"):
                continue
            units = concept["units"]
            unit = "USD" if "USD" in units else next(iter(units))
            rows = [row for row in units[unit] if row.get("form") in ALLOWED_FORMS and row.get("filed") and row.get("end")]
            if rows:
                latest = max(rows, key=lambda row: (row["end"], row["filed"]))
                candidates.append((latest["end"], latest["filed"], -alias_index, tag, concept, unit))
        if not candidates:
            continue
        # Issuers sometimes change XBRL tags; prefer the alias disclosed most recently.
        _, _, _, tag, concept, unit = max(candidates)
        source_rows = [row for row in concept["units"][unit]
                       if row.get("form") in ALLOWED_FORMS and row.get("filed") and row.get("end")]
        # Prefer facts filed in 10-Q/10-K and use the SEC's exact filing accession as provenance.
        by_fiscal_year: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in source_rows:
            if row.get("start"):
                by_fiscal_year.setdefault((str(row.get("fy", "")), str(row.get("fp", ""))), []).append(row)
        converted = []
        for row in source_rows:
            start = date.fromisoformat(row["start"]) if row.get("start") else None
            end = date.fromisoformat(row["end"])
            duration = (end - start).days + 1 if start else None
            period = None
            value = float(row["val"])
            if metric == "capital_expenditures":
                value = abs(value)
            elif metric == "interest_expense":
                value = abs(value)
            if metric in FLOW_METRICS:
                if duration is not None and 70 <= duration <= 110:
                    fp = str(row.get("fp", ""))
                    if fp in {"Q1", "Q2", "Q3"}:
                        period = f"FY{row.get('fy', '')}{fp}"
                    elif fp == "FY" and duration < 150:
                        period = f"FY{row.get('fy', '')}Q4"
                    elif duration >= 330:
                        period = f"FY{row.get('fy', '')}"
                    else:
                        # Some issuers omit fp; infer a quarter only from quarter-length duration.
                        period = f"{end.year}-Q{((end.month - 1) // 3) + 1}"
                elif duration is not None and 120 <= duration <= 300 and row.get("fp") in {"Q2", "Q3"}:
                    prior_fp = "Q1" if row["fp"] == "Q2" else "Q2"
                    prior_rows = [prior for prior in by_fiscal_year.get((str(row.get("fy", "")), prior_fp), [])
                                  if prior.get("start") and date.fromisoformat(prior["end"]) < end
                                  and prior.get("form") in {"10-Q", "6-K"}]
                    if prior_rows:
                        previous = max(prior_rows, key=lambda item: item["end"])
                        value -= float(previous["val"])
                        period = f"FY{row.get('fy', '')}{row['fp']}"
                    else:
                        continue  # Preserve no mislabeled YTD value as a standalone quarter.
                elif duration is not None and duration >= 330:
                    period = f"FY{row.get('fy', end.year)}"
            else:
                period = end.isoformat()
            if period is None:
                continue
            converted.append({"metric": metric, "value": value, "unit": unit, "period": period,
                              "start": start.isoformat() if start else None, "end": end.isoformat(),
                              "filed": row["filed"], "accession": row.get("accn", ""),
                              "form": row.get("form"), "fy": row.get("fy"), "fp": row.get("fp"), "tag": tag,
                              "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{str(row.get('accn', '')).replace('-', '')}/"})
        # An accession can repeat a fact in multiple contexts; keep the most filing-specific row.
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in converted:
            key = (row["accession"], row["metric"], row["period"])
            previous = unique.get(key)
            if previous is None or (row["start"] or "") > (previous["start"] or ""):
                unique[key] = row
        output.extend(unique.values())
    return output


def _store_company_records(engine: Engine, company: dict[str, str], records: list[dict[str, Any]]) -> None:
    rows = [{"company_id": company["id"], "reporting_period": row["period"], "filing_date": row["filed"],
             "metric": row["metric"], "value": row["value"], "unit": row["unit"], "source_url": row["source_url"],
             "accession_number": row["accession"], "filing_form": row["form"], "fiscal_year": row["fy"],
             "fiscal_period": row["fp"], "start_date": row["start"], "end_date": row["end"], "fact_tag": row["tag"]}
            for row in records]
    if not rows:
        return
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO company_financials
            (company_id, metric, value, unit, reporting_period, filing_date, source_url, accession_number,
             filing_form, fiscal_year, fiscal_period, start_date, end_date, fact_tag)
            VALUES (:company_id, :metric, :value, :unit, :reporting_period, :filing_date, :source_url,
             :accession_number, :filing_form, :fiscal_year, :fiscal_period, :start_date, :end_date, :fact_tag)
            ON CONFLICT(company_id, accession_number, metric, reporting_period) DO UPDATE SET
              value=excluded.value, unit=excluded.unit, filing_date=excluded.filing_date,
              source_url=excluded.source_url, filing_form=excluded.filing_form,
              fiscal_year=excluded.fiscal_year, fiscal_period=excluded.fiscal_period,
              start_date=excluded.start_date, end_date=excluded.end_date, fact_tag=excluded.fact_tag"""), rows)


def _store_filings(engine: Engine, company: dict[str, str], filings: list[dict[str, Any]]) -> None:
    if not filings:
        return
    with engine.begin() as connection:
        for row in filings:
            connection.execute(text("""INSERT INTO events (title, summary, company_id, category, severity, published_at, source_url, confidence)
                SELECT :title, :summary, :company_id, 'company_filing', NULL, :published_at, :source_url, 1.0
                WHERE NOT EXISTS (SELECT 1 FROM events WHERE source_url=:source_url)"""), {
                "title": f"{company['ticker']} filed {row['form']}",
                "summary": f"SEC filing accession {row['accession']}", "company_id": company["id"],
                "published_at": row["filed"], "source_url": row["source_url"],
            })


async def _scan_capex_guidance_filings(
    engine: Engine,
    collector: SecCollector,
    company: dict[str, str],
    filings: list[dict[str, Any]],
) -> dict[str, Any]:
    with engine.connect() as connection:
        scanned_rows = connection.execute(text("""SELECT accession_number FROM sec_filing_scans
            WHERE company_id=:company_id AND scanner=:scanner"""), {
            "company_id": company["id"], "scanner": GUIDANCE_SCANNER,
        }).all()
    scanned_accessions = {str(row.accession_number) for row in scanned_rows}
    recent_guidance_filings = [row for row in filings if row["form"] in GUIDANCE_FORMS][:GUIDANCE_FILINGS_PER_ISSUER]
    candidates = [row for row in recent_guidance_filings if row["accession"] not in scanned_accessions]
    candidates = candidates[:GUIDANCE_BATCH_PER_ISSUER]
    events_found = 0
    for filing in candidates:
        index = await collector.fetch_filing_index(company["cik"], filing["accession"])
        directory = index.get("directory") or {}
        directory_items = directory.get("item") or []
        candidate_names = [str(item.get("name", "")) for item in directory_items
                           if str(item.get("name", "")).lower().endswith((".htm", ".html"))
                           and re.search(r"(?:ex|exhibit)[-_]?99[-_.]?1|99[-_.]1", str(item.get("name", "")), re.I)]
        if not candidate_names:
            candidate_names = [str(item.get("name", "")) for item in directory_items
                               if str(item.get("name", "")).lower().endswith((".htm", ".html"))
                               and re.search(r"(?:press|earnings|results|release|(?:q[1-4]?)?fy\d+pr)",
                                             str(item.get("name", "")), re.I)]
        if candidate_names:
            exhibit_name = sorted(candidate_names, key=lambda name: ("ex99" not in name.lower(), name.lower()))[0]
            base = filing["source_url"].rsplit("/", 1)[0]
            source_url = f"{base}/{exhibit_name}"
        else:
            source_url = filing["source_url"]
        document = await collector.fetch_filing(source_url)
        records = extract_capex_guidance(document, company, {**filing, "source_url": source_url})
        if records:
            record = records[0]
            key_payload = f"{company['id']}:{filing['accession']}:{record['source_quote']}"
            event_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
            with engine.begin() as connection:
                connection.execute(text("""INSERT INTO events
                    (title, summary, company_id, category, severity, published_at, source_url, confidence,
                     guidance_period, guidance_lower, guidance_upper, guidance_unit, source_quote,
                     source_accession, event_key)
                    VALUES (:title, :summary, :company_id, :category, NULL, :published_at, :source_url,
                     :confidence, :guidance_period, :guidance_lower, :guidance_upper, :guidance_unit,
                     :source_quote, :source_accession, :event_key)
                    ON CONFLICT(event_key) DO NOTHING"""), {**record, "event_key": event_key})
            events_found += len(records)
        ai_records = extract_ai_revenue_evidence(document, company, {**filing, "source_url": source_url})
        for record in ai_records:
            key_payload = f"{company['id']}:{filing['accession']}:{record['category']}:{record['source_quote']}"
            event_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
            with engine.begin() as connection:
                connection.execute(text("""INSERT INTO events
                    (title, summary, company_id, category, severity, published_at, source_url, confidence,
                     guidance_period, guidance_lower, guidance_upper, guidance_unit, source_quote,
                     source_accession, event_key)
                    VALUES (:title, :summary, :company_id, :category, NULL, :published_at, :source_url,
                     :confidence, :guidance_period, :guidance_lower, :guidance_upper, :guidance_unit,
                     :source_quote, :source_accession, :event_key)
                    ON CONFLICT(event_key) DO NOTHING"""), {**record, "event_key": event_key})
        events_found += len(ai_records)
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO sec_filing_scans (company_id, accession_number, scanner, scanned_at)
                VALUES (:company_id, :accession, :scanner, :scanned_at)
                ON CONFLICT(company_id, accession_number, scanner) DO NOTHING"""), {
                "company_id": company["id"], "accession": filing["accession"],
                "scanner": GUIDANCE_SCANNER, "scanned_at": datetime.now(UTC).isoformat(),
            })
        scanned_accessions.add(filing["accession"])
    pending = [row for row in recent_guidance_filings if row["accession"] not in scanned_accessions]
    return {"scanned": len(candidates), "events": events_found,
            "pending": len(pending)}

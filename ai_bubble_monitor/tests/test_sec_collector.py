import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.collectors.sec import SecCollector, _collect_historical_filing_metrics, _mark_sec_filing_scanned, _stored_filing_metric_counts, _store_company_records, collect_sec_financials, extract_company_facts, load_companies
from app.collectors.sec_segments import extract_customer_concentration_metrics, extract_filing_metrics, load_filing_configuration, load_filing_metrics
from app.collectors.definitions import load_definitions
from app.database import make_engine, upgrade_database
from app.services.companies import company_catalog, company_history, recalculate_company_scores
from app.services.scoring import recalculate_scenarios, scenario_catalog
from app.services.collection import seed_catalog


def _facts_fixture():
    filing = {"accn": "0000000001-25-000001", "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-04-20"}
    revenue = [
        {"start": "2025-01-01", "end": "2025-03-31", "val": 100, **filing},
        {"start": "2025-01-01", "end": "2025-06-30", "val": 250, **{**filing, "accn": "0000000001-25-000002", "fp": "Q2", "filed": "2025-07-20"}},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 450, **{**filing, "accn": "0000000001-25-000003", "fp": "Q3", "filed": "2025-10-20"}},
        {"start": "2025-01-01", "end": "2025-12-31", "val": 600, **{**filing, "accn": "0000000001-26-000001", "fp": "FY", "form": "10-K", "filed": "2026-02-20"}},
    ]
    return {
        "facts": {
            "ifrs-full": {"ShorttermBorrowings": {"units": {"USD": [{"end": "2025-03-31", "val": 5, **filing}]}}},
            "us-gaap": {
                "InterestExpense": {"units": {"USD": [{"start": "2025-01-01", "end": "2025-03-31", "val": -10, **filing}]}},
                "InterestExpenseNonoperating": {"units": {"USD": [{
                    "start": "2026-04-27", "end": "2026-07-26", "val": -12,
                    "accn": "0000000001-26-000002", "fy": 2027, "fp": "Q2", "form": "10-Q", "filed": "2026-08-20",
                }]}},
                "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [{
                    "start": "2025-01-01", "end": "2025-03-31", "val": -20, **filing,
                }]}},
                "PaymentsToAcquireProductiveAssets": {"units": {"USD": [{
                    "start": "2026-01-26", "end": "2026-04-26", "val": 300,
                    "accn": "0000000001-26-000002", "fy": 2027, "fp": "Q1", "form": "10-Q", "filed": "2026-05-20",
                }]}},
                "OperatingLeaseLiability": {"units": {"USD": [{"end": "2025-03-31", "val": 45, **filing}]}},
                "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths": {"units": {"USD": [{"end": "2025-03-31", "val": 15, **filing}]}},
                "UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount": {"units": {"USD": [{"end": "2025-03-31", "val": 25, **filing}]}},
                "Revenues": {"units": {"USD": revenue}},
            },
        },
    }


def test_sec_facts_convert_ytd_flows_to_quarter_values():
    company = load_companies()[0]
    records = extract_company_facts(_facts_fixture(), company)
    by_period = {row["period"]: row["value"] for row in records if row["metric"] == "revenue"}
    assert by_period["FY2025Q1"] == 100
    assert by_period["FY2025Q2"] == 150
    assert by_period["FY2025Q3"] == 200
    assert by_period["FY2025"] == 600
    assert all(row["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/") for row in records)
    assert next(row["value"] for row in records if row["metric"] == "interest_expense") == 12
    assert next(row["tag"] for row in records if row["metric"] == "interest_expense") == "InterestExpenseNonoperating"
    assert next(row["value"] for row in records if row["metric"] == "capital_expenditures") == 300
    assert next(row["tag"] for row in records if row["metric"] == "capital_expenditures") == "PaymentsToAcquireProductiveAssets"
    assert next(row["value"] for row in records if row["metric"] == "lease_liabilities") == 45
    assert next(row["value"] for row in records if row["metric"] == "current_debt") == 5
    assert next(row["value"] for row in records if row["metric"] == "debt_maturities_next_12m") == 15
    assert next(row["tag"] for row in records if row["metric"] == "debt_maturities_next_12m") == "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"
    assert next(row["value"] for row in records if row["metric"] == "purchase_obligations") == 25


def test_filing_inline_xbrl_extracts_dimensioned_cloud_revenue_and_company_rpo(tmp_path):
    company = next(row for row in load_companies() if row["id"] == "amzn")
    filing = {"form": "10-Q", "filed": "2026-10-30", "accession": "0001018724-26-000040",
              "source_url": "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000040/amzn-20260930.htm"}
    document = '''<html><body>
      <xbrli:context id="aws"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="aws-prior"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="total"><xbrli:entity><xbrli:segment><xbrldi:typedMember dimension="us-gaap:RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis"><us-gaap:date.domain>2026-10-01</us-gaap:date.domain></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="customer"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">amzn:OpenAIGroupPBCMember</xbrldi:explicitMember><xbrldi:typedMember dimension="us-gaap:RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis"><us-gaap:date.domain>2026-10-01</us-gaap:date.domain></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="aws" unitRef="usd" scale="6" format="ixt:num-dot-decimal">45,984</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="aws-prior" unitRef="usd" scale="6" format="ixt:num-dot-decimal">33,006</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueRemainingPerformanceObligation" contextRef="total" unitRef="usd" scale="9">578</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueRemainingPerformanceObligation" contextRef="customer" unitRef="usd" scale="9">38</ix:nonFraction>
    </body></html>'''
    records = extract_filing_metrics(document, company, filing)
    cloud_rows = [row for row in records if row["metric"] == "cloud_revenue_aws"]
    by_metric = {row["metric"]: row for row in records}
    assert {row["period"]: row["value"] for row in cloud_rows} == {
        "2025-09-30": 33_006_000_000, "2026-09-30": 45_984_000_000,
    }
    assert next(row for row in cloud_rows if row["period"] == "2026-09-30")["start"] == "2026-07-01"
    assert by_metric["rpo_reported"]["value"] == 578_000_000_000
    assert by_metric["rpo_reported"]["end"] == "2026-09-30"
    assert all(row["accession"] == filing["accession"] and row["source_url"] == filing["source_url"] for row in records)
    database = tmp_path / "filing-metrics.db"
    engine = make_engine(database)
    upgrade_database(database)
    _store_company_records(engine, company, records)
    with engine.connect() as connection:
        saved = connection.execute(text("""SELECT metric, value, accession_number, fact_tag, source_url
            FROM company_financials WHERE company_id='amzn' ORDER BY metric""")).all()
    assert [(row.metric, row.value) for row in saved] == sorted([
        ("cloud_revenue_aws", 33_006_000_000), ("cloud_revenue_aws", 45_984_000_000),
        ("rpo_reported", 578_000_000_000),
    ])
    assert all(row.accession_number == filing["accession"] and row.source_url == filing["source_url"] for row in saved)
    assert _stored_filing_metric_counts(engine, "amzn", filing["accession"]) == {"cloud_revenue_aws": 2, "rpo_reported": 1}
    prior_rpo = {"metric": "rpo_reported", "value": 200_000_000_000, "unit": "USD", "period": "2025-09-30",
                 "start": None, "end": "2025-09-30", "filed": "2025-10-30", "accession": "0001018724-25-000123",
                 "form": "10-Q", "fy": 2025, "fp": "Q3", "tag": "us-gaap:RevenueRemainingPerformanceObligation",
                 "source_url": "https://www.sec.gov/Archives/edgar/data/1018724/prior"}
    _store_company_records(engine, company, [prior_rpo])
    amazon = next(item for item in company_catalog(engine, "amzn"))
    assert amazon["metrics"]["cloud_revenue_aws"]["year_over_year"]["change_percent"] == 39.32
    assert amazon["metrics"]["rpo_reported"]["year_over_year"]["change_percent"] == 189.0
    engine.dispose()


def test_company_specific_filing_definitions_keep_cloud_and_total_rpo_facts_distinct():
    companies = {row["id"]: row for row in load_companies()}
    cases = [
        ("msft", "cloud_revenue_azure", "srt:ProductOrServiceAxis", "msft:AzureMember", 33_165_000_000, 683_000_000_000,
         "srt:MajorCustomersAxis", "msft:CommercialCustomersMember"),
        ("googl", "cloud_revenue_google", "us-gaap:StatementBusinessSegmentsAxis", "goog:GoogleCloudMember", 30_709_000_000, 567_700_000_000,
         "us-gaap:StatementBusinessSegmentsAxis", "goog:GoogleCloudMember"),
        ("orcl", "cloud_revenue_oracle", "srt:ProductOrServiceAxis", "orcl:CloudRevenuesMember", 11_607_000_000, 664_000_000_000,
         "srt:ProductOrServiceAxis", "orcl:CloudRevenuesMember"),
    ]
    for index, (company_id, metric, axis, member, cloud_value, rpo_value, secondary_axis, secondary_member) in enumerate(cases):
        document = f'''<html><body>
          <xbrli:context id="cloud{index}"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="{axis}">{member}</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="rpo{index}"><xbrli:entity></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
          <xbrli:context id="other{index}"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="{secondary_axis}">{secondary_member}</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
          <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="cloud{index}" unitRef="usd" scale="0">{cloud_value}</ix:nonFraction>
          <ix:nonFraction name="us-gaap:RevenueRemainingPerformanceObligation" contextRef="rpo{index}" unitRef="usd" scale="0">{rpo_value}</ix:nonFraction>
          <ix:nonFraction name="us-gaap:RevenueRemainingPerformanceObligation" contextRef="other{index}" unitRef="usd" scale="0">1</ix:nonFraction>
        </body></html>'''
        filing = {"form": "10-Q", "filed": "2026-10-30", "accession": f"test-{index}",
                  "source_url": "https://www.sec.gov/filing"}
        records = extract_filing_metrics(document, companies[company_id], filing,
                                         load_filing_metrics()[company_id])
        actual = {row["metric"]: row["value"] for row in records}
        assert actual[metric] == cloud_value
        assert actual["rpo_reported"] == rpo_value


def test_google_cloud_segment_total_wins_over_nested_product_dimension():
    company = next(row for row in load_companies() if row["id"] == "googl")
    filing = {"form": "10-Q", "filed": "2023-10-25", "accession": "googl-history",
              "source_url": "https://www.sec.gov/filing"}
    document = '''<xbrli:context id="total"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">goog:GoogleCloudMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2023-07-01</xbrli:startDate><xbrli:endDate>2023-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="product"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">goog:GoogleCloudMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">goog:GoogleCloudPlatformMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2023-07-01</xbrli:startDate><xbrli:endDate>2023-09-30</xbrli:endDate></xbrli:period></xbrli:context>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="total" unitRef="usd" scale="6">11,384</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="product" unitRef="usd" scale="6">8,410</ix:nonFraction>'''
    records = extract_filing_metrics(document, company, filing, load_filing_metrics()["googl"])
    assert [(row["metric"], row["value"]) for row in records] == [("cloud_revenue_google", 11_384_000_000)]


def test_customer_concentration_keeps_largest_revenue_customer_and_comparable():
    company = next(row for row in load_companies() if row["id"] == "crwv")
    definition = load_filing_configuration()["customer_concentration"]["crwv"]
    filing = {"form": "10-Q", "filed": "2026-11-12", "accession": "crwv-concentration",
              "source_url": "https://www.sec.gov/filing"}
    document = '''<xbrli:context id="current-a"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">crwv:CustomerAMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:RevenueFromContractWithCustomerMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="current-b"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">crwv:CustomerBMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:RevenueFromContractWithCustomerMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <xbrli:context id="current-ar"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">crwv:CustomerCMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:AccountsReceivableMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:context>
      <xbrli:context id="prior"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="srt:MajorCustomersAxis">crwv:CustomerAMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByTypeAxis">us-gaap:CustomerConcentrationRiskMember</xbrldi:explicitMember><xbrldi:explicitMember dimension="us-gaap:ConcentrationRiskByBenchmarkAxis">us-gaap:RevenueFromContractWithCustomerMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-09-30</xbrli:instant></xbrli:period></xbrli:context>
      <ix:nonFraction name="us-gaap:ConcentrationRiskPercentage1" contextRef="current-a" unitRef="pure" scale="-2">24</ix:nonFraction>
      <ix:nonFraction name="us-gaap:ConcentrationRiskPercentage1" contextRef="current-b" unitRef="pure" scale="-2">33</ix:nonFraction>
      <ix:nonFraction name="us-gaap:ConcentrationRiskPercentage1" contextRef="current-ar" unitRef="pure" scale="-2">81</ix:nonFraction>
      <ix:nonFraction name="us-gaap:ConcentrationRiskPercentage1" contextRef="prior" unitRef="pure" scale="-2">67</ix:nonFraction>'''
    records = extract_customer_concentration_metrics(document, company, filing, definition)
    assert {row["period"]: row["value"] for row in records} == {"2025-09-30": 67, "2026-09-30": 33}
    assert all(row["unit"] == "percent" and row["accession"] == filing["accession"] for row in records)
    duration_document = document.replace(
        "<xbrli:instant>2026-09-30</xbrli:instant>",
        "<xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate>",
    ).replace(
        "<xbrli:instant>2025-09-30</xbrli:instant>",
        "<xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate>",
    )
    duration_records = extract_customer_concentration_metrics(duration_document, company, filing, definition)
    assert {row["period"]: row["value"] for row in duration_records} == {"2025-09-30": 67, "2026-09-30": 33}


def test_customer_concentration_migration_converts_fraction_to_percent_points(tmp_path):
    database = tmp_path / "concentration-migration.db"
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "0005_company_scenario_inputs")
    engine = make_engine(database)
    seed_catalog(engine, load_definitions())
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO company_financials
            (company_id, metric, value, unit, reporting_period, filing_date, source_url)
            VALUES ('crwv','customer_revenue_concentration_percent',.33,'percent','2026-09-30',
                    '2026-11-12','https://www.sec.gov/filing')"""))
    engine.dispose()
    command.upgrade(config, "head")
    engine = make_engine(database)
    with engine.connect() as connection:
        value = connection.execute(text("SELECT value FROM company_financials WHERE metric='customer_revenue_concentration_percent'")).scalar_one()
    assert value == 33
    engine.dispose()


def test_customer_concentration_default_and_scan_marker_are_reusable(tmp_path):
    database = tmp_path / "concentration-scan.db"
    engine = make_engine(database)
    upgrade_database(database)
    default = load_filing_configuration()["customer_concentration_default"]
    assert default["tags"] == ["us-gaap:ConcentrationRiskPercentage1"]
    _mark_sec_filing_scanned(engine, "amzn", "acc-1", "customer_concentration_v1")
    _mark_sec_filing_scanned(engine, "amzn", "acc-1", "customer_concentration_v1")
    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM sec_filing_scans WHERE scanner='customer_concentration_v1'")).scalar_one()
    assert count == 1
    engine.dispose()


def test_historical_filing_backfill_is_incremental_and_skips_completed_filings(tmp_path):
    company = next(row for row in load_companies() if row["id"] == "amzn")
    database = tmp_path / "sec-history.db"
    engine = make_engine(database)
    upgrade_database(database)
    definitions = [
        {"metric": "cloud_revenue_aws", "tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
         "required_dimensions": {"us-gaap:StatementBusinessSegmentsAxis": "amzn:AmazonWebServicesSegmentMember"},
         "period_kind": "quarter", "include_year_ago_comparative": True, "minimum_facts": 2},
        {"metric": "rpo_reported", "tag": "us-gaap:RevenueRemainingPerformanceObligation",
         "required_dimension_axes": ["us-gaap:RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis"],
         "period_kind": "instant", "minimum_facts": 1},
    ]
    old = {"form": "10-Q", "filed": "2025-08-01", "report_date": "2025-06-30",
           "accession": "0001018724-25-000099", "source_url": "https://www.sec.gov/old-filing"}
    latest = {"form": "10-Q", "filed": "2026-08-01", "report_date": "2026-06-30",
              "accession": "0001018724-26-000099", "source_url": "https://www.sec.gov/latest-filing"}
    document = '''<xbrli:context id="cloud"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2025-06-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="cloud-prior"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">amzn:AmazonWebServicesSegmentMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2024-06-30</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="rpo"><xbrli:entity><xbrli:segment><xbrldi:typedMember dimension="us-gaap:RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis"><us-gaap:date.domain>2025-07-01</us-gaap:date.domain></xbrldi:typedMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="cloud" unitRef="usd" scale="6">30</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="cloud-prior" unitRef="usd" scale="6">20</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueRemainingPerformanceObligation" contextRef="rpo" unitRef="usd" scale="9">150</ix:nonFraction>'''

    class FakeCollector:
        async def fetch_filing(self, _source_url):
            return document

    args = (engine, FakeCollector(), [company], {"amzn": definitions},
            {"amzn": [latest, old]}, {"amzn": latest}, 4, 1)
    first = asyncio.run(_collect_historical_filing_metrics(*args))
    second = asyncio.run(_collect_historical_filing_metrics(*args))
    assert first["processed"] == 1 and first["metrics_received"] == 3 and first["pending"] == 0
    assert second["processed"] == 0 and second["skipped"] == 1 and second["pending"] == 0
    assert _stored_filing_metric_counts(engine, "amzn", old["accession"]) == {
        "cloud_revenue_aws": 2, "rpo_reported": 1,
    }

    class EmptyCollector:
        async def fetch_filing(self, _source_url):
            return "<html></html>"

    missing = {**old, "report_date": "2025-03-31", "accession": "0001018724-25-000088"}
    incomplete = asyncio.run(_collect_historical_filing_metrics(
        engine, EmptyCollector(), [company], {"amzn": definitions},
        {"amzn": [latest, missing]}, {"amzn": latest}, 4, 1,
    ))
    assert incomplete["processed"] == 1 and incomplete["pending"] == 1
    assert incomplete["warnings"]
    engine.dispose()


def test_sec_company_facts_persist_filing_provenance(tmp_path):
    database = tmp_path / "sec.db"
    engine = make_engine(database)
    upgrade_database(database)
    company = load_companies()[0]
    records = extract_company_facts(_facts_fixture(), company)
    _store_company_records(engine, company, records)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT metric, value, reporting_period, filing_form, fact_tag, source_url FROM company_financials WHERE reporting_period='FY2025Q2'")).one()
    assert row.metric == "revenue"
    assert row.value == 150
    assert row.filing_form == "10-Q"
    assert row.fact_tag == "Revenues"
    assert "/Archives/edgar/data/1045810/" in row.source_url
    corrected = next(dict(item) for item in records if item["metric"] == "revenue" and item["period"] == "FY2025Q1")
    corrected.update({"value": 101, "tag": "RevenueRestated"})
    _store_company_records(engine, company, [corrected])
    with engine.connect() as connection:
        updated = connection.execute(text("SELECT value, fact_tag FROM company_financials WHERE company_id='nvda' AND metric='revenue' AND reporting_period='FY2025Q1'")).one()
    assert updated.value == 101
    assert updated.fact_tag == "RevenueRestated"
    engine.dispose()


def test_sec_collector_requires_contact_email_and_sends_user_agent():
    try:
        SecCollector("AI Bubble Risk Monitor")
        assert False, "missing contact email should be rejected"
    except ValueError as exc:
        assert "contact email" in str(exc)

    seen = []
    async def respond(request):
        seen.append(request.headers.get("user-agent"))
        if "submissions" in request.url.path:
            return httpx.Response(200, json={"cik": 1045810, "tickers": ["NVDA"], "filings": {"recent": {}}})
        return httpx.Response(200, json=_facts_fixture())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            collector = SecCollector("AI Bubble Risk Monitor raul@example.test", client)
            await collector.fetch_company(load_companies()[0])

    asyncio.run(run())
    assert seen == ["AI Bubble Risk Monitor raul@example.test"] * 2


def test_company_score_uses_a_single_reporting_date_and_persists_components(tmp_path):
    database = tmp_path / "company-score.db"
    engine = make_engine(database)
    upgrade_database(database)
    with engine.begin() as connection:
        for metric, value, end_date, period in [
            ("cash", 50, "2025-09-30", "2025-09-30"),
            ("current_debt", 0, "2025-09-30", "2025-09-30"),
            ("long_term_debt", 50, "2025-09-30", "2025-09-30"),
            ("total_assets", 100, "2025-09-30", "2025-09-30"),
            ("operating_cash_flow", 100, "2025-06-30", "FY2025Q2"),
            ("capital_expenditures", 200, "2025-03-31", "FY2025Q1"),
        ]:
            connection.execute(text("""INSERT INTO company_financials
                (company_id, metric, value, unit, reporting_period, end_date, filing_date)
                VALUES ('nvda', :metric, :value, 'USD', :period, :end_date, :end_date)"""), {
                "metric": metric, "value": value, "period": period, "end_date": end_date,
            })
    result = recalculate_company_scores(engine)
    with engine.connect() as connection:
        score = connection.execute(text("SELECT score, coverage, methodology_version, end_date, reporting_period FROM company_scores WHERE company_id='nvda'")).one()
        components = connection.execute(text("SELECT component_id, weight, source_metrics FROM company_score_components WHERE company_score_id=(SELECT id FROM company_scores WHERE company_id='nvda')")).all()
    assert result["companies_scored"] == 1
    assert score.score == 25.0
    assert str(score.end_date) == "2025-09-30"
    assert {item.component_id for item in components} == {"cash_to_debt", "debt_to_assets"}
    assert score.coverage == 0.3
    assert score.methodology_version == "0.3.0"
    assert all(item.weight == 0.15 and item.source_metrics for item in components)
    assert len(company_history(engine, "nvda", 30)) == 1
    nvda = next(item for item in company_catalog(engine, "nvda"))
    assert "free_cash_flow" not in nvda["metrics"]
    engine.dispose()


def test_sec_company_group_scores_feed_credit_and_repricing_scenarios(tmp_path):
    database_path = tmp_path / "company-scenario.db"
    engine = make_engine(database_path)
    upgrade_database(database_path)
    definitions = load_definitions()
    seed_catalog(engine, definitions)
    with engine.begin() as connection:
        for company_id, score in (("crwv", 80), ("nbis", 70), ("iren", 60),
                                  ("msft", 15), ("amzn", 20), ("googl", 25), ("meta", 30), ("orcl", 35)):
            connection.execute(text("""INSERT INTO company_scores
                (company_id, score, confidence, coverage, methodology_version, calculated_at, reporting_period, end_date)
                VALUES (:company, :score, .8, .8, '0.3.0', :now, 'FY2026Q2', :end)"""), {
                "company": company_id, "score": score, "now": datetime.now(UTC).isoformat(),
                "end": date.today().isoformat(),
            })
        financials = {
            "crwv": {"current_debt": 50, "long_term_debt": 50, "debt_maturities_next_12m": 25,
                     "total_assets": 100, "purchase_obligations": 20},
            # NBIS has no matching tagged maturity schedule in the fixture; its
            # current debt is used only as a lower-confidence near-term proxy.
            "nbis": {"current_debt": 25, "long_term_debt": 75},
            "iren": {"current_debt": 20, "long_term_debt": 80, "debt_maturities_next_12m": 10,
                     "total_assets": 100, "purchase_obligations": 20},
            "msft": {"current_debt": 0, "long_term_debt": 100, "debt_maturities_next_12m": 5,
                     "total_assets": 100, "purchase_obligations": 5},
            "amzn": {"current_debt": 0, "long_term_debt": 100, "debt_maturities_next_12m": 5,
                     "total_assets": 100, "purchase_obligations": 5},
            "googl": {"current_debt": 0, "long_term_debt": 100, "debt_maturities_next_12m": 5,
                      "total_assets": 100, "purchase_obligations": 5},
            "meta": {"current_debt": 0, "long_term_debt": 100, "debt_maturities_next_12m": 5,
                     "total_assets": 100, "purchase_obligations": 5},
            "orcl": {"current_debt": 0, "long_term_debt": 100, "debt_maturities_next_12m": 5,
                     "total_assets": 100, "purchase_obligations": 5},
        }
        for company_id, metrics in financials.items():
            for metric, value in metrics.items():
                connection.execute(text("""INSERT INTO company_financials
                    (company_id, metric, value, unit, reporting_period, filing_date, end_date, source_url)
                    VALUES (:company, :metric, :value, 'USD', 'FY2026Q3', :day, :day, 'https://www.sec.gov/filing')"""), {
                    "company": company_id, "metric": metric, "value": value, "day": date.today().isoformat(),
                })
    recalculate_scenarios(engine, definitions)
    catalog = {item["id"]: item for item in scenario_catalog(engine)}
    assert catalog["credit_crunch"]["components"][0]["score"] == 70.0
    assert catalog["credit_crunch"]["components"][0]["coverage"] == 0.75
    assert catalog["credit_crunch"]["components"][1]["score"] == 25.0
    maturity = next(component for component in catalog["credit_crunch"]["components"] if component["id"] == "refinancing_risk")
    commitments = next(component for component in catalog["credit_crunch"]["components"] if component["id"] == "contractual_commitments")
    assert maturity["score"] is not None and maturity["coverage"] == 0.5833
    assert commitments["score"] is not None and commitments["coverage"] == 0.5
    assert any("Debt due in next 12 months" in item["source"] for item in maturity["inputs"])
    assert any("current-debt proxy" in item["source"] for item in maturity["inputs"])
    assert any("Unrecorded purchase obligations / assets" in item["source"] for item in commitments["inputs"])
    assert any(item["source"] == "SEC company scores: neoclouds (3/4 recent)"
               for component in catalog["credit_crunch"]["components"] for item in component["inputs"])
    engine.dispose()


def test_sec_collection_keeps_cik_verified_company_when_symbol_changed(tmp_path):
    database = tmp_path / "sec-collection.db"
    engine = make_engine(database)
    upgrade_database(database)
    seed_catalog(engine, load_definitions())
    companies = load_companies()
    by_cik = {company["cik"]: company for company in companies}

    async def respond(request):
        cik = request.url.path.split("CIK")[-1].split(".")[0]
        company = by_cik[cik]
        if "/submissions/" in request.url.path:
            ticker = "DBRHP" if company["ticker"] == "DBRG" else company["ticker"]
            return httpx.Response(200, json={"cik": int(cik), "tickers": [ticker], "filings": {"recent": {}}})
        return httpx.Response(200, json=_facts_fixture())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await collect_sec_financials(engine, "AI Bubble Monitor contact@example.test", client)

    result = asyncio.run(run())
    assert result["status"] == "ok"
    assert result["series_succeeded"] == 17
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["metadata_notices"][0]["ticker"] == "DBRG"
    assert "verified CIK" in result["metadata_notices"][0]["message"]
    catalog = company_catalog(engine)
    assert sum(item["status"] == "available" for item in catalog) == 17
    engine.dispose()

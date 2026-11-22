import asyncio

import httpx
from sqlalchemy import text

from app.collectors.sec import SecCollector, _scan_capex_guidance_filings, load_companies
from app.collectors.sec_guidance import extract_capex_guidance
from app.database import make_engine, upgrade_database
from app.services.events import event_catalog


AMAZON = """<html><body><p>We continue to expect 2026 cash capital expenditures to be approximately $220 billion.</p></body></html>"""


def test_extracts_explicit_annual_capex_guidance_with_source_quote():
    company = next(row for row in load_companies() if row["id"] == "amzn")
    filing = {"filed": "2026-10-29", "accession": "0001018724-26-000038", "source_url": "https://www.sec.gov/exhibit"}

    result = extract_capex_guidance(AMAZON, company, filing)

    assert len(result) == 1
    assert result[0]["guidance_period"] == "2026"
    assert result[0]["guidance_lower"] == result[0]["guidance_upper"] == 220_000_000_000
    assert result[0]["source_quote"] == "We continue to expect 2026 cash capital expenditures to be approximately $220 billion."
    assert result[0]["source_accession"] == filing["accession"]


def test_extracts_numeric_range_and_rejects_historical_or_unscaled_amounts():
    company = {"id": "test", "ticker": "TST"}
    filing = {"filed": "2026-11-01", "accession": "test-1", "source_url": "https://www.sec.gov/exhibit"}
    range_doc = "<p>For fiscal year 2027, we expect capital spending between $110 and $125 billion.</p>"
    assert [(row["guidance_lower"], row["guidance_upper"]) for row in extract_capex_guidance(range_doc, company, filing)] == [
        (110_000_000_000, 125_000_000_000),
    ]
    historical = "<p>Capital expenditures were $10 billion last year.</p>"
    ambiguous = "<p>We expect capital expenditures of $12 this year.</p>"
    assert extract_capex_guidance(historical, company, filing) == []
    assert extract_capex_guidance(ambiguous, company, filing) == []


def test_capex_extractor_handles_abbreviated_units_and_more_range_wording():
    company = {"id": "test", "ticker": "TST"}
    filing = {"filed": "2026-11-01", "accession": "test-2", "source_url": "https://www.sec.gov/exhibit"}
    assert [(row["guidance_lower"], row["guidance_upper"]) for row in extract_capex_guidance(
        "<p>For FY2027, we expect capex to be $250B-$275B.</p>", company, filing,
    )] == [(250_000_000_000, 275_000_000_000)]
    assert [(row["guidance_lower"], row["guidance_upper"]) for row in extract_capex_guidance(
        "<p>We expect 2027 capital expenditures to range from $1.2 billion to $1.4 billion.</p>", company, filing,
    )] == [(1_200_000_000, 1_400_000_000)]
    assert extract_capex_guidance("<p>We do not expect capital expenditures to exceed $5 billion.</p>", company, filing) == []


def test_capex_extractor_does_not_attribute_free_cash_flow_amount_to_capex():
    company = {"id": "test", "ticker": "TST"}
    filing = {"filed": "2026-11-01", "accession": "test-3", "source_url": "https://www.sec.gov/exhibit"}
    document = ("<p>Following a period of peak capex in FY27-FY28, capital expenditures are expected "
                "to step-down in FY29, supporting positive free cash flow of at least $30 billion in FY29.</p>")
    assert extract_capex_guidance(document, company, filing) == []


def test_sec_collector_fetch_filing_without_injected_client(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            return httpx.Response(200, text=AMAZON, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    collector = SecCollector("AI Bubble Monitor contact@example.test")

    assert asyncio.run(collector.fetch_filing("https://www.sec.gov/example")) == AMAZON


def test_guidance_scan_is_idempotent_and_preserves_event_provenance(tmp_path):
    database = tmp_path / "guidance.db"
    engine = make_engine(database)
    upgrade_database(database)
    company = next(row for row in load_companies() if row["id"] == "amzn")
    filing = {"form": "8-K", "filed": "2026-10-29", "accession": "0001018724-26-000038",
              "source_url": "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000038/amzn-20261029.htm"}

    class Collector:
        async def fetch_filing_index(self, cik, accession):
            assert cik == "0001018724" and accession == filing["accession"]
            return {"directory": {"item": [{"name": "amzn-20260930xex991.htm"}]}}

        async def fetch_filing(self, source_url):
            assert source_url.endswith("amzn-20260930xex991.htm")
            return AMAZON

    first = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, [filing]))
    second = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, [filing]))
    assert first == {"scanned": 1, "events": 1, "pending": 0}
    assert second == {"scanned": 0, "events": 0, "pending": 0}
    with engine.connect() as connection:
        event = connection.execute(text("""SELECT category, guidance_period, guidance_lower, guidance_upper,
            source_quote, source_accession, source_url FROM events WHERE category='capex_guidance'""")).one()
        scan_count = connection.execute(text("SELECT COUNT(*) FROM sec_filing_scans")).scalar_one()
    assert event.category == "capex_guidance"
    assert event.guidance_period == "2026"
    assert event.guidance_lower == event.guidance_upper == 220_000_000_000
    assert event.source_accession == filing["accession"]
    assert event.source_url.endswith("amzn-20260930xex991.htm")
    assert scan_count == 1
    engine.dispose()


def test_guidance_scan_batches_two_recent_8k_and_6k_filings(tmp_path):
    database = tmp_path / "guidance-batch.db"
    engine = make_engine(database)
    upgrade_database(database)
    company = next(row for row in load_companies() if row["id"] == "amzn")
    filings = [
        {"form": "6-K", "filed": "2026-11-03", "accession": "amzn-6k-new", "source_url": "https://www.sec.gov/Archives/amzn-new/main.htm"},
        {"form": "8-K", "filed": "2026-10-29", "accession": "amzn-8k-new", "source_url": "https://www.sec.gov/Archives/amzn-older/main.htm"},
        {"form": "8-K", "filed": "2026-10-01", "accession": "amzn-8k-old", "source_url": "https://www.sec.gov/Archives/amzn-old/main.htm"},
    ]

    class Collector:
        async def fetch_filing_index(self, _cik, accession):
            return {"directory": {"item": [{"name": f"{accession}-ex99-1.htm"}]}}

        async def fetch_filing(self, _source_url):
            return AMAZON

    first = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, filings))
    second = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, filings))
    assert first == {"scanned": 2, "events": 2, "pending": 1}
    assert second == {"scanned": 1, "events": 1, "pending": 0}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM sec_filing_scans")).scalar_one() == 3
        assert connection.execute(text("SELECT COUNT(*) FROM events WHERE category='capex_guidance'")).scalar_one() == 3
    engine.dispose()


def test_guidance_scan_advances_rolling_window_for_new_filings(tmp_path):
    database = tmp_path / "guidance-window.db"
    engine = make_engine(database)
    upgrade_database(database)
    company = next(row for row in load_companies() if row["id"] == "amzn")
    filings = [{"form": "8-K", "filed": f"2026-11-{day:02d}", "accession": f"amzn-{day}",
                "source_url": f"https://www.sec.gov/Archives/amzn-{day}/main.htm"}
               for day in range(30, 23, -1)]

    class Collector:
        async def fetch_filing_index(self, _cik, accession):
            return {"directory": {"item": [{"name": f"{accession}-ex99-1.htm"}]}}

        async def fetch_filing(self, _source_url):
            return AMAZON

    for expected_pending in (4, 2, 0):
        result = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, filings))
        assert result["scanned"] == 2
        assert result["pending"] == expected_pending

    new_filing = {"form": "8-K", "filed": "2026-12-01", "accession": "amzn-new",
                  "source_url": "https://www.sec.gov/Archives/amzn-new/main.htm"}
    following_filings = [new_filing, *filings[:5], filings[6]]
    result = asyncio.run(_scan_capex_guidance_filings(engine, Collector(), company, following_filings))
    assert result == {"scanned": 1, "events": 1, "pending": 0}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM sec_filing_scans WHERE company_id='amzn'")).scalar_one() == 7
    engine.dispose()


def test_event_catalog_compares_only_matching_company_and_guidance_year(tmp_path):
    database = tmp_path / "guidance-revisions.db"
    engine = make_engine(database)
    upgrade_database(database)
    with engine.begin() as connection:
        for title, year, filed, lower, upper, accession in [
            ("prior 2026", "2026", "2025-10-01", 100.0, 120.0, "prior-2026"),
            ("current 2026", "2026", "2026-01-01", 110.0, 130.0, "current-2026"),
            ("first 2027", "2027", "2026-04-01", 200.0, 250.0, "first-2027"),
        ]:
            connection.execute(text("""INSERT INTO events (title, summary, company_id, category, published_at,
                source_url, confidence, guidance_period, guidance_lower, guidance_upper, guidance_unit,
                source_quote, source_accession)
                VALUES (:title, :summary, 'amzn', 'capex_guidance', :filed, :url, 0.85,
                :year, :lower, :upper, 'USD', :summary, :accession)"""), {
                "title": title, "summary": title, "filed": filed, "url": f"https://sec.gov/{accession}",
                "year": year, "lower": lower, "upper": upper, "accession": accession,
            })
    result = event_catalog(engine, 10)
    current = next(item for item in result["items"] if item["source_accession"] == "current-2026")
    first = next(item for item in result["items"] if item["source_accession"] == "first-2027")
    assert current["revision"]["previous_source_accession"] == "prior-2026"
    assert current["revision"]["lower_change_percent"] == 10.0
    assert current["revision"]["upper_change_percent"] == 8.33
    assert first["revision"] is None
    engine.dispose()

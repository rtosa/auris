from app.collectors.sec_ai_metrics import extract_ai_revenue_evidence


def test_extracts_explicit_ai_run_rate_and_compute_segment_but_not_generic_cloud():
    company = {"id": "amzn", "ticker": "AMZN"}
    filing = {"filed": "2026-07-30", "report_date": "2026-06-30", "accession": "acc-1",
              "source_url": "https://sec.example/filing"}
    document = """<html><body>
      <p>AWS's AI business exceeded a $25 billion annual revenue run rate.</p>
      <p>AWS revenue increased to $42.2 billion in the quarter.</p>
    </body></html>"""

    rows = extract_ai_revenue_evidence(document, company, filing)

    assert len(rows) == 1
    assert rows[0]["category"] == "ai_monetization"
    assert rows[0]["guidance_lower"] == 25_000_000_000
    assert rows[0]["guidance_unit"] == "USD_annualized"
    assert rows[0]["guidance_period"] == "2026-06-30"
    assert "AWS's AI business" in rows[0]["source_quote"]


def test_extracts_nvidia_data_center_revenue_as_separate_compute_evidence():
    rows = extract_ai_revenue_evidence(
        "<p>Data Center revenue of $107.6 billion, up 110% from a year ago.</p>",
        {"id": "nvda", "ticker": "NVDA"},
        {"filed": "2026-11-18", "report_date": "2026-10-25", "accession": "acc-2",
         "source_url": "https://sec.example/nvda"},
    )

    assert len(rows) == 1
    assert rows[0]["category"] == "ai_compute_revenue"
    assert rows[0]["guidance_lower"] == 107_600_000_000
    assert rows[0]["guidance_unit"] == "USD_reported_period"


def test_keeps_adjacent_release_bullets_separate_when_each_has_an_amount():
    rows = extract_ai_revenue_evidence(
        "<ul><li>Revenue of $115.8 billion, up 103% from a year ago</li>"
        "<li>Data Center revenue of $107.6 billion, up 110% from a year ago</li></ul>",
        {"id": "nvda", "ticker": "NVDA"},
        {"filed": "2026-11-18", "report_date": "2026-10-25", "accession": "acc-3",
         "source_url": "https://sec.example/nvda"},
    )

    assert len(rows) == 1
    assert rows[0]["category"] == "ai_compute_revenue"
    assert rows[0]["guidance_lower"] == 107_600_000_000

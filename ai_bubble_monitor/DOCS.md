# AI Bubble Monitor

## What this app does

AI Bubble Monitor is a local Home Assistant app for financial and systemic risk monitoring of the AI ecosystem. The current iteration includes data collection, indicator normalization, and coverage-gated scenario scoring on top of the service, Ingress dashboard, SQLite persistence, and migrations.

The current collection workflow covers 20 active indicators: 18 FRED macro, market, energy, bank credit, employment, and industrial activity series, plus BTC and ETH prices from CoinPaprika. The PRD's Russell 2000 FRED series is retired because the provider removed it; the dashboard keeps it visible as retired. Scenario and composite scores use only fresh normalized data. Missing or stale inputs reduce coverage and confidence and are never treated as zero. Company financial ingestion is integrated into three scenarios; HA sensors and notifications remain later work.

## Open the dashboard

Start the app in Home Assistant and choose **Open Web UI**. Home Assistant Ingress provides access to the dashboard. The app accepts connections through the Supervisor proxy and rejects requests from other network addresses.

Ingress requests are normalized using the `X-Ingress-Path` header so API and documentation links continue to work beneath Home Assistant's URL prefix.

## Options

| Option | Default | Description |
|---|---:|---|
| `log_level` | `info` | App log verbosity: `debug`, `info`, `warning`, or `error`. |
| `collection_interval_minutes` | `60` | Interval for scheduled market data collection when FRED is configured. |
| `scoring_interval_minutes` | `60` | Reserved for the scoring scheduler in a later phase. |
| `fred_api_key` | empty | Free FRED API key; the application never returns or logs it. |
| `sec_user_agent` | empty | Descriptive app identifier and contact email required for SEC EDGAR collection. Kept local and sent as the SEC request header. |
| `historical_window_days` | `1825` | Initial FRED history window (1–10 years). |

FRED API keys are free and available from [the FRED API key page](https://fred.stlouisfed.org/docs/api/api_key.html).

## Data and storage

The app stores its SQLite database at `/data/ai_bubble_monitor.db`, outside Home Assistant Recorder. SQLite uses WAL mode. Database schema upgrades run automatically at app startup and are versioned with Alembic.

## Local endpoints

- `GET /api/v1/health` — service, database migration version, and timestamp.
- `GET /api/v1/indicators` — catalog and latest available observations.
- `GET /api/v1/indicators/{indicator_id}/history?days=90` — raw and normalized history, confidence, data quality, and source reference.
- `GET /api/v1/scenarios` and `GET /api/v1/scenarios/{scenario_id}` — latest scenario and composite results, coverage, confidence, classifications, and component contributions.
- `GET /api/v1/scenarios/{scenario_id}/history?days=90` — latest immutable snapshot per day.
- `GET /api/v1/companies` and `GET /api/v1/companies/{company_id}` — monitored company fundamentals, SEC filing provenance, missing metrics, weighted score components, and recent daily score history.
- `GET /api/v1/companies/{company_id}/history?days=365` — company risk score, coverage, confidence, and methodology version by day.
- `GET /api/v1/fragility` and `GET /api/v1/stress` — dedicated composite-index views.
- `GET /api/v1/data-sources` — source configuration and collection state.
- `GET /api/v1/data-health` — fresh, stale, and unavailable indicator counts.
- `POST /api/v1/admin/collect` — run configured source collections now.
- `POST /api/v1/admin/recalculate` — recalculate normalization and all scenario/composite scores.
- `POST /api/v1/admin/recalculate/{scenario_id}` — recalculate one scenario or composite; Fragility also recalculates its scenario inputs.
- `/docs` — local OpenAPI documentation.

## FRED collection

The configurable indicator catalog is `config/indicators.yaml`. It covers S&P 500, Nasdaq Composite, Russell 2000, VIX, US 2-year and 10-year Treasury yields, high-yield and investment-grade OAS, bank lending tightening, NFCI, St. Louis Fed Financial Stress Index, broad US dollar, CPI, unemployment, initial claims, industrial production, WTI crude, natural gas, Fed funds, Bitcoin, and Ethereum.

The first successful collection backfills `historical_window_days`. Scheduled collections revisit the latest observations with a seven-day overlap so revisions are picked up. Each observation retains its original date and FRED payload. FRED's `.` values are persisted as SQL NULL, counted as missing, and never converted to zero. Repeated pulls update a source/indicator/date record rather than creating duplicates.

Locally, set `FRED_API_KEY` in an ignored `.env` file. In Home Assistant, use the `fred_api_key` option. Without a key the source is explicitly reported as `unconfigured`, and the collection endpoint returns HTTP 409.

## SEC EDGAR collection

The configurable company universe is `config/companies.yaml`. The SEC connector uses the public Submissions and Company Facts APIs, stores original filing dates, accession numbers, reporting periods, XBRL tags, and filing links, and does not require an API key. The source stays disabled until `SEC_USER_AGENT` (local `.env`) or the Home Assistant `sec_user_agent` option contains an app identifier and contact email, as requested by the SEC's declared User-Agent guidance. Each configured CIK is checked against the Submissions response before its facts are accepted. A changed or missing ticker is surfaced as a warning while CIK-verified facts are retained. Requests are serialized and separated to remain well below SEC's published 10 requests/second ceiling. [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) · [SEC fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

The filing-level inline XBRL extractor currently targets AWS revenue and company-wide reported remaining performance obligations (RPO) for Amazon, Azure revenue and total RPO for Microsoft, Google Cloud revenue and total RPO for Alphabet, and Oracle Cloud revenue and total RPO for Oracle. Cloud revenue includes the matching quarter from the prior year in the same filing; RPO uses the prior-year filing for the same report date when it is not included in the current filing. Company metrics expose the prior-period value, its filing provenance, and the year-over-year percentage change when available. Repeated collection skips documents already processed by accession and period. Segment revenue requires an exact configured dimension; RPO excludes major-customer contexts and rejects ambiguous matches. The figures are cloud/backlog proxies, not AI-only sales, and are not included in scenario scoring until additional quarterly time series and comparable-growth methodology are validated. The wider SEC Financial Statement and Notes datasets contain as-filed dimensional disclosures, but are too large to ingest wholesale in the HA app; targeted filing collection avoids bulk downloads.

Numeric CAPEX outlook extraction scans up to six recent 8-K/6-K filings per issuer, at most two per issuer in each collection cycle, and reports unscanned filings as pending. It records only source-backed forward-looking numeric ranges or point estimates, including common B/M/T abbreviations. The events endpoint compares disclosures only for the same company and guidance year and links to the previous filing. CAPEX guidance remains an evidence feed and is excluded from all scenario scores pending issuer coverage and false-positive review.

Quarterly flow values use exact quarter-duration facts when available. For Q2/Q3 YTD facts, the prior quarter's cumulative value is subtracted only within the same fiscal year and XBRL concept; missing prior values are left unavailable. Annual facts remain annual. Filing and reporting dates are preserved separately, so fiscal calendars do not assume December year ends.

Company scores use the versioned weights in `config/company_scoring.yaml`. Inputs include cash/debt, debt/assets, lease liabilities/assets, CAPEX/operating cash flow, free cash flow direction, and interest coverage. A score is produced only when at least two components and 30% of configured weight are available from the same reporting end date. Missing components are excluded and reported through coverage; they are not treated as zero. Component rows preserve the source metric IDs and their weighted contributions. Scores are experimental vulnerability heuristics, not probabilities or cross-industry calibrated rankings.

Scenario methodology 0.8.0 feeds SEC company risk groups into Boom Continuation, Prolonged Repricing, and Credit Crunch. General group values are equal-weight averages for the configured SEC universe and represent a balance-sheet and CAPEX/FCF risk proxy, not direct credit spreads or CAPEX guidance. Credit Crunch also includes a debt maturity ratio (principal due within 12 months / reported total debt) and a purchase-commitments ratio (unrecorded obligations / assets, capped at a 50% ratio). These values require matching-period facts; they are calculated per company then averaged by group. A company fact is included only when its reporting period ended within the last 550 days, and a group is usable only when at least half of its companies have recent values. Group size, observed coverage, and source group are preserved with each scenario component. Missing or stale facts reduce scenario coverage. Purchase obligations are not a measure of customer concentration.

Energy shock uses the EIA WTI crude and Henry Hub natural gas spot price series distributed through FRED. The credit-crunch scenario includes the Federal Reserve's quarterly Senior Loan Officer Opinion Survey measure of bank lending tightening. Low-frequency normalization uses 48 monthly observations or 16 quarterly observations because the five-year initial backfill cannot reliably supply the generic 60-observation threshold at those frequencies. Macro crisis includes weekly initial claims and monthly total industrial production. These are broad macro proxies; they do not measure AI-specific energy contracts, private credit, or company demand. The FRED series IDs and freshness thresholds are configured in `config/indicators.yaml`.

See [`UNCOVERED_INPUTS.md`](UNCOVERED_INPUTS.md) for the source audit of remaining scenario gaps and the proposed implementation order.

The latest SEC filing also supplies largest disclosed customer revenue shares for seven issuers (CRWV, NBIS, IREN, APLD, DLR, MU, and TSM). The API and dashboard expose the share with filing provenance; receivables-only concentrations are excluded. Coverage is uneven, so this disclosure metric remains outside scenario scoring. A missing disclosure means unavailable, not zero. Earnings revisions and scheduled Proskauer private-credit ingestion remain deferred for the source and licensing limitations documented in `UNCOVERED_INPUTS.md`.

The SEC collector also scans the most recent 8-K per issuer for a numeric capital-expenditure outlook, preferring an Exhibit 99.1 earnings release. The prototype requires forward-looking language and an explicit USD scale, stores the quoted sentence, numeric point/range, period, accession, filing date, and source link, and avoids rescanning the same accession. `GET /api/v1/events` and the dashboard show these source-linked events. They are evidence only; no CAPEX guidance or AI-revenue score is inferred from them. Coverage and prose variation remain under evaluation.

## CoinPaprika collection

Bitcoin and Ethereum use CoinPaprika's public ticker and daily-history endpoints. History is limited to the most recent year on its free API; each collection also fetches the latest USD ticker and upserts the current UTC day. The app makes no more than four calls per collection cycle (history and ticker for each coin). CoinPaprika's attribution is shown in the dashboard.

## Indicator and scenario scoring

Cloud segment revenue and company-wide remaining performance obligations (RPO) are collected from issuer-specific inline XBRL facts in SEC filings. The latest filing supplies a same-quarter year-ago cloud revenue comparison; RPO comparisons come from the matching filing one year earlier. Historical `10-Q` filings are backfilled over a four-year window in batches of up to four documents per SEC collection cycle. Complete accessions are skipped, and each collection result reports processed, skipped, and pending filing counts. These proxies remain outside scenario scoring until coverage and multi-year distributions are evaluated.

Historical availability is issuer-specific. Microsoft's [2025 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000119312525256321/msft-20250930.htm) reports Azure growth rates but no absolute quarterly Azure revenue series; the quantified Azure line appears in its [2026 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000119312526436133/msft-20260930.htm), so older Azure revenue remains unavailable. The four-year window now contains 45 complete historical 10-Q accession/company combinations; a repeat collection processed zero new historical filings and skipped all 45. Cloud-revenue YoY sample counts and observed growth ranges are AWS 13 (12.16–39.32%), Google Cloud 10 (22.47–102.61%), Oracle Cloud 13 (21.32–61.52%), and Azure 1 (48.16%). Company-wide RPO has 7–10 YoY periods per issuer, with some very large step changes. These small and uneven samples are not calibrated scoring distributions.

The current methodology (`0.4.0`, `config/scoring.yaml`) stores normalized values in `indicator_values` and versions its configuration in `scoring_versions`. The default is a trailing five-year rolling percentile with a minimum of 60 valid observations. Higher-is-worse values retain their percentile; higher-is-better and lower-is-worse values invert it so that 100 represents more risk. Robust z-score, percentage change, rate of change, moving-average deviation, and volatility-adjusted momentum are available per indicator. Confidence combines configured source reliability and available history; freshness is applied separately. Missing observations remain missing and do not become zero.

Scenario weights follow the PRD. Available fresh inputs are reweighted only when coverage is at least 50% and the reweight factor is no more than 2x. Partial scores are marked `low_confidence`; scores below those gates are `unavailable`. Each calculation appends a scenario snapshot with component values and contributions; existing score rows are never overwritten. Cards show score changes against the nearest available daily snapshot at or before 7, 30, and 90 days (only when the reference is within three extra days), a daily sparkline, and persistence. Persistence means a score of at least 60 on 20 or more distinct days in the trailing 30-day window; it also reports the current consecutive-day streak. Missing days are not interpolated.

Current source coverage is incomplete. Boom continuation lacks AI earnings, compute-demand, and CAPEX signals; prolonged repricing lacks CAPEX/FCF, monetization, and earnings revisions; credit crunch lacks company, refinancing, and private-credit data; macro scoring has no energy series and uses a broad USD proxy for liquidity. The dashboard labels uncovered components. Fragility remains unavailable because its structural company inputs are missing. Current Stress and Macro Crisis can be emitted with low confidence when current data clears the configured coverage floor. These scores represent evidence strength, not probabilities. Historical backtesting is not included: current inputs do not provide point-in-time company fundamentals or outcome labels, so any claimed predictive performance would be misleading.

## Troubleshooting

If the app does not start, review its Home Assistant app logs. The health endpoint returns HTTP 503 if the database has not initialized or cannot be read. Restarting the app re-runs pending migrations.

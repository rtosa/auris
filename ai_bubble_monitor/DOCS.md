# AI Bubble Monitor

## What this app does

AI Bubble Monitor is a local Home Assistant app for financial and systemic risk monitoring of the AI ecosystem. The current iteration includes data collection, indicator normalization, and coverage-gated scenario scoring on top of the service, Ingress dashboard, SQLite persistence, and migrations.

The current collection workflow covers 24 active indicators: 21 FRED macro, market, energy, bank credit, employment, industrial activity, and liquidity series; BTC and ETH from CoinPaprika; and Proskauer's quarterly private-credit default headline rate as unscored context. The PRD's Russell 2000 FRED series is retired because the provider removed it; the dashboard keeps it visible as retired. Scenario and composite scores use only fresh normalized data. Missing or stale inputs reduce coverage and confidence and are never treated as zero. Company financial ingestion is integrated into three scenarios; HA sensors and notifications remain later work.

## Open the dashboard

Start the app in Home Assistant and choose **Open Web UI**. Home Assistant Ingress provides access to the dashboard. The app accepts connections through the Supervisor proxy and rejects requests from other network addresses.

Ingress requests are normalized using the `X-Ingress-Path` header so API and documentation links continue to work beneath Home Assistant's URL prefix.

## Options

| Option | Default | Description |
|---|---:|---|
| `log_level` | `info` | App log verbosity: `debug`, `info`, `warning`, or `error`. |
| `collection_interval_minutes` | `60` | Interval for scheduled collection of configured feeds. Public feeds need no API keys. |
| `scoring_interval_minutes` | `60` | Reserved for a separate scoring schedule; scores currently refresh after each collection. |
| `auto_collection_enabled` | `true` | Runs configured feeds on startup and at the collection interval. Public feeds work without API keys. |
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
- `GET /api/v1/analyst-estimates?days=365` — dated consensus estimates and history for Fiscal.ai-covered companies, with Zacks attribution.
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

Scenario methodology 0.10.0 keeps coverage and confidence separate. Coverage is the weighted share of configured inputs that are usable; confidence is the assessed reliability of inputs that are present and is not reduced again by missing coverage. A score below the configured 80% coverage target is labeled `partial_coverage`; `low_confidence` is reserved for available inputs whose reliability is below 60%. Those are operational labels, not calibrated probabilities. The broad USD and equity-momentum valuation proxy remain capped because neither is a direct complete measure. SEC company risk groups feed Boom Continuation, Prolonged Repricing, and Credit Crunch. In refinancing risk, matching-period current debt is used as a lower-confidence fallback only where a maturity schedule is missing; the component names this proxy in its source explanation. Purchase obligations remain the narrower unrecorded-obligations / assets metric and are not filled using general capex guidance.

Point-in-time Fiscal.ai/Zacks EPS and revenue consensus revisions now feed the earnings-revision components. Revisions are matched to the same company, metric and quarter; a directional heuristic maps +/-10% changes to 0/100 risk and is explicitly uncalibrated. Direct AI-business revenue and Data Center revenue disclosures are compared only within the same issuer and unit. The Data Center growth proxy is limited to NVIDIA and AMD; it is not a measure of utilization or total compute demand. AI monetization stays unavailable until at least half of the tracked issuers have comparable dated observations. The SEC evidence scanner preserves quote, filing, period and source link.

Energy shock uses the EIA WTI crude and Henry Hub natural gas spot price series distributed through FRED. The credit-crunch scenario includes the Federal Reserve's quarterly Senior Loan Officer Opinion Survey measure of bank lending tightening. Low-frequency normalization uses 48 monthly observations or 16 quarterly observations because the five-year initial backfill cannot reliably supply the generic 60-observation threshold at those frequencies. Macro crisis includes weekly initial claims and monthly total industrial production. These are broad macro proxies; they do not measure AI-specific energy contracts, private credit, or company demand. The FRED series IDs and freshness thresholds are configured in `config/indicators.yaml`.

See [`UNCOVERED_INPUTS.md`](UNCOVERED_INPUTS.md) for the source audit of remaining scenario gaps and the proposed implementation order.

The latest SEC filing also supplies largest disclosed customer revenue shares for seven issuers (CRWV, NBIS, IREN, APLD, DLR, MU, and TSM). The API and dashboard expose the share with filing provenance; receivables-only concentrations are excluded. Coverage is uneven, so this disclosure metric remains outside scenario scoring. A missing disclosure means unavailable, not zero. Proskauer private-credit data remains contextual only because of its quarterly frequency and source limitations documented in `UNCOVERED_INPUTS.md`.

The SEC collector also scans the most recent 8-K per issuer for a numeric capital-expenditure outlook, preferring an Exhibit 99.1 earnings release, and extracts explicit AI-business or Data Center revenue claims. It stores the quoted sentence, numeric point/range, period, accession, filing date, and source link, and avoids rescanning the same accession. `GET /api/v1/events` and the dashboard show these source-linked events. Generic CAPEX guidance remains evidence only; comparable AI-business revenue growth is not currently available at adequate coverage.

## CoinPaprika collection

Bitcoin and Ethereum use CoinPaprika's public ticker and daily-history endpoints. History is limited to the most recent year on its free API; each collection also fetches the latest USD ticker and upserts the current UTC day. The app makes no more than four calls per collection cycle (history and ticker for each coin). CoinPaprika's attribution is shown in the dashboard.

## Indicator and scenario scoring

Cloud segment revenue and company-wide remaining performance obligations (RPO) are collected from issuer-specific inline XBRL facts in SEC filings. The latest filing supplies a same-quarter year-ago cloud revenue comparison; RPO comparisons come from the matching filing one year earlier. Historical `10-Q` filings are backfilled over a four-year window in batches of up to four documents per SEC collection cycle. Complete accessions are skipped, and each collection result reports processed, skipped, and pending filing counts. These proxies remain outside scenario scoring until coverage and multi-year distributions are evaluated.

Historical availability is issuer-specific. Microsoft's [2025 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000119312525256321/msft-20250930.htm) reports Azure growth rates but no absolute quarterly Azure revenue series; the quantified Azure line appears in its [2026 10-Q](https://www.sec.gov/Archives/edgar/data/789019/000119312526436133/msft-20260930.htm), so older Azure revenue remains unavailable. The four-year window now contains 45 complete historical 10-Q accession/company combinations; a repeat collection processed zero new historical filings and skipped all 45. Cloud-revenue YoY sample counts and observed growth ranges are AWS 13 (12.16–39.32%), Google Cloud 10 (22.47–102.61%), Oracle Cloud 13 (21.32–61.52%), and Azure 1 (48.16%). Company-wide RPO has 7–10 YoY periods per issuer, with some very large step changes. These small and uneven samples are not calibrated scoring distributions.

Scenario methodology configuration is version `0.10.0` in `config/scoring.yaml`; indicator normalization stores values in `indicator_values` and versions its parameters in `scoring_versions`. The default is a trailing five-year rolling percentile with a minimum of 60 valid observations. Higher-is-worse values retain their percentile; higher-is-better and lower-is-worse values invert it so that 100 represents more risk. Robust z-score, percentage change, rate of change, moving-average deviation, and volatility-adjusted momentum are available per indicator. Scenario confidence combines configured source reliability and available history for inputs that are present; coverage is reported separately. Freshness is applied separately. Missing observations remain missing and do not become zero.

Scenario weights follow the PRD. Available fresh inputs are reweighted only when coverage is at least 50% and the reweight factor is no more than 2x. A calculated score below the 80% coverage target is marked `partial_coverage`; scores below the calculation gates are `unavailable`. Each calculation appends a scenario snapshot with component values and contributions; existing score rows are never overwritten. Cards show score changes against the nearest available daily snapshot at or before 7, 30, and 90 days (only when the reference is within three extra days), a daily sparkline, and persistence. Persistence means a score of at least 60 on 20 or more distinct days in the trailing 30-day window; it also reports the current consecutive-day streak. Missing days are not interpolated.

Coverage remains uneven. Boom continuation and prolonged repricing now receive dated consensus revisions, and the former receives a limited Data Center revenue growth proxy; direct AI monetization remains unavailable because comparable issuer histories are sparse. Credit Crunch still has incomplete company maturity and purchase-obligation facts. The source and component explanations identify the missing cohorts and lower-confidence current-debt proxy. Historical backtesting is not included: current inputs do not provide sufficient point-in-time fundamentals and outcome labels, so predictive performance is not established.

## Troubleshooting

If the app does not start, review its Home Assistant app logs. The health endpoint returns HTTP 503 if the database has not initialized or cannot be read. Restarting the app re-runs pending migrations.

# AI Bubble Risk Monitor — Implementation Plan

## Scope

Deliver the Home Assistant OS app in incremental phases. The first release provides a single container, local web UI through Ingress, a FastAPI backend, persistent SQLite storage under `/data`, configuration loading, logging, and health reporting. Market data collection and scoring are built in subsequent phases.

## Implementation steps

1. [x] Add the Home Assistant app manifest following the repository conventions used by `rtosa/VulnDashApp`.
2. [x] Create a Python 3.12 FastAPI service with `/api/v1/health`, startup validation, logs, and local OpenAPI docs.
3. [x] Initialize a versioned SQLite schema in `/data`, enable WAL mode, and isolate migration setup from API handlers.
4. [x] Add a React + TypeScript dashboard, built into static assets in the same image and served by FastAPI. Use same-origin paths and normalize HA Ingress prefixes.
5. [x] Add a multi-stage Docker build, HA options, local Docker Compose setup, test container target, and installation/configuration documentation.
6. [x] Run the full test suite in the Linux test container, build and run the app image, and verify local routes, assets, health, and persistence after a container restart.
7. [ ] Smoke-test installation through HAOS Ingress.

## Acceptance checks

- The HA app manifest identifies the app as x86_64/amd64 and enables Ingress.
- The container listens on the configured Ingress port and stores state only under `/data`.
- `GET /api/v1/health` reports service and database status with a timestamp.
- The dashboard loads from the same container and reports backend health without external APIs.
- SQLite survives container recreation when `/data` is mounted.
- Logs and configuration contain no secrets and the app starts when optional options are absent.

## Environment notes

- Docker Desktop 4.99.0 is running on the Windows development machine with the Linux engine available.
- The Linux container suite passes (36 tests). The production image and TypeScript production bundle build successfully.
- Local smoke checks pass for the dashboard, JS/CSS assets, OpenAPI JSON, `/docs`, FRED/CoinPaprika/SEC collection, scoring, and `/api/v1/health`. SQLite schema migrations upgrade automatically at app startup.
- The final real Ingress smoke test still requires installing the app in the user's HAOS instance. The private GitHub repository is being created for publication.

## Phase 2 — Market and macro data (initial FRED slice)

1. [x] Add YAML-backed definitions for 10 active FRED series plus the retired Russell 2000 indicator, and seed the indicator/source catalog.
2. [x] Add a FRED observations connector with five-year initial backfill, incremental overlap, retry handling, source attribution, and preservation of missing values and vintage payloads.
3. [x] Add persistence and API routes for indicator catalog, source state, data health, and manual collection; schedule collections when a key is configured.
4. [x] Replace the foundation page with a source-aware indicator dashboard and explicit unconfigured/stale/unavailable states.
5. [x] Mock-test parsing, missing values, upserts, incrementals, no-key behavior, and secret redaction. Build the app image and verify the dashboard and routes locally.
6. [x] Configure a free FRED API key locally and verify live historical ingestion against FRED. The live pull stored 9,927 observations across 10 active series; 9 are fresh and the Broad US Dollar series is marked stale based on its observation date.
7. [x] Add CPI, unemployment, and federal funds series from FRED, plus Bitcoin and Ethereum prices from the free CoinPaprika API, reaching 15 active indicators.
8. [x] Verify live collection of all new series and the dashboard in Docker; monitor freshness and provider errors.
9. [x] Add WTI, Henry Hub natural gas, bank lending tightening, initial claims, and industrial production to reach 20 active indicators and verify live FRED collection.

This is an incremental delivery; company financial ingestion and richer AI-specific inputs remain later work. CoinPaprika free historical data is daily and limited to one year. Russell 2000 is visibly marked retired because FRED removed its Russell index series in 2019; it is excluded from active collection until a supported alternate source is selected.

### Phase 2 validation

- The Docker test image passes all 18 tests; the production image and frontend build successfully.
- Live FRED and CoinPaprika collection both report `ready`. All 15 active indicators have values; 14 are fresh and one (the broad US dollar index) is stale based on its source observation date. Russell 2000 remains retired.
- Bitcoin and Ethereum each have 365 daily observations spanning 2025-11-23 through 2026-11-22. The dashboard renders the USD prices, macro values, source health, and observation dates.
- Five additional FRED series are configured for energy, bank lending, employment, and industrial activity; see the live validation below.

### Phase 2 expansion validation

- All five new FRED series are collected and fresh. FRED and CoinPaprika are `ready`; the SEC source remains degraded only by the known DBRG ticker-metadata warning.
- The active catalog contains 20 indicators, with 19 fresh and the broad US dollar series stale.
- Live scoring exposed low-frequency normalization limits: CPI and unemployment now use 48 monthly observations; quarterly SLOOS uses 16 quarters. All three produce normalized values after recalculation.
- MacroCrisis coverage is 90%; CreditCrunch coverage is 85%. Both still have low confidence because AI-specific earnings, customer concentration and direct private-credit coverage remain limited.
- The Phase 2 expansion validation was performed on version 0.9.0; see Phase 4 validation for the current release.

## Phase 3 — Scoring engine

1. [x] Implement six deterministic normalization methods and configurable rolling lookback/minimum sample settings.
2. [x] Persist normalized indicator history, data quality, source/history confidence, and methodology configuration.
3. [x] Expose normalized values in the indicator catalog and add a bounded indicator history endpoint plus manual recalculation route.
4. [x] Configure the PRD's scenario weights and indicator mappings, including explicit proxy limitations.
5. [x] Apply missing-component coverage and confidence gates; append each score run without overwriting earlier snapshots.
6. [x] Calculate Fragility and Current Stress indices with configured component weights and minimum coverage rules.
7. [x] Add scenario, composite, and daily-history API routes plus dashboard cards for classification, confidence, coverage, and contributors.
8. [x] Mock-test mappings, coverage, unavailable inputs, immutable history, migrations, and routes; the Docker suite passes 36 tests.
9. [x] Add score changes over 7/30/90 days, 30-day persistence detection, and daily historical sparklines.
10. [ ] Historical backtesting needs point-in-time company and market inputs plus outcome labels; current data coverage cannot support a meaningful backtest yet.

## Phase 4 — Company financial intelligence

1. [x] Add the PRD's 17-company universe and verify each configured CIK against SEC Submissions metadata before ingestion.
2. [x] Add SEC User-Agent configuration and public Submissions/Company Facts collection with source health, filing links, and no API key.
3. [x] Preserve filing/accession/XBRL/fiscal metadata and convert cumulative Q2/Q3 cash-flow facts into discrete quarters when prior YTD facts exist.
4. [x] Add company APIs and dashboard, and score only same-period metrics with persisted components.
5. [x] Configure the SEC contact email and verify live ingestion: all 17 companies have stored facts and experimental scores. SEC reports a ticker metadata warning for DigitalBridge; its CIK matches and its facts are retained.
6. [x] Expand debt, interest, and lease tag coverage based on observed Company Facts and version the weighted risk methodology.
7. [x] Persist component source inputs and company-score history; expose a daily history endpoint and dashboard sparklines.
8. [x] Feed SEC company-group risk scores into Boom Continuation, Prolonged Repricing, and Credit Crunch with freshness and proportional coverage gates.
9. [x] Audit the still-uncovered scenario inputs against free public FRED and SEC sources; document coverage, freshness, proxy limits, and next implementation steps in `ai_bubble_monitor/UNCOVERED_INPUTS.md`.
10. [x] Add SEC debt maturity and purchase-obligation facts as distinct, filing-linked metrics and score coverage-aware proxies in Credit Crunch; keep customer concentration separate.
11. [x] Prototype targeted inline XBRL extraction on Amazon's latest 10-Q for AWS segment revenue and total RPO; verify both values against the filing and keep them out of scenario scoring until group coverage is validated.
12. [x] Extend filing-specific revenue/RPO definitions to Microsoft, Alphabet, and Oracle; verify live fact values against each filing and leave Meta unavailable where no comparable cloud segment/RPO fact exists.
13. [x] Collect the same-quarter prior-year cloud revenue comparables and matching prior-year RPO filings; expose filing-linked YoY changes without adding them to scenario scores.
14. [x] Backfill four years of quarterly cloud revenue and company-wide RPO incrementally (maximum four filing documents per SEC cycle); verify complete accessions are skipped and evaluate multi-year growth coverage before scoring. Microsoft Azure absolute revenue is only available starting with the 2026 quantified disclosure, so earlier periods stay unavailable. The live backfill imported 44 documents; multi-year sample sizes remain uneven and are not scored.
15. [x] Extract filing-level largest customer share of revenue for the seven issuers with explicit revenue concentration facts; exclude receivables concentration, retain filing provenance, convert ratios to percentage points, and keep the unevenly covered metric outside scoring.
16. [x] Prototype numeric CAPEX guidance extraction from the latest SEC 8-K exhibit per issuer; retain source quote, period, numeric value/range, accession, and filing date, make scans idempotent, and surface events without scoring them.

### Phase 4 validation

- The Linux Docker test suite passes all 38 tests; `git diff --check` and Python bytecode compilation pass.
- Version 0.12.0 production image builds and starts locally. `/api/v1/health` reports status `ok` and database migration 0005.
- Live collection succeeded for all 17 SEC companies and stored 11,964 observations. The known DBRG ticker metadata warning remains; no SEC collection errors occurred.
- Recalculation on live data reports methodology 0.8.0. Credit Crunch includes debt due in 12 months / total debt (coverage 43.33%) and purchase obligations / assets (coverage 58.89%) as separate components. Each input records its company group and recent-fact count. Missing groups stay unavailable; customer concentration remains unavailable.
- The local app is reachable at `http://localhost:8099`. HAOS Ingress smoke testing remains pending installation on the user's Home Assistant instance.

### Filing-level XBRL pilot

- Amazon's latest 10-Q (accession `0001018724-26-000040`) contains AWS segment revenue of $45.984B for Q3 2026 and RPO of $578B as of 2026-09-30. The inline XBRL facts carry the segment and timing dimensions, filing date, accession, and source URL.
- The prototype parser rejects major-customer RPO contexts and requires a unique match for each configured fact. It stores the two selected facts as `cloud_revenue_aws` and `rpo_reported`; neither currently feeds scenario scores.
- Version 0.11.0 live SEC collection stored both configured facts and reported them in the Amazon company API.

### Cross-company filing validation

- Version 0.12.0 added configured tags/dimensions for Microsoft Azure, Alphabet Google Cloud, Oracle Cloud, and company-wide RPO for all three; Amazon remains covered. Meta is intentionally left without a cloud-segment proxy.
- Current filings inspected: Microsoft 10-Q (Sep 30, 2026), Alphabet 10-Q (Sep 30, 2026), and Oracle 10-Q (Aug 31, 2026). Their cloud segment revenue and RPO values are cross-checked against the respective segment tables and notes.
- Live SEC collection fetched four current filing documents and saved eight filing-level facts with source accession provenance: Amazon AWS $45.984B / RPO $578B; Microsoft Azure $33.165B / RPO $683B; Alphabet Google Cloud $30.709B / RPO $567.7B; Oracle Cloud $11.607B / RPO $664B. The values match filing tables/notes. Meta exposes no equivalent fact in its current filing, so it remains unavailable.
- Repeating SEC collection succeeded for all 17 companies and skipped all four already processed filing documents by accession, avoiding duplicate downloads.
- Scenario scoring did not consume the initial single-company pilot; cross-company collection and YoY comparison were completed in versions 0.12.0 and 0.13.0 below.

### Year-over-year comparison

- Version 0.13.0 extracts matching prior-year cloud-revenue facts from each current filing and retrieves the same report-date prior-year filing for RPO where the current report does not include the comparison.
- Latest quarter changes calculated from filing-linked values: AWS revenue +39.32%, Azure +48.16%, Google Cloud +102.61%, Oracle Cloud +61.52%; RPO: Amazon +189.00%, Microsoft +71.61%, Alphabet +259.99%, Oracle +45.84%.
- Company API and dashboard surface each current value, YoY change, and both filing links. Scenario scoring remains unchanged until a multi-year growth distribution is available.
- A live SEC refresh stored all 16 current/comparative facts. The next serialized refresh succeeded for all 17 issuers, added no duplicate metrics, and skipped all eight current/prior filing documents.
- All 38 Docker tests pass. The 0.13.0 production image builds, health reports `ok`, and the served frontend bundle contains the YoY panel and proxy explanation.

### Multi-year SEC history

- Version 0.14.0 adds a four-year lookback and processes up to four historical 10-Q documents per SEC collection. The collection response reports processed, skipped, and remaining incomplete filings; complete company/accession metric sets are not downloaded again.
- Docker test suite: 40 passed. The production container rebuilt successfully; `/api/v1/health` reports `ok`, version 0.14.0, and migration 0005. All 17 companies are exposed by the live API.
- Live SEC API cycle succeeded for all 17 companies with no errors. The corrected full backfill completed with zero pending; the repeat collection processed zero historical filings and skipped all 45 complete historical accession/company combinations. The known DBRG ticker-name warning remains.
- Historical samples contain cloud-revenue YoY observations for AWS (13; 12.16–39.32%), Google Cloud (10; 22.47–102.61%), Oracle Cloud (13; 21.32–61.52%), and Azure (1; 48.16%); RPO contains 7–10 observations per issuer. Growth ranges are uneven and contain large step changes, so neither proxy enters scenario scoring.
- Microsoft's 2025 10-Q reports Azure revenue growth percentages but no absolute quarterly Azure revenue value, while the quantified absolute Azure line appears in the 2026 10-Q. The Azure historical boundary reflects this source limitation.
- Proskauer's official Browse Insights page lists quarterly public headlines through Q3 2026; the latest four reported rates are Q4 2025 2.46%, Q1 2026 2.73%, Q2 2.51%, and Q3 2.41%. Loan count/original-principal coverage changes across releases, the full report is client-only, and the listing returned HTTP 500 on an immediate repeat request. Since the page is not a documented feed, scheduled scraping is deferred; existing SLOOS remains explicitly labeled as a bank-credit proxy.

### Customer concentration prototype

- Version 0.15.0 adds a filing-level largest-customer revenue share for CRWV, NBIS, IREN, APLD, DLR, MU, and TSM (7/17 issuers). Explicit revenue-benchmark/customer dimensions are used where available; receivables-only disclosures are excluded. Missing disclosure is unavailable, never zero.
- The company API and dashboard show the latest share and filing provenance. Current values span 11%–56%; the metric is deliberately not fed into scenario scores because issuer and sector coverage is uneven.
- A migration converts previously stored XBRL ratios to percentage points. Docker test suite passes all 42 tests; local production container reports healthy on version 0.15.0 and migration 0006. The latest live collection populated all seven values.
- Final repeat collection: FRED 18/18 and CoinPaprika 2/2 succeeded; SEC 17/17 succeeded with 0 collection errors, 15 filing documents skipped, 45 historical accessions skipped, and 0 historical filings pending. The existing DBRG ticker-metadata notice is the only SEC warning. Health is `ok`; the company API returns all 17 companies. `git diff --check` is clean.
- Re-audited broad USD coverage: FRED confirms configured `DTWEXBGS` remains the active broad goods-and-services successor series. Its Nov 13 observation was followed by the next scheduled release Nov 23, so the 7-day freshness threshold temporarily marked it stale on Nov 22. No narrower USD basket was substituted; direct liquidity remains unavailable.
- Version 0.17.0 expands CAPEX guidance scanning to a rolling window of six recent 8-K/6-K filings per issuer, in batches of two per collection cycle. It parses ranges and common unit abbreviations, rejects amounts tied to unrelated measures, and compares same-company/same-year revisions. Docker tests pass (52); the live scanner processed 102 filings across 17 issuers, displaying four sourced events including a META guidance revision. Guidance remains unscored.
- Analyst estimate revisions remain unavailable: Alpha Vantage terms do not fit the intended no-cost individual research/monitoring use, while FMP has no point-in-time as-of consensus snapshots. Proskauer automation also remains deferred pending a stable public feed. See `ai_bubble_monitor/UNCOVERED_INPUTS.md` for the source audit.

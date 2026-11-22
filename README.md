# AI Bubble Risk Monitor

Home Assistant OS app for local monitoring of financial sustainability and systemic risks in the AI ecosystem.

The local app collects macro and market data from FRED and CoinPaprika, and SEC filings for 17 configured public companies. It calculates coverage-gated scenario scores with confidence, component explanations, append-only history, 7/30/90-day changes, persistence detection, and daily trend charts. SEC CAPEX guidance is retained as a separate, source-linked company-wide proxy, with same-year revisions shown; it is not included in scenario scoring and is not represented as AI-specific CAPEX. Some scenario scores remain unavailable or low confidence while important inputs lack sufficient coverage. Historical backtesting awaits point-in-time fundamentals and outcome labels. The requested Russell 2000 series was removed from FRED and is marked retired.

The HA app lives in [`ai_bubble_monitor/`](ai_bubble_monitor/) and is built as one container with a FastAPI backend, a React dashboard, and SQLite persistence under `/data`.

See [`ai_bubble_monitor/README.md`](ai_bubble_monitor/README.md) for installation and local development instructions, and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for phase scope and acceptance checks. The current local build is version 0.17.0; the Docker test suite passes 52 tests.

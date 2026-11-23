# AI Bubble Risk Monitor

Home Assistant OS app for local monitoring of financial sustainability and systemic risks in the AI ecosystem.

The local app collects macro and market data from FRED and CoinPaprika, and SEC filings for 17 configured public companies. It calculates coverage-gated scenario scores with separate coverage and input-reliability confidence, component explanations, append-only history, 7/30/90-day changes, persistence detection, and daily trend charts. Point-in-time analyst revisions and a narrow SEC Data Center revenue growth proxy now contribute to relevant scenarios; direct AI revenue evidence remains unscored until comparable issuer history exists. SEC CAPEX guidance remains a separate sourced proxy and is not represented as AI-specific CAPEX. Historical backtesting awaits point-in-time fundamentals and outcome labels. The requested Russell 2000 series was removed from FRED and is marked retired.

The HA app lives in [`ai_bubble_monitor/`](ai_bubble_monitor/) and is built as one container with a FastAPI backend, a React dashboard, and SQLite persistence under `/data`.

See [`ai_bubble_monitor/README.md`](ai_bubble_monitor/README.md) for installation and local development instructions, and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for phase scope and acceptance checks. The current local build is version 0.23.9.


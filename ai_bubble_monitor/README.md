# AI Bubble Risk Monitor

Local-first Home Assistant app for monitoring financial sustainability and systemic risks in the AI ecosystem.

## Current functionality

The app loads 20 active indicators: 18 FRED macro, market, energy, credit, and activity series plus Bitcoin and Ethereum daily USD prices from CoinPaprika. FRED collects five years of observations on first run and refreshes recent observations hourly. CoinPaprika provides one year of daily history plus the current ticker price. Raw observations, original observation dates, missing values, and provider payloads are stored in SQLite. Indicator values are normalized deterministically to 0–100 using their configured risk direction and a 60-observation minimum. Four scenario scores and two composite indices use explicit coverage and confidence; unavailable inputs are never treated as zero. Historical snapshots are append-only.

SEC EDGAR ingestion now covers the PRD's 17 public companies, preserves filing and fiscal provenance, and converts cumulative Q2/Q3 cash-flow facts to quarter values where prior YTD filings are available. Live collection requires a descriptive SEC User-Agent with a contact email in `SEC_USER_AGENT` or the `sec_user_agent` app option. Company scores use a versioned weighted model with debt, liquidity, lease, CAPEX/FCF, and interest coverage components from a shared reporting date; daily score history is exposed in the dashboard and API. Scenario summaries also include 7/30/90-day changes, a 30-day persistence check, and daily history sparklines. Backtesting awaits point-in-time inputs and outcome labels.

The SEC collector incrementally scans up to six recent 8-K/6-K filings per issuer (two per collection cycle) for explicit numeric CAPEX outlook, retains the quoted text and filing link, and shows supported disclosures under Latest risk events. Same-company, same-year guidance updates are compared with the previous disclosure. This prototype is not included in scoring; unstructured or ambiguous guidance stays unavailable.


## Install in Home Assistant OS

1. Add this GitHub repository under **Settings → Apps → Install app → ⋮ → Repositories**.
2. Install **AI Bubble Monitor** and start it.
3. Open the app from the Home Assistant Apps page. Its UI is served through Supervisor Ingress.

The app stores the database at `/data/ai_bubble_monitor.db`. Do not point Home Assistant Recorder at this database.

## Local container build

Requires Docker Desktop with the WSL 2 backend on Windows, or Docker Engine on Linux/macOS.

```sh
docker compose up --build -d
```

Open `http://localhost:8099`. Check `http://localhost:8099/api/v1/health` and `http://localhost:8099/docs`. Local database files are stored under `dev-data/` and are ignored by Git.

To enable live FRED collection locally, copy `.env.example` to `.env` and set `FRED_API_KEY` to a free key from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html). Compose reads `.env`; the file is ignored by Git. The app runs an initial backfill and then collects on the configured interval. You can also trigger a collection with `POST /api/v1/admin/collect`.

Indicator and scoring configuration is in `config/indicators.yaml` and `config/scoring.yaml`. The API exposes `/api/v1/indicators`, `/api/v1/indicators/{id}/history`, `/api/v1/scenarios`, `/api/v1/scenarios/{id}/history`, `/api/v1/fragility`, `/api/v1/stress`, `/api/v1/data-sources`, and `/api/v1/data-health`. `POST /api/v1/admin/recalculate` reruns normalization and scoring. Scenario inputs and limitations are documented in `DOCS.md`.

Run the backend test suite in the same Linux environment used for the Home Assistant container:

```sh
docker compose --profile test run --build --rm backend-tests
```

## Configuration

The Home Assistant options schema exposes `log_level`, collection and scoring intervals, `fred_api_key`, and `historical_window_days`. The FRED key is free; CoinPaprika's public endpoint needs no key. The dashboard credits CoinPaprika.

In Home Assistant mode, the service accepts requests only from the Supervisor Ingress proxy (`172.30.32.2`). The standalone Compose setup binds to `127.0.0.1` and disables that proxy check for local development.

## Development checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```

The dashboard uses same-origin relative URLs so it can run beneath the Home Assistant Ingress path.

# AI Bubble Risk Monitor

Local-first Home Assistant app for monitoring financial sustainability and systemic risks in the AI ecosystem.

## Current functionality

The app loads 24 active indicators: 21 FRED macro, market, energy, credit, activity, and liquidity series, two CoinPaprika crypto prices, and one contextual Proskauer private-credit series. FRED collects five years of observations on first run and refreshes recent observations hourly. CoinPaprika provides one year of daily history plus the current ticker price. Raw observations, original observation dates, missing values, and provider payloads are stored in SQLite. Indicator values are normalized deterministically to 0–100 using their configured risk direction and a source-specific minimum history. Four scenario scores and two composite indices use explicit coverage and confidence; unavailable inputs are never treated as zero. Historical snapshots are append-only.

SEC EDGAR ingestion now covers the PRD's 17 public companies, preserves filing and fiscal provenance, and converts cumulative Q2/Q3 cash-flow facts to quarter values where prior YTD filings are available. Live collection requires a descriptive SEC User-Agent with a contact email in `SEC_USER_AGENT` or the `sec_user_agent` app option. Company scores use a versioned weighted model with debt, liquidity, lease, CAPEX/FCF, and interest coverage components from a shared reporting date; daily score history is exposed in the dashboard and API. Scenario summaries also include 7/30/90-day changes, a 30-day persistence check, and daily history sparklines. Backtesting awaits point-in-time inputs and outcome labels.

The SEC collector incrementally scans up to six recent 8-K/6-K filings per issuer (two per collection cycle) for explicit numeric CAPEX outlook and directly attributed AI / Data Center revenue evidence, retains the quoted text and filing link, and shows supported disclosures under Latest risk events. Comparable same-company disclosures are linked. These events are evidence only and are not included in scoring; unstructured or ambiguous statements stay unavailable. Proskauer's headline private-credit default series is market context only and is also unscored.


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

To enable live FRED collection locally, copy `.env.example` to `.env` and set `FRED_API_KEY` to a free key from [FRED](https://fred.stlouisfed.org/docs/api/api_key.html). To enable Fiscal.ai, set `FISCAL_API_KEY` too. CoinPaprika and Proskauer provide public context feeds without keys. Compose reads `.env`; the file is ignored by Git. Automatic collection runs on startup and on the configured interval. You can pause it with `AUTO_COLLECTION_ENABLED=false` or trigger a collection with `POST /api/v1/admin/collect`.

Indicator and scoring configuration is in `config/indicators.yaml` and `config/scoring.yaml`. The API exposes `/api/v1/indicators`, `/api/v1/indicators/{id}/history`, `/api/v1/scenarios`, `/api/v1/scenarios/{id}/history`, `/api/v1/fragility`, `/api/v1/stress`, `/api/v1/data-sources`, and `/api/v1/data-health`. `POST /api/v1/admin/recalculate` reruns normalization and scoring. Scenario inputs and limitations are documented in `DOCS.md`.

Run the backend test suite in the same Linux environment used for the Home Assistant container:

```sh
docker compose --profile test run --build --rm backend-tests
```

## Configuration

The Home Assistant options schema exposes `log_level`, collection and scoring intervals, automatic collection, `fred_api_key`, `fiscal_api_key`, `sec_user_agent`, `allow_direct_access`, and `historical_window_days`. The app maps container port 8099 to host port 8100 by default. If 8100 is in use, change the host port in the app's Configuration tab under Network. `allow_direct_access` enables direct LAN and Cloudflare Tunnel origin access. Cloudflare Access protects the external hostname; LAN access bypasses it.

Docker checks `/api/v1/health` and restarts the process according to the Compose restart policy. The local SQLite database persists in `dev-data/`. To create a consistent standalone backup while the app is running:

```sh
docker compose exec -T ai-bubble-monitor python -c "import sqlite3; source=sqlite3.connect('/data/ai_bubble_monitor.db'); target=sqlite3.connect('/data/ai_bubble_monitor.backup.db'); source.backup(target); target.close(); source.close()"
```

The backup appears at `dev-data/ai_bubble_monitor.backup.db`; copy it outside the repository for safekeeping. To restore it, stop the container first and replace `dev-data/ai_bubble_monitor.db`, then start the container again.

Fiscal.ai Street Consensus is optional and stored separately from market indicators. Configure `FISCAL_API_KEY` in local `.env` or `fiscal_api_key` in Home Assistant options. Its free key has a restricted company list and current-calendar-year revision history; plan entitlements may exclude Zacks Street Consensus. The API requires this attribution wherever estimates are displayed: “Data provided by Zacks Investment Research, Inc., www.zacksdata.com”. Use `GET /api/v1/analyst-estimates` to inspect collected snapshots. Dated EPS and revenue consensus revisions now feed the earnings-revisions components as an uncalibrated directional proxy; the dashboard continues to show the underlying 30/90-day changes.

The dashboard's Analyst estimate revisions panel shows the next reported quarter's EPS and revenue consensus for supported companies, with 30- and 90-day changes versus prior dated snapshots and the latest analyst counts. Missing prior snapshots are shown as unavailable. Zacks attribution links directly to its site.

In Home Assistant mode, the service accepts requests only from the Supervisor Ingress proxy (`172.30.32.2`). The standalone Compose setup binds to `127.0.0.1` and disables that proxy check for local development.

## Development checks

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```

The dashboard uses same-origin relative URLs so it can run beneath the Home Assistant Ingress path.

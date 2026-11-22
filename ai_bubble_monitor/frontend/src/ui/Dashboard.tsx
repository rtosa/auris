import { useEffect, useState } from "react";

type Health = {
  status: string;
  version: string;
  timestamp: string;
  database: { ok: boolean; migration_version: string };
};

type Indicator = {
  id: string;
  name: string;
  category: string;
  unit: string;
  frequency: string;
  source_id: string;
  availability_note: string | null;
  latest_observation_date: string | null;
  latest_value: number | null;
  latest_normalized_value: number | null;
  normalized_confidence: number | null;
  normalized_data_quality: string | null;
  data_status: "fresh" | "stale" | "unavailable" | "retired";
};

type DataHealth = {
  active_indicators: number;
  fresh_indicators: number;
  stale_indicators: number;
  unavailable_indicators: number;
  retired_indicators: number;
  failed_sources: number;
  last_successful_collection: string | null;
  last_scoring_execution: string | null;
};

type DataSource = {
  id: string;
  name: string;
  status: "unconfigured" | "pending" | "ready" | "degraded" | "failed";
  configured: boolean;
  last_success: string | null;
  last_error: string | null;
};

type CompanyMetric = {
  value: number;
  unit: string;
  reporting_period: string;
  filing_date: string;
  source_url: string;
  year_over_year?: {
    prior_value: number;
    prior_period: string;
    change_percent: number | null;
    source_url: string | null;
    accession_number: string | null;
  } | null;
};

type Company = {
  id: string; name: string; ticker: string; cik: string; group: string; status: string; data_coverage: number;
  missing_metrics: string[];
  metrics: Record<string, CompanyMetric>;
  score: { score: number; confidence: number; coverage: number; methodology_version: string; reporting_period: string; end_date: string; components: { component_id: string; value: number; contribution: number; weight: number; source_metrics: string[] }[] } | null;
  history: { date: string; score: number; confidence: number; coverage: number }[];
};

type RiskEvent = {
  id: number; title: string; summary: string; company_id: string; category: string; published_at: string;
  source_url: string; confidence: number; guidance_period: string | null; guidance_lower: number;
  guidance_upper: number; guidance_unit: string; source_quote: string; source_accession: string;
  revision: { previous_published_at: string; previous_source_url: string; previous_source_accession: string;
    previous_guidance_lower: number; previous_guidance_upper: number; lower_change_percent: number | null;
    upper_change_percent: number | null } | null;
};

type Scenario = {
  id: string;
  name: string;
  score: number | null;
  confidence: number;
  coverage: number;
  status: "pending" | "available" | "low_confidence" | "unavailable";
  classification: string | null;
  calculated_at: string | null;
  components: { id: string; name: string; score: number | null; contribution: number; coverage: number; status: string }[];
  top_contributors: { name: string; contribution: number; inputs: { source: string | null; value: number | null; weight: number }[] }[];
  score_changes: Record<string, { value: number; reference_at: string } | null>;
  persistence: { threshold: number; lookback_days: number; required_days: number; days_above_threshold: number; current_streak_days: number; persistent: boolean };
  history: { date: string; score: number; status: string }[];
};

type CollectionResult = {
  status: string;
  series_succeeded: number;
  series_requested: number;
  observations_received: number;
  sources: { source_id: string; status: string }[];
};

type LoadState = "loading" | "ready" | "error";

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(`./api/v1/${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return (await response.json()) as T;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: value.includes("T") ? "short" : undefined }).format(date);
}

function formatValue(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "Unavailable";
  const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  if (unit === "percent") return `${number}%`;
  if (unit === "usd") return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: value < 1 ? 4 : 2 }).format(value);
  return number;
}

function yearOverYearLabel(metric: CompanyMetric | undefined): string {
  const change = metric?.year_over_year?.change_percent;
  if (change === null || change === undefined) return "YoY unavailable";
  return `${change > 0 ? "+" : ""}${change.toFixed(1)}% YoY`;
}

function YearOverYearLine({ metric }: { metric: CompanyMetric | undefined }) {
  const prior = metric?.year_over_year;
  return <span className="series-id">{yearOverYearLabel(metric)}{prior?.source_url ? <> · <a className="financial-source-link" href={prior.source_url} target="_blank" rel="noreferrer">prior filing ↗</a></> : null}</span>;
}

function Sparkline({ points }: { points: { score: number }[] }) {
  const valid = points.slice(-90);
  if (valid.length < 2) return <div className="sparkline-empty">Daily history builds as scoring runs.</div>;
  const coords = valid.map((point, index) => `${(index / (valid.length - 1)) * 300},${(58 - point.score * 0.5).toFixed(1)}`).join(" ");
  return <svg className="scenario-sparkline" viewBox="0 0 300 60" role="img" aria-label={`Score history, ${valid.length} daily points`}><line x1="0" y1="28" x2="300" y2="28" /><polyline points={coords} /></svg>;
}

export function Dashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [dataHealth, setDataHealth] = useState<DataHealth | null>(null);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [collecting, setCollecting] = useState(false);
  const [collectionMessage, setCollectionMessage] = useState("");
  const [collectionError, setCollectionError] = useState("");

  async function refresh() {
    setState("loading");
    try {
      const [nextHealth, catalog, nextDataHealth, sourceList, scenarioList, companyList, eventList] = await Promise.all([
        readJson<Health>("health"),
        readJson<{ items: Indicator[] }>("indicators"),
        readJson<DataHealth>("data-health"),
        readJson<{ items: DataSource[] }>("data-sources"),
        readJson<{ items: Scenario[] }>("scenarios"),
        readJson<{ items: Company[] }>("companies"),
        readJson<{ items: RiskEvent[] }>("events?limit=20"),
      ]);
      setHealth(nextHealth);
      setIndicators(catalog.items);
      setDataHealth(nextDataHealth);
      setSources(sourceList.items);
      setScenarios(scenarioList.items);
      setCompanies(companyList.items);
      setEvents(eventList.items);
      setState("ready");
    } catch {
      setState("error");
    }
  }

  async function collectNow() {
    setCollecting(true);
    setCollectionMessage("");
    setCollectionError("");
    try {
      const response = await fetch("./api/v1/admin/collect", { method: "POST", headers: { Accept: "application/json" } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? `Collection returned ${response.status}`);
      const result = body as CollectionResult;
      setCollectionMessage(`${result.series_succeeded}/${result.series_requested} serii procesate din ${result.sources.map((source) => source.source_id).join(" și ")}; ${result.observations_received.toLocaleString()} observații.`);
      await refresh();
    } catch (error) {
      setCollectionError(error instanceof Error ? error.message : "Colectarea datelor a eșuat.");
    } finally {
      setCollecting(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const fred = sources.find((source) => source.id === "fred");
  const coinpaprika = sources.find((source) => source.id === "coinpaprika");
  const sec = sources.find((source) => source.id === "sec");
  const collectionAvailable = Boolean(fred?.configured || coinpaprika?.configured);
  const dataReady = state === "ready" && Boolean(health?.database.ok);
  const coverage = dataHealth?.active_indicators
    ? Math.round((dataHealth.fresh_indicators / dataHealth.active_indicators) * 100)
    : 0;

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="./" aria-label="AI Bubble Monitor home">
          <span className="brand-mark" aria-hidden="true">A</span>
          <span>AI Bubble Monitor</span>
        </a>
        <span className="local-pill"><span className="pulse-dot" /> LOCAL INSTANCE</span>
      </header>

      <section className="hero">
        <div className="eyebrow">MARKET DATA <span>·</span> PHASE 2 + SCORING</div>
        <h1>Know what the data says.</h1>
        <p className="intro">A local record of market and financial conditions, with source dates and missing observations kept visible. Scenario indices show evidence strength, coverage, and confidence.</p>
      </section>

      <section className="status-grid" aria-label="Data collection status">
        <article className="status-card main-status">
          <div className="card-heading"><span className="card-icon green">⌁</span><span>Application</span></div>
          <div className={`status-value ${dataReady ? "healthy" : state === "error" ? "unhealthy" : "pending"}`}>
            <span className="status-orb" />{dataReady ? "Operational" : state === "error" ? "Unavailable" : "Checking"}
          </div>
          <p className="card-detail">FastAPI · SQLite · migration {health?.database.migration_version ?? "—"}</p>
        </article>

        <article className="status-card">
          <div className="card-heading"><span className="card-icon blue">▤</span><span>FRED source</span></div>
          <div className="metric-value source-value"><span className={`source-dot ${fred?.status ?? "pending"}`} />{fred?.status ?? "Checking"}</div>
          <p className="card-detail">FRED {fred?.status ?? "Checking"} · CoinPaprika {coinpaprika?.status ?? "Checking"} · SEC {sec?.status ?? "Checking"}</p>
        </article>

        <article className="status-card">
          <div className="card-heading"><span className="card-icon violet">◷</span><span>Fresh indicators</span></div>
          <div className="metric-value">{dataHealth?.fresh_indicators ?? 0}<span className="metric-total">/ {dataHealth?.active_indicators ?? 20}</span></div>
          <p className="card-detail">{dataHealth?.stale_indicators ?? 0} stale · {dataHealth?.unavailable_indicators ?? 0} unavailable</p>
        </article>
      </section>

      {sec?.status === "unconfigured" && state === "ready" && (
        <section className="setup-note" role="status">
          <div className="setup-icon">i</div>
          <div><strong>SEC company filings are not connected yet</strong>
            <p>Set <code>SEC_USER_AGENT</code> locally or the <code>sec_user_agent</code> app option to a descriptive app name and contact email. SEC requests stay disabled until this is set.</p>
          </div>
        </section>
      )}

      <section className="scenario-section" aria-labelledby="scenario-title">
        <div className="scenario-heading">
          <div>
            <div className="eyebrow">EVIDENCE INDICES · METHODOLOGY 0.4</div>
            <h2 id="scenario-title">Scenario signals</h2>
            <p>Scores describe the strength of supporting evidence. They are not probabilities. Missing components lower coverage and can leave an index unavailable.</p>
          </div>
        </div>
        <div className="scenario-grid">
          {scenarios.map((scenario) => {
            const missing = scenario.components.filter((component) => component.status !== "available");
            return (
              <article className={`scenario-card ${scenario.status}`} key={scenario.id}>
                <div className="scenario-card-heading">
                  <h3>{scenario.name}</h3>
                  <span className={`scenario-badge ${scenario.status}`}>{scenario.status.replaceAll("_", " ")}</span>
                </div>
                <div className="scenario-value-row">
                  <strong>{scenario.score === null ? "—" : scenario.score.toFixed(1)}</strong>
                  <span>{scenario.classification ?? (scenario.status === "unavailable" ? "Insufficient inputs" : "Waiting for data")}</span>
                </div>
                <div className="scenario-metrics">
                  <span>Coverage <b>{Math.round(scenario.coverage * 100)}%</b></span>
                  <span>Confidence <b>{Math.round(scenario.confidence * 100)}%</b></span>
                </div>
                <div className="scenario-trends" aria-label="Score changes">
                  {["7d", "30d", "90d"].map((period) => {
                    const change = scenario.score_changes?.[period];
                    return <span key={period}>{period} <b className={change ? (change.value > 0 ? "trend-up" : change.value < 0 ? "trend-down" : "") : ""}>{change ? `${change.value > 0 ? "+" : ""}${change.value.toFixed(1)}` : "—"}</b></span>;
                  })}
                </div>
                <Sparkline points={scenario.history ?? []} />
                {scenario.persistence && <div className={`persistence-note ${scenario.persistence.persistent ? "active" : ""}`}>
                  {scenario.persistence.persistent ? "Persistent signal" : "Persistence not established"}
                  <span>{scenario.persistence.days_above_threshold}/{scenario.persistence.lookback_days} days ≥ {scenario.persistence.threshold}</span>
                </div>}
                <div className="scenario-track" aria-label={`${Math.round(scenario.coverage * 100)}% component coverage`}>
                  <span style={{ width: `${Math.round(scenario.coverage * 100)}%` }} />
                </div>
                {scenario.top_contributors.length > 0 ? (
                  <div className="scenario-contributors">
                    <span className="scenario-caption">TOP CONTRIBUTORS</span>
                    {scenario.top_contributors.map((item) => <span key={item.name} title={item.inputs.map((input) => `${input.source ?? "Unavailable"}: ${input.value === null ? "no data" : input.value.toFixed(1)}`).join(" · ")}>{item.name} <b>+{item.contribution.toFixed(1)}</b></span>)}
                  </div>
                ) : missing.length > 0 ? (
                  <div className="scenario-contributors">
                    <span className="scenario-caption">NOT COVERED</span>
                    {missing.slice(0, 2).map((item) => <span key={item.id}>{item.name}</span>)}
                    {missing.length > 2 && <span>+{missing.length - 2} more components</span>}
                  </div>
                ) : null}
              </article>
            );
          })}
          {scenarios.length === 0 && state === "ready" && <div className="empty-state">Scenario scores have not been calculated yet.</div>}
        </div>
      </section>

      {!fred?.configured && state === "ready" && (
        <section className="setup-note" role="status">
          <div className="setup-icon">i</div>
          <div>
            <strong>Connect FRED to load the first market history</strong>
            <p>Add your free FRED API key as <code>FRED_API_KEY</code> in the local <code>.env</code> file or Home Assistant app options, then restart the app. The key is never returned by the API.</p>
          </div>
        </section>
      )}

      <section className="data-panel" aria-labelledby="events-title">
        <div className="panel-heading">
          <div>
            <div className="eyebrow">SEC EDGAR · FILING EVIDENCE</div>
            <h2 id="events-title">Latest risk events</h2>
            <p>Numeric CAPEX outlook extracted from recent 8-K/6-K filings. Up to two filings per company are scanned each collection cycle; each event links to its SEC filing, and historical spending is not treated as guidance.</p>
          </div>
        </div>
        {events.length ? <div className="event-list">{events.slice(0, 8).map((item) => {
          const lower = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(item.guidance_lower);
          const upper = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(item.guidance_upper);
          const revision = item.revision;
          const change = (value: number | null) => value === null ? "n/a" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
          return <article className="event-card" key={item.id}>
            <div className="event-meta"><span>{item.company_id.toUpperCase()}</span><span>{item.guidance_period ?? "Period unspecified"}</span><time>{formatDate(item.published_at)}</time></div>
            <h3>{item.title}</h3>
            <p className="event-guidance">{lower === upper ? lower : `${lower}–${upper}`} expected CAPEX</p>
            {revision ? <p className="event-revision">Change vs {formatDate(revision.previous_published_at)} · lower {change(revision.lower_change_percent)} · upper {change(revision.upper_change_percent)} · <a href={revision.previous_source_url} target="_blank" rel="noreferrer">prior guidance ↗</a></p> : <p className="event-revision">First tracked disclosure for this guidance period</p>}
            <blockquote>{item.source_quote}</blockquote>
            <a className="financial-source-link" href={item.source_url} target="_blank" rel="noreferrer">SEC exhibit · accession {item.source_accession} ↗</a>
          </article>;
        })}</div> : <div className="empty-state">No explicit numeric CAPEX guidance found in the scanned 8-K/6-K filings.</div>}
      </section>

      <section className="data-panel company-panel" aria-labelledby="companies-title">
        <div className="panel-heading">
          <div>
            <div className="eyebrow">SEC EDGAR · 17 PUBLIC FILERS</div>
            <h2 id="companies-title">Company financial monitor</h2>
            <p>Reported fundamentals with filing dates and source links. Cloud revenue and RPO are broad proxies, not AI-only measures; largest-customer revenue share appears only when explicitly disclosed. A dash means unavailable, not zero. Company risk scores are experimental heuristics.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Company</th><th>Group</th><th>Revenue</th><th>Cloud / RPO</th><th>Largest customer / revenue</th><th>Free cash flow</th><th>Risk score</th><th>Reporting period</th><th>Trend</th><th>Data</th></tr></thead>
            <tbody>{companies.map((company) => {
              const revenue = company.metrics.revenue;
              const fcf = company.metrics.free_cash_flow;
              const cloudEntry = Object.entries(company.metrics).find(([metric]) => metric.startsWith("cloud_revenue_"));
              const cloud = cloudEntry?.[1];
              const cloudNames: Record<string, string> = { cloud_revenue_aws: "AWS", cloud_revenue_azure: "Azure", cloud_revenue_google: "Google Cloud", cloud_revenue_oracle: "Oracle Cloud" };
              const rpo = company.metrics.rpo_reported;
              const customerConcentration = company.metrics.customer_revenue_concentration_percent;
              return <tr key={company.id}>
                <td><span className="indicator-name">{company.name}</span><span className="series-id">{company.ticker} · CIK {company.cik}</span></td>
                <td className="category-cell">{company.group.replaceAll("_", " ")}</td>
                <td className="value-cell">{revenue ? <a className="financial-source-link" href={revenue.source_url} target="_blank" rel="noreferrer">{formatValue(revenue.value, "usd")} ↗</a> : "—"}</td>
                <td className="financial-trend-cell">{cloud ? <div><a className="financial-source-link" href={cloud.source_url} target="_blank" rel="noreferrer">{cloudNames[cloudEntry?.[0] ?? ""] ?? "Cloud"}: {formatValue(cloud.value, "usd")} ↗</a><YearOverYearLine metric={cloud} /></div> : null}{rpo ? <div><a className="financial-source-link" href={rpo.source_url} target="_blank" rel="noreferrer">RPO: {formatValue(rpo.value, "usd")} ↗</a><YearOverYearLine metric={rpo} /></div> : null}{!cloud && !rpo ? "—" : null}</td>
                <td className="financial-trend-cell">{customerConcentration ? <div><a className="financial-source-link" href={customerConcentration.source_url} target="_blank" rel="noreferrer">{formatValue(customerConcentration.value, "percent")} ↗</a><YearOverYearLine metric={customerConcentration} /></div> : "—"}</td>
                <td className="value-cell">{fcf ? <a className="financial-source-link" href={fcf.source_url} target="_blank" rel="noreferrer">{formatValue(fcf.value, "usd")} ↗</a> : "—"}</td>
                <td className="value-cell" title={company.score?.components.map((item) => `${item.component_id}: ${item.value.toFixed(1)} (inputs: ${item.source_metrics.join(", ")})`).join(" · ")}>{company.score ? `${company.score.score.toFixed(1)} · ${Math.round(company.score.coverage * 100)}% coverage` : "—"}</td>
                <td className="date-cell">{company.score?.reporting_period ?? revenue?.reporting_period ?? "—"}</td>
                <td><Sparkline points={company.history ?? []} /></td>
                <td><span className={`data-badge ${company.status === "available" ? "fresh" : "unavailable"}`}>{company.status === "available" ? `${Math.round(company.data_coverage * 100)}% coverage` : "awaiting SEC"}</span></td>
              </tr>;
            })}
            {companies.length === 0 && <tr><td colSpan={10} className="empty-state">Company universe unavailable.</td></tr>}</tbody>
          </table>
        </div>
      </section>

      <section className="data-panel" aria-labelledby="indicators-title">
        <div className="panel-heading">
          <div>
            <div className="eyebrow">OBSERVATIONS</div>
            <h2 id="indicators-title">Market and macro indicators</h2>
            <p>Sources: FRED and CoinPaprika · rolling risk signals are normalized to 0–100 after enough history is available</p>
          </div>
          <button className="collect-button" disabled={!collectionAvailable || collecting || state !== "ready"} onClick={() => void collectNow()} type="button">
            {collecting ? "Collecting history…" : "Collect history"}
            <span aria-hidden="true">↻</span>
          </button>
        </div>

        {collectionMessage && <div className="collection-message success" role="status">{collectionMessage}</div>}
        {collectionError && <div className="collection-message error" role="alert">{collectionError}</div>}
        {state === "error" && <div className="collection-message error" role="alert">The indicator API could not be reached. Check that the local service is running.</div>}

        <div className="coverage-row">
          <span>Data coverage</span>
          <div className="coverage-track" aria-label={`${coverage}% fresh indicator coverage`}><span style={{ width: `${coverage}%` }} /></div>
          <strong>{coverage}%</strong>
          <span className="coverage-update">Collected {formatDate(dataHealth?.last_successful_collection)} · normalized {formatDate(dataHealth?.last_scoring_execution)}</span>
        </div>

        <div className="table-wrap">
          <table>
            <thead><tr><th>Indicator</th><th>Category</th><th>Latest</th><th>Risk signal</th><th>Observation date</th><th>Frequency</th><th>Status</th></tr></thead>
            <tbody>
              {indicators.map((indicator) => (
                <tr key={indicator.id}>
                  <td><span className="indicator-name">{indicator.name}</span><span className="series-id" title={indicator.availability_note ?? undefined}>{indicator.id.endsWith("_usd") ? "CoinPaprika" : "FRED"} · {indicator.source_id}{indicator.availability_note ? " · retired by source" : ""}</span></td>
                  <td className="category-cell">{indicator.category.replaceAll("_", " ")}</td>
                  <td className="value-cell">{formatValue(indicator.latest_value, indicator.unit)}</td>
                  <td className="value-cell" title={indicator.normalized_confidence === null ? indicator.normalized_data_quality ?? "Not calculated" : `Confidence ${Math.round(indicator.normalized_confidence * 100)}%`}>
                    {indicator.latest_normalized_value === null || indicator.latest_normalized_value === undefined
                      ? indicator.normalized_data_quality === "insufficient_history" ? "Building history" : "—"
                      : `${indicator.latest_normalized_value.toFixed(0)} / 100`}
                  </td>
                  <td className="date-cell">{formatDate(indicator.latest_observation_date)}</td>
                  <td className="frequency-cell">{indicator.frequency}</td>
                  <td><span className={`data-badge ${indicator.data_status}`} title={indicator.availability_note ?? undefined}>{indicator.data_status}</span></td>
                </tr>
              ))}
              {indicators.length === 0 && state === "ready" && <tr><td colSpan={7} className="empty-state">No indicator definitions are available.</td></tr>}
              {state === "loading" && <tr><td colSpan={7} className="empty-state">Loading indicator catalog…</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <footer className="footer">
        <span>AI BUBBLE RISK MONITOR <span className="footer-separator">/</span> LOCAL-FIRST</span>
        <div className="footer-actions">
          <a className="api-link" href="https://coinpaprika.com/" target="_blank" rel="noreferrer">Powered by CoinPaprika <span aria-hidden="true">↗</span></a>
          <a className="api-link" href="./docs">API documentation <span aria-hidden="true">↗</span></a>
          <button className="refresh-button" onClick={() => void refresh()} type="button">Refresh data <span aria-hidden="true">↻</span></button>
        </div>
      </footer>
    </main>
  );
}

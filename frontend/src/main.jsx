import React, { useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import "./style.css";

const TIMEFRAMES = ["1M", "1W", "1D", "4H", "1H"];
const TF_LABELS = { "1M": "Monthly", "1W": "Weekly", "1D": "Daily", "4H": "4 hour", "1H": "1 hour" };

function splitCsvLine(line) {
  const values = [];
  let value = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"' && line[index + 1] === '"' && quoted) {
      value += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      values.push(value.trim());
      value = "";
    } else {
      value += char;
    }
  }
  values.push(value.trim());
  return values;
}

function parseCsv(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2) throw new Error("CSV needs a header and at least one OHLC row.");
  const headers = splitCsvLine(lines[0]).map((header) => header.toLowerCase().replace(/[ _-]/g, ""));
  const findColumn = (...names) => names.map((name) => headers.indexOf(name)).find((index) => index >= 0);
  const columns = {
    timestamp: findColumn("timestamp", "time", "date", "datetime"),
    open: findColumn("open", "o"),
    high: findColumn("high", "h"),
    low: findColumn("low", "l"),
    close: findColumn("close", "c"),
    volume: findColumn("volume", "vol", "v"),
  };
  const missing = ["timestamp", "open", "high", "low", "close"].filter((key) => columns[key] === undefined);
  if (missing.length) throw new Error(`Missing CSV columns: ${missing.join(", ")}`);

  return lines.slice(1).map((line, rowIndex) => {
    const values = splitCsvLine(line);
    const row = { timestamp: values[columns.timestamp] };
    for (const key of ["open", "high", "low", "close"]) {
      row[key] = Number(values[columns[key]]);
      if (!Number.isFinite(row[key])) throw new Error(`Invalid ${key} at CSV row ${rowIndex + 2}.`);
    }
    if (columns.volume !== undefined && values[columns.volume] !== "") {
      row.volume = Number(values[columns.volume]);
      if (!Number.isFinite(row.volume)) throw new Error(`Invalid volume at CSV row ${rowIndex + 2}.`);
    }
    if (!row.timestamp) throw new Error(`Missing timestamp at CSV row ${rowIndex + 2}.`);
    return row;
  });
}

function formatPrice(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 8 });
}

function statusClass(value = "") {
  return value.toLowerCase().replaceAll(" ", "-").replaceAll("/", "-");
}

function UploadCard({ timeframe, file, onChange }) {
  return (
    <label className={`upload-card ${file ? "has-file" : ""}`}>
      <input type="file" accept=".csv,text/csv" onChange={(event) => onChange(event.target.files?.[0] || null)} />
      <span className="upload-topline"><strong>{timeframe}</strong><span>{TF_LABELS[timeframe]}</span></span>
      <span className="upload-file">{file ? file.name : "Choose OHLC CSV"}</span>
      <span className="upload-hint">timestamp, open, high, low, close · volume optional</span>
    </label>
  );
}

function ZoneCard({ zone, rank }) {
  return (
    <article className={`zone-card ${zone.direction === "BULLISH" ? "zone-demand" : "zone-supply"}`}>
      <div className="zone-heading">
        <div>
          <span className={`tag ${zone.direction === "BULLISH" ? "tag-green" : "tag-red"}`}>{zone.type}</span>
          <span className="zone-timeframe">{zone.timeframe} · #{rank}</span>
        </div>
        <div className="score"><strong>{zone.score}</strong><span>/100</span></div>
      </div>
      <div className="zone-range">{formatPrice(zone.lower)} <span>—</span> {formatPrice(zone.upper)}</div>
      <div className="zone-meta">
        <span className={`tag tag-neutral ${statusClass(zone.status)}`}>{zone.status}</span>
        {zone.risk_state === "AT RISK" && <span className="tag tag-amber">AT RISK</span>}
        {zone.is_order_block && <span className="tag tag-violet">ORDER BLOCK</span>}
        {zone.has_fvg && <span className="tag tag-blue">FVG</span>}
        {zone.parent_zone && <span className="tag tag-violet">NESTED</span>}
      </div>
      <div className="score-track"><span style={{ width: `${zone.score}%` }} /></div>
      <p className="zone-foot">{zone.source.replaceAll("_", " ")} · {zone.retests} retest{zone.retests === 1 ? "" : "s"} · {zone.score_label}</p>
      <p className="zone-foot">Created {zone.created_at} · invalidation {formatPrice(zone.invalidation)}</p>
    </article>
  );
}

function TimeframePanel({ timeframe, data }) {
  const zones = [...(data.demand_zones || []), ...(data.supply_zones || [])].slice(0, 6);
  return (
    <details className="timeframe-panel" open={data.status === "OK"}>
      <summary>
        <span className="tf-name"><b>{timeframe}</b><span>{TF_LABELS[timeframe]}</span></span>
        <span className="tf-summary">
          {data.status === "OK" ? <><span className={`bias-dot ${statusClass(data.bias)}`} />{data.bias} · {data.bars} bars</> : "INSUFFICIENT DATA"}
        </span>
        <span className="chevron">⌄</span>
      </summary>
      {data.status !== "OK" ? (
        <p className="empty-note">Provide at least 5 OHLC rows for this timeframe. No candles or levels are synthesized.</p>
      ) : (
        <div className="tf-content">
          <div className="tf-facts">
            <div><span>Data range</span><strong>{data.data_range[0]} → {data.data_range[1]}</strong></div>
            <div><span>Last close</span><strong>{formatPrice(data.current_price)}</strong></div>
            <div><span>Structure</span><strong>{data.structure}</strong></div>
            <div><span>Pivots detected</span><strong>{data.pivots.length}</strong></div>
          </div>
          {data.latest_event && <div className="event-line"><span className="event-mark">↗</span><span><b>{data.latest_event.type}</b> {data.latest_event.direction} close through {formatPrice(data.latest_event.level)} · {data.latest_event.timestamp}</span></div>}
          {zones.length ? <div className="mini-zones">{zones.map((zone) => <div className="mini-zone" key={zone.id}><b>{zone.type}</b><span>{formatPrice(zone.lower)}–{formatPrice(zone.upper)}</span><span>{zone.status}</span></div>)}</div> : <p className="empty-note">No qualified supply/demand candidates found on this timeframe.</p>}
        </div>
      )}
    </details>
  );
}

function App() {
  const [instrument, setInstrument] = useState("");
  const [files, setFiles] = useState({});
  const [result, setResult] = useState(null);
  const [liveMarket, setLiveMarket] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyTask, setBusyTask] = useState("");
  const uploadedCount = Object.values(files).filter(Boolean).length;
  const rankedZones = useMemo(() => result?.ranked_zones || [], [result]);

  async function analyze(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setLiveMarket(null);
    setBacktest(null);
    if (!uploadedCount) {
      setError("Choose at least one timeframe CSV to analyze.");
      return;
    }
    setBusy(true);
    setBusyTask("csv");
    try {
      const timeframes = {};
      for (const timeframe of TIMEFRAMES) {
        if (files[timeframe]) timeframes[timeframe] = parseCsv(await files[timeframe].text());
      }
      const response = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instrument: instrument.trim(), timeframes }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Analysis request failed.");
      setResult(body);
    } catch (requestError) {
      setError(requestError.message || "Could not analyze the supplied data.");
    } finally {
      setBusy(false);
      setBusyTask("");
    }
  }

  async function fetchLiveAnalysis() {
    setError("");
    setBusy(true);
    setBusyTask("live");
    try {
      const symbol = instrument.trim() || "SOL/USDT";
      const response = await fetch(`/api/live-analysis?symbol=${encodeURIComponent(symbol)}&limit=300`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Could not fetch live market data.");
      setResult(body.analysis);
      setLiveMarket(body.live_market);
      setBacktest(null);
    } catch (requestError) {
      setError(requestError.message || "Could not fetch live market data.");
    } finally {
      setBusy(false);
      setBusyTask("");
    }
  }

  async function runHistoricalBacktest() {
    setError("");
    setBusy(true);
    setBusyTask("backtest");
    try {
      const symbol = instrument.trim() || "SOL/USDT";
      const response = await fetch(`/api/backtest?symbol=${encodeURIComponent(symbol)}&timeframe=1H&limit=1000&risk_reward=2&fee_bps=10&slippage_bps=5`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Historical backtest failed.");
      setBacktest(body);
    } catch (requestError) {
      setError(requestError.message || "Historical backtest failed.");
    } finally {
      setBusy(false);
      setBusyTask("");
    }
  }

  async function runLiveAndBacktest() {
    setError("");
    setBusy(true);
    setBusyTask("both");
    try {
      const symbol = encodeURIComponent(instrument.trim() || "SOL/USDT");
      const [liveResponse, backtestResponse] = await Promise.all([
        fetch(`/api/live-analysis?symbol=${symbol}&limit=300`),
        fetch(`/api/backtest?symbol=${symbol}&timeframe=1H&limit=1000&risk_reward=2&fee_bps=10&slippage_bps=5`),
      ]);
      const [liveBody, backtestBody] = await Promise.all([liveResponse.json(), backtestResponse.json()]);
      if (!liveResponse.ok) throw new Error(liveBody.detail || "Could not fetch live market data.");
      if (!backtestResponse.ok) throw new Error(backtestBody.detail || "Historical backtest failed.");
      setResult(liveBody.analysis);
      setLiveMarket(liveBody.live_market);
      setBacktest(backtestBody);
    } catch (requestError) {
      setError(requestError.message || "Live analysis/backtest failed.");
    } finally {
      setBusy(false);
      setBusyTask("");
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Sentinel home"><span className="brand-mark">S</span><span>sentinel<span className="brand-light"> / research</span></span></a>
        <div className="topbar-right"><span className="mode-pill"><i /> {liveMarket ? "LIVE DATA · READ ONLY" : "RESEARCH MODE"}</span><a className="top-link" href="/docs" target="_blank" rel="noreferrer">API docs ↗</a></div>
      </header>

      <section className="hero" id="top">
        <div className="eyebrow"><span /> PRICE ACTION INTELLIGENCE</div>
        <h1>Map the structure.<br /><em>Respect the evidence.</em></h1>
        <p className="hero-copy">A deterministic multi-timeframe read of supply, demand, order blocks, imbalances, and liquidity—built only from the OHLC you provide.</p>
        <div className="hero-note"><span>↳</span> Public market-data reads only. No exchange orders. No generated candles.</div>
      </section>

      <form className="analysis-form panel" onSubmit={analyze}>
        <div className="section-heading"><div><span className="section-kicker">01 / INPUT</span><h2>Bring your market data</h2></div><span className="muted-count">{uploadedCount} / 5 timeframes loaded</span></div>
        <div className="form-row"><label className="field-label" htmlFor="instrument">Instrument <span>optional</span></label><input id="instrument" className="text-input" value={instrument} onChange={(event) => setInstrument(event.target.value)} placeholder="e.g. SOL/USDT" autoComplete="off" /></div>
        <div className="upload-grid">{TIMEFRAMES.map((timeframe) => <UploadCard key={timeframe} timeframe={timeframe} file={files[timeframe]} onChange={(file) => setFiles((current) => ({ ...current, [timeframe]: file }))} />)}</div>
        <div className="form-bottom"><p>CSV header: <code>timestamp,open,high,low,close</code> · Volume optional. Live/backtest default: SOL/USDT.</p><div className="action-buttons"><button className="primary-button" type="submit" disabled={busy}>{busy && busyTask === "csv" ? <><span className="spinner" /> Analyzing</> : <>Analyze CSV <span>→</span></>}</button><button className="secondary-button" type="button" disabled={busy} onClick={fetchLiveAnalysis}>{busy && busyTask === "live" ? "Fetching…" : "Live snapshot"}</button><button className="secondary-button" type="button" disabled={busy} onClick={runHistoricalBacktest}>{busy && busyTask === "backtest" ? "Testing…" : "Historical 1H backtest"}</button><button className="combined-button" type="button" disabled={busy} onClick={runLiveAndBacktest}>{busy && busyTask === "both" ? <><span className="spinner" /> Running</> : "Run live + backtest"}</button></div></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </form>

      {result && <section className="results" aria-live="polite">
        <div className="section-heading results-heading"><div><span className="section-kicker">02 / MARKET MAP</span><h2>{result.instrument || "Unspecified instrument"}<span className="result-date">{result.analysis_date || "Date unavailable"}</span></h2></div><button className="text-button" onClick={() => setResult(null)} type="button">Clear results ×</button></div>
        <div className="summary-grid">
          <div className="summary-card"><span>OVERALL BIAS</span><strong className={statusClass(result.market_bias.overall)}>{result.market_bias.overall}</strong><small>{result.market_bias.alignment} alignment</small></div>
          <div className="summary-card"><span>{liveMarket ? "LIVE TICKER" : "LATEST CANDLE"}</span><strong>{formatPrice(liveMarket?.live_price ?? result.current_price)}</strong><small>{liveMarket ? `Binance Spot · ${liveMarket.as_of_utc}` : `Completed ${result.price_timeframe || "—"} candle`}</small></div>
          <div className="summary-card"><span>DATA COVERAGE</span><strong>{result.available_timeframes.length}<small> / 5</small></strong><small>{result.available_timeframes.join(" · ") || "No valid timeframes"}</small></div>
          <div className="summary-card"><span>QUALIFIED ZONES</span><strong>{rankedZones.length}</strong><small>Ranked by rule-based confluence</small></div>
        </div>
        <div className="callout"><span className="callout-icon">i</span><div><b>{result.final_summary}</b><p>{result.limitations?.[4]}</p></div></div>

        {result.market_structure && <section className="panel structure-panel"><div className="section-heading compact"><div><span className="section-kicker">STRUCTURE + ZONES / MERGED VIEW</span><h2>{result.market_structure.state} · {result.market_structure.primary_bias}</h2></div><span className="muted-count">Context: {result.market_structure.source_timeframe || "—"}</span></div><div className="structure-grid">{TIMEFRAMES.map((timeframe) => { const row = result.market_structure.timeframes[timeframe]; return <div className="structure-cell" key={timeframe}><b>{timeframe}</b><span>{row.structure}</span><strong>{row.bias}</strong><small>{row.latest_event ? `${row.latest_event.type} ${row.latest_event.direction} · ${formatPrice(row.latest_event.level)}` : "No recent structural event"}</small><small>Protected low {formatPrice(row.protected_low)} · high {formatPrice(row.protected_high)}</small></div>; })}</div><div className="scenario-strip"><div><span>PRIMARY STRUCTURAL SCENARIO</span><p>{result.market_structure.primary_scenario}</p></div><div><span>ALTERNATIVE</span><p>{result.market_structure.alternative_scenario}</p></div></div></section>}

        <div className="content-grid">
          <section className="panel zones-panel"><div className="section-heading compact"><div><span className="section-kicker">03 / POINTS OF INTEREST</span><h2>Ranked zones</h2></div><span className="muted-count">Score ≠ probability</span></div>
            {rankedZones.length ? <div className="zone-list">{rankedZones.slice(0, 8).map((zone, index) => <ZoneCard key={zone.id} zone={zone} rank={index + 1} />)}</div> : <p className="empty-note large">No qualified zones in the supplied candles. A strong displacement and structural break (or a 2× ATR move) are required.</p>}
          </section>
          <aside className="panel poi-panel"><span className="section-kicker">04 / BEST ALIGNED AREAS</span><h2>Primary POIs</h2>
            {[ ["LONG POI", result.primary_long_poi, "long"], ["SHORT POI", result.primary_short_poi, "short"] ].map(([title, poi, side]) => <div className={`poi-card ${side}`} key={title}><div className="poi-title"><span>{title}</span><b>{poi ? `${poi.score}/100` : "—"}</b></div>{poi ? <><strong className="poi-range">{formatPrice(poi.lower)} – {formatPrice(poi.upper)}</strong><div className="poi-sub">{poi.timeframe} · {poi.freshness}</div><div className="poi-tags">{poi.confluence.length ? poi.confluence.map((item) => <span key={item}>{item.replaceAll("_", " ")}</span>) : <span>Standalone zone</span>}</div><div className="poi-invalidation">Invalidation <b>{formatPrice(poi.invalidation)}</b></div><p>Zone touch alone is not entry confirmation.</p></> : <p className="empty-note">No valid zone found in the uploaded data.</p>}</div>)}
            <div className="liquidity-summary"><h3>Liquidity pools</h3><div><span>Buy-side</span><b>{result.liquidity.buy_side.length ? result.liquidity.buy_side.slice(0, 3).map((item) => formatPrice(item.level)).join(" · ") : "None detected"}</b></div><div><span>Sell-side</span><b>{result.liquidity.sell_side.length ? result.liquidity.sell_side.slice(0, 3).map((item) => formatPrice(item.level)).join(" · ") : "None detected"}</b></div><div><span>Recent sweeps</span><b>{result.liquidity.recent_sweeps.length}</b></div></div>
          </aside>
        </div>

        <section className="panel timeframe-section"><div className="section-heading compact"><div><span className="section-kicker">05 / TOP-DOWN REVIEW</span><h2>Timeframe breakdown</h2></div><span className="muted-count">1M → 1W → 1D → 4H → 1H</span></div>{TIMEFRAMES.map((timeframe) => <TimeframePanel key={timeframe} timeframe={timeframe} data={result.timeframes[timeframe]} />)}</section>
        {backtest && <section className="panel backtest-panel"><div className="section-heading compact"><div><span className="section-kicker">06 / HISTORICAL SIMULATION</span><h2>{backtest.symbol} · {backtest.timeframe}</h2></div><span className="muted-count">{backtest.data_range[0]} → {backtest.data_range[1]}</span></div><p className="backtest-rule">{backtest.strategy.signal} Entry: next bar open · Target: {backtest.strategy.target} · Fees: {backtest.strategy.fee_bps_per_side} bps/side · Slippage: {backtest.strategy.slippage_bps_per_side} bps/side.</p><div className="summary-grid backtest-stats"><div className="summary-card"><span>TRADES</span><strong>{backtest.summary.trades}</strong><small>{backtest.summary.wins} wins · {backtest.summary.losses} losses</small></div><div className="summary-card"><span>WIN RATE</span><strong>{backtest.summary.win_rate_pct}%</strong><small>After modeled costs</small></div><div className="summary-card"><span>EXPECTANCY</span><strong>{backtest.summary.expectancy_net_r}R</strong><small>Per trade, net of fees/slippage</small></div><div className="summary-card"><span>NET RETURN</span><strong>{backtest.summary.total_net_return_pct_constant_notional}%</strong><small>Summed, constant-notional trades</small></div></div>{backtest.trades.length ? <div className="trade-table-wrap"><table className="trade-table"><thead><tr><th>Side</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Net %</th><th>Net R</th></tr></thead><tbody>{backtest.trades.slice(-12).reverse().map((trade, index) => <tr key={`${trade.signal_time}-${index}`}><td className={trade.direction === "BULLISH" ? "bull-text" : "bear-text"}>{trade.direction}</td><td>{trade.signal_time}</td><td>{formatPrice(trade.entry_price)}</td><td>{formatPrice(trade.exit_price)}</td><td>{trade.exit_reason}</td><td>{trade.net_return_pct}%</td><td>{trade.net_r_multiple}R</td></tr>)}</tbody></table></div> : <p className="empty-note large">No qualifying setups in this historical window. No trades were forced to fill the report.</p>}<div className="backtest-warning"><b>Historical simulation only.</b> It uses completed {backtest.timeframe} candles, configured fee/slippage assumptions, and stop-first handling for ambiguous candles. It does not backtest on a live quote, place orders, or model funding/partial fills. A live ticker is shown separately.</div></section>}
        <details className="json-details"><summary>View machine-readable result <span>JSON</span></summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
      </section>}

      {backtest && !result && <section className="results"><section className="panel backtest-panel"><div className="section-heading compact"><div><span className="section-kicker">HISTORICAL SIMULATION</span><h2>{backtest.symbol} · {backtest.timeframe}</h2></div><span className="muted-count">{backtest.data_range[0]} → {backtest.data_range[1]}</span></div><div className="summary-grid backtest-stats"><div className="summary-card"><span>TRADES</span><strong>{backtest.summary.trades}</strong><small>{backtest.summary.wins} wins · {backtest.summary.losses} losses</small></div><div className="summary-card"><span>WIN RATE</span><strong>{backtest.summary.win_rate_pct}%</strong><small>After modeled costs</small></div><div className="summary-card"><span>EXPECTANCY</span><strong>{backtest.summary.expectancy_net_r}R</strong><small>Per trade, net of costs</small></div><div className="summary-card"><span>NET RETURN</span><strong>{backtest.summary.total_net_return_pct_constant_notional}%</strong><small>Summed constant-notional</small></div></div>{backtest.trades.length ? <div className="trade-table-wrap"><table className="trade-table"><thead><tr><th>Side</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Reason</th><th>Net %</th><th>Net R</th></tr></thead><tbody>{backtest.trades.slice(-12).reverse().map((trade, index) => <tr key={`${trade.signal_time}-${index}`}><td className={trade.direction === "BULLISH" ? "bull-text" : "bear-text"}>{trade.direction}</td><td>{trade.signal_time}</td><td>{formatPrice(trade.entry_price)}</td><td>{formatPrice(trade.exit_price)}</td><td>{trade.exit_reason}</td><td>{trade.net_return_pct}%</td><td>{trade.net_r_multiple}R</td></tr>)}</tbody></table></div> : <p className="empty-note large">No qualifying setups in this historical window. No trades were forced.</p>}<div className="backtest-warning"><b>Historical simulation only.</b> This uses completed candles, not a live quote, and does not place orders.</div></section></section>}

      <footer className="footer"><span>PROJECT SENTINEL</span><span>Structural research only · not financial advice · no orders are placed</span></footer>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);

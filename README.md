# Project Sentinel — Market Structure Research

A local research dashboard for deterministic multi-timeframe analysis of user-supplied OHLC CSV files or read-only Binance Spot public market data. It reports pivots/structure, BOS and CHoCH candidates, liquidity pools and sweeps, supply/demand and order-block candidates, three-candle FVGs, freshness/mitigation, nested zones, and rule-based confluence scores.

It does not use exchange credentials, create missing candles/timeframes, or place orders. Public Binance data is read-only. A zone/POI is not an entry signal. The score is a confluence heuristic, not a probability or trade recommendation.

## Run locally

### Backend (PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If the backend virtual environment already exists, activate it rather than recreating it.

- Health: `http://127.0.0.1:8000/health`
- API docs: `http://127.0.0.1:8000/docs`

### Frontend (second terminal)

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` and `/health` to the local backend.

The dashboard supports a **Live snapshot** (current ticker plus latest completed candles), a **Historical 1H backtest**, and a combined action. The default symbol is `SOL/USDT`; set another Binance Spot pair in the Instrument field.

## Open it on your phone without VS Code

1. Double-click `start_sentinel.bat` on the PC. It starts only the local servers that are not already running; VS Code is not needed.
2. Keep the PC on and connected to Wi-Fi. In PowerShell, run `ipconfig` and note the PC's IPv4 address under its Wi-Fi or Ethernet adapter (for example, `192.168.1.7`).
3. Connect the phone to that same Wi-Fi and visit `http://<PC-IP>:5173` (for example, `http://192.168.1.7:5173`).
4. If Windows Firewall asks, allow Node/Vite on **Private networks only**. Do not configure router port forwarding; this development server is intended for your trusted local network.

This local option requires the PC to stay awake and both Sentinel windows to keep running. To access the page when the PC is off or from outside your home network, deploy it to a cloud host and use that host's HTTPS URL instead.

## Deploy to Render

Render deploys from a connected Git repository; it does not deploy directly from a local folder or ZIP. Put the contents of this `project-sentinel` folder at the root of a GitHub repository and push it, including `Dockerfile`, `render.yaml`, and `backend/requirements-render.txt`.

1. Create/push the repository to GitHub. Do not include `.venv`, `node_modules`, API secrets, or local data files.
2. In Render, choose **New → Blueprint**, connect the repository, and apply the `render.yaml` plan. Alternatively, create a **Web Service** for the repository and select the **Docker** runtime.
3. Wait for the Docker build and deploy to finish. Open the generated `https://<service-name>.onrender.com` URL; `/health` should return `{"status":"ok","service":"market-analysis"}`.
4. The frontend and API share one origin, so no API URL or secret environment variable is required. Live ticker calls use Binance's public read-only API.

This service does not persist uploaded CSVs or trading data. When you upload a CSV, its rows are sent from your browser to your Render service for analysis. Treat uploaded data as shared with that hosting provider. Free instances may sleep when idle and take time to wake; check the current Render plan limits. Do not enable live order execution or expose credentials in this research app.

### Local Docker check (optional)

From this folder run `docker build -t project-sentinel .`, then `docker run --rm -p 10000:10000 project-sentinel`. Open `http://localhost:10000` and check `http://localhost:10000/health`.

## Backtest assumptions

The default simulation retrieves up to 1,000 completed 1H candles from Binance and requires this causal sequence: opposing-side liquidity sweep → same-direction displacement/structure break from an OB/FVG candidate → directional candle-close rejection on that zone's retest. It enters at the next candle open, uses the zone's distal edge as stop, targets 2R, charges 10 bps per side in fees and 5 bps per side in slippage, and assumes the stop is hit first if stop and target are both inside one candle. Parameters are exposed in the API query string.

This is a historical 1H simulation; the live ticker is shown separately and is never used as historical backtest input. Higher-timeframe confluence is not yet an entry filter in this initial backtest. Shorts are theoretical directional simulations, not Binance Spot orders. Funding, partial fills, latency, and market impact are not modeled. No orders are submitted.

## Input CSV

Upload one CSV per supplied timeframe: `1M`, `1W`, `1D`, `4H`, and/or `1H`. Required columns are `timestamp,open,high,low,close`; `volume` is optional. Headers are case-insensitive. Each file must contain at least five valid, unique-timestamp OHLC rows. Timestamps are sorted chronologically but their timezone is not inferred.

Missing timeframes are reported as `INSUFFICIENT DATA`; the analyzer will not resample or infer them. Upload the actual timeframe data you want analyzed.

## Analysis rules

- Pivots use two bars on each side; only confirmed pivots participate in structural breaks.
- BOS/CHoCH candidates require a close beyond the latest confirmed pivot. Wick-only breaches are classified separately as liquidity sweeps.
- FVGs use the three-candle definition and report fill state from subsequent bars.
- A zone candidate requires a qualifying displacement plus either a structural break or a move of at least 2× the prior ATR. Its origin is the last opposite candle, or a compact prior base when no opposite candle exists.
- Zone scores use the supplied 100-point rubric: higher-timeframe alignment (20), displacement (15), structure break (15), FVG (10), sweep (10), freshness (10), base quality (5), nesting (10), and liquidity proximity (5).
- Overall directional bias uses the supplied timeframe weights (1M 30, 1W 25, 1D 20, 4H 15, 1H 10); ties resolve toward the higher timeframe, and conflicting votes are reported rather than hidden.
- Equal-high/low clustering currently uses a configurable relative tolerance of 0.1% (`EQUAL_LEVEL_TOLERANCE` in `backend/app/analyzer.py`). Tune this to the instrument's tick size and volatility before relying on those clusters.
- “Order block” and supply/demand outputs are algorithmic candidates, not verified institutional activity. Review their source candles and lifecycle in the JSON result.

## Tests

From `backend` run:

```powershell
python -m pytest -q
```

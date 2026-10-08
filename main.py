"""FastAPI service for the Project Sentinel market-structure analyzer."""

from typing import Any
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.analyzer import analyze_market
from app.backtest import backtest_combined_strategy
from app.market_data import MarketDataError, fetch_historical_bars, fetch_live_market

app = FastAPI(
    title="Project Sentinel Market Analysis API",
    description="Deterministic multi-timeframe supply/demand and price-action analysis of supplied OHLC data.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_file_path = Path(__file__).resolve()
_PROJECT_ROOT_CANDIDATES = [
    _file_path.parent.parent,
    *_file_path.parents[:3],
]
FRONTEND_DIST = next(
    (candidate / "frontend" / "dist" for candidate in _PROJECT_ROOT_CANDIDATES if (candidate / "frontend" / "dist").is_dir()),
    _file_path.parent.parent / "frontend" / "dist",
)
FRONTEND_ASSETS = FRONTEND_DIST / "assets"
if FRONTEND_ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="frontend-assets")


class AnalyzeRequest(BaseModel):
    instrument: str = ""
    timeframes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "market-analysis"}


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    try:
        return analyze_market(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/live-analysis")
def live_analysis(
    symbol: str = "SOL/USDT",
    limit: int = Query(default=300, ge=50, le=1000),
) -> dict[str, Any]:
    try:
        market = fetch_live_market(symbol, limit)
        analysis = analyze_market({"instrument": market["symbol"], "timeframes": market["timeframes"]})
        live_fields = ("symbol", "exchange_symbol", "live_price", "as_of_utc", "source")
        return {
            "live_market": {key: market[key] for key in live_fields},
            "analysis": analysis,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/backtest")
def historical_backtest(
    symbol: str = "SOL/USDT",
    timeframe: str = "1H",
    limit: int = Query(default=1000, ge=50, le=1000),
    risk_reward: float = Query(default=2.0, ge=0.5, le=10),
    fee_bps: float = Query(default=10.0, ge=0, le=100),
    slippage_bps: float = Query(default=5.0, ge=0, le=100),
) -> dict[str, Any]:
    try:
        market = fetch_historical_bars(symbol, timeframe, limit)
        return backtest_combined_strategy(
            market["bars"],
            symbol=market["symbol"],
            timeframe=timeframe,
            risk_reward=risk_reward,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/", include_in_schema=False, response_model=None)
def frontend_home() -> FileResponse | dict[str, str]:
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {
        "service": "Project Sentinel Market Analysis API",
        "message": "Build the frontend or run the Vite development server at http://localhost:5173.",
        "docs": "/docs",
    }

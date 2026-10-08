"""Read-only Binance Spot market-data access; no credentials or order APIs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx

BASE_URLS = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api.binance.us",
)
INTERVALS = {"1M": "1M", "1W": "1w", "1D": "1d", "4H": "4h", "1H": "1h"}
MAX_KLINE_LIMIT = 1000


class MarketDataError(RuntimeError):
    """Raised when Binance public market data cannot be retrieved or validated."""


class InvalidMarketSymbol(ValueError):
    """Raised when Binance does not recognize a requested Spot pair."""


def normalize_symbol(symbol: str) -> str:
    normalized = re.sub(r"[/\s_-]", "", symbol).upper()
    if not re.fullmatch(r"[A-Z0-9]{5,20}", normalized):
        raise ValueError("Symbol must be a Binance Spot pair such as SOL/USDT or SOLUSDT")
    return normalized


def _get_json(client: httpx.Client, path: str, params: dict[str, Any]) -> Any:
    failures = []
    for base_url in BASE_URLS:
        try:
            response = client.get(f"{base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            failures.append(f"{base_url}: {exc}")
            continue

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code == 400 and isinstance(payload, dict) and payload.get("code") == -1121:
            symbol = str(params.get("symbol", ""))
            raise InvalidMarketSymbol(
                f"Binance does not recognize Spot symbol '{symbol}'. Enter a valid Binance Spot pair, "
                "for example SOL/USDT."
            )

        if response.is_success:
            if payload is None:
                failures.append(f"{base_url}: response was not valid JSON")
                continue
            return payload

        detail = f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            detail += f" ({payload.get('msg', 'market-data request rejected')})"
        failures.append(f"{base_url}: {detail}")

    raise MarketDataError(
        "All public Binance market-data endpoints failed. " + "; ".join(failures)
    )


def _iso_utc(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def _parse_klines(payload: Any, now_ms: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise MarketDataError("Binance returned an unexpected candle payload")
    bars = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            continue
        close_time_ms = int(row[6])
        if close_time_ms >= now_ms:
            continue
        bars.append(
            {
                "timestamp": _iso_utc(int(row[0])),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return bars


def fetch_historical_bars(symbol: str, timeframe: str = "1H", limit: int = 1000) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if timeframe not in INTERVALS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if not 50 <= limit <= MAX_KLINE_LIMIT:
        raise ValueError(f"limit must be between 50 and {MAX_KLINE_LIMIT}")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        with httpx.Client(timeout=15.0) as client:
            raw = _get_json(
                client,
                "/api/v3/klines",
                {"symbol": normalized, "interval": INTERVALS[timeframe], "limit": limit},
            )
    except httpx.HTTPError as exc:
        raise MarketDataError(f"Binance market-data request failed: {exc}") from exc
    bars = _parse_klines(raw, now_ms)
    if not bars:
        raise MarketDataError("Binance returned no completed candles for this request")
    return {
        "symbol": normalized,
        "timeframe": timeframe,
        "bars": bars,
        "source": "Binance Spot public market data (regional endpoint fallback enabled)",
    }


def fetch_live_market(symbol: str = "SOL/USDT", limit: int = 300) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    if not 50 <= limit <= MAX_KLINE_LIMIT:
        raise ValueError(f"limit must be between 50 and {MAX_KLINE_LIMIT}")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        with httpx.Client(timeout=15.0) as client:
            ticker = _get_json(client, "/api/v3/ticker/price", {"symbol": normalized})
            if not isinstance(ticker, dict) or "price" not in ticker:
                raise MarketDataError("Binance returned an unexpected ticker payload")
            timeframes = {}
            for timeframe, interval in INTERVALS.items():
                raw = _get_json(
                    client,
                    "/api/v3/klines",
                    {"symbol": normalized, "interval": interval, "limit": limit},
                )
                timeframes[timeframe] = _parse_klines(raw, now_ms)
    except httpx.HTTPError as exc:
        raise MarketDataError(f"Binance market-data request failed: {exc}") from exc

    display_symbol = f"{normalized[:-4]}/{normalized[-4:]}" if normalized.endswith("USDT") else normalized
    return {
        "symbol": display_symbol,
        "exchange_symbol": normalized,
        "live_price": float(ticker["price"]),
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Binance Spot public market data (regional endpoint fallback enabled)",
        "timeframes": timeframes,
    }

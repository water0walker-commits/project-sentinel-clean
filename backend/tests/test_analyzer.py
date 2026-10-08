from __future__ import annotations

import pytest

from app.analyzer import _fvg_candidates, _weighted_bias, analyze_market, normalize_bars
from app.main import app
from fastapi.testclient import TestClient


def bar(timestamp: str, open_: float, high: float, low: float, close: float, volume: float = 1) -> dict:
    return {
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_normalize_sorts_rows_and_preserves_missing_volume() -> None:
    rows = [
        {"timestamp": "2026-01-02", "open": 2, "high": 3, "low": 1, "close": 2},
        {"timestamp": "2026-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5},
    ]

    normalized = normalize_bars(rows)

    assert [row["timestamp"] for row in normalized] == ["2026-01-01", "2026-01-02"]
    assert normalized[0]["volume"] is None


def test_normalize_rejects_invalid_ohlc_and_duplicate_timestamps() -> None:
    with pytest.raises(ValueError, match="high is below"):
        normalize_bars([bar("2026-01-01", 10, 9, 8, 9.5)])

    duplicate = bar("2026-01-01", 2, 3, 1, 2)
    with pytest.raises(ValueError, match="duplicate timestamps"):
        normalize_bars([duplicate, duplicate.copy()])


def test_three_candle_fvg_boundaries_and_fill_state() -> None:
    rows = [
        bar("1", 9, 10, 8, 9.5),
        bar("2", 10, 11, 9, 10.5),
        bar("3", 12, 13, 12, 12.5),
        bar("4", 12.5, 13, 11.5, 12),
    ]

    gap = _fvg_candidates(rows)[0]

    assert gap["direction"] == "BULLISH"
    assert gap["lower"] == 10
    assert gap["upper"] == 12
    assert gap["status"] == "PARTIALLY FILLED FVG"
    assert gap["fill_pct"] == 25


def test_missing_timeframes_are_explicit_and_no_prices_are_invented() -> None:
    rows = [
        bar("2026-01-01T00:00", 10, 11, 9, 10.5),
        bar("2026-01-01T01:00", 10.5, 12, 10, 11.5),
        bar("2026-01-01T02:00", 11.5, 12, 10.5, 11),
        bar("2026-01-01T03:00", 11, 11.5, 10, 10.5),
        bar("2026-01-01T04:00", 10.5, 11, 10, 10.75),
    ]

    result = analyze_market({"instrument": "TEST", "timeframes": {"1H": rows}})

    assert result["timeframes"]["1H"]["status"] == "OK"
    assert result["timeframes"]["1M"]["status"] == "INSUFFICIENT DATA"
    assert result["current_price"] == 10.75
    assert result["available_timeframes"] == ["1H"]
    assert result["market_bias"]["alignment"] == "LOW"


def test_unsupported_timeframe_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported timeframe"):
        analyze_market({"timeframes": {"15m": []}})


def test_current_price_uses_freshest_supplied_timeframe() -> None:
    older = [bar(f"2026-01-0{day}T00:00", 10, 11, 9, 10 + day / 10) for day in range(1, 6)]
    newer = [bar(f"2026-02-0{day}T00:00", 20, 21, 19, 20 + day / 10) for day in range(1, 6)]

    result = analyze_market({"timeframes": {"1H": older, "4H": newer}})

    assert result["price_timeframe"] == "4H"
    assert result["current_price"] == 20.5
    assert result["analysis_date"] == "2026-02-05T00:00"


def test_top_down_bias_uses_configured_timeframe_weights() -> None:
    results = {
        timeframe: {"status": "OK", "bias": bias}
        for timeframe, bias in {
            "1M": "BULLISH",
            "1W": "BULLISH",
            "1D": "BEARISH",
            "4H": "BEARISH",
            "1H": "BEARISH",
        }.items()
    }

    bias, scores = _weighted_bias(results, ("1M", "1W", "1D", "4H", "1H"))

    assert bias == "BULLISH"
    assert scores == {"BULLISH": 55, "BEARISH": 45}


def test_analyze_api_accepts_ohlc_payload() -> None:
    client = TestClient(app)
    rows = [bar(f"2026-01-0{day}T00:00", 10, 11, 9, 10 + day / 10) for day in range(1, 6)]

    response = client.post("/api/analyze", json={"instrument": "TEST", "timeframes": {"1H": rows}})

    assert response.status_code == 200
    assert response.json()["instrument"] == "TEST"
    assert response.json()["timeframes"]["1M"]["status"] == "INSUFFICIENT DATA"

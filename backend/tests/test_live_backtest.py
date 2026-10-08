from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

import app.backtest as backtest_module
import app.main as main_module
from app.backtest import backtest_combined_strategy
from app.main import app
from app.market_data import InvalidMarketSymbol, _get_json, _parse_klines, normalize_symbol


def flat_bars(count: int = 30) -> list[dict[str, float | str]]:
    return [
        {
            "timestamp": f"2026-01-{day:02d}T00:00:00+00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1.0,
        }
        for day in range(1, count + 1)
    ]


def test_normalize_binance_symbols_and_reject_invalid_input() -> None:
    assert normalize_symbol("SOL/USDT") == "SOLUSDT"
    assert normalize_symbol("eth-usdt") == "ETHUSDT"

    try:
        normalize_symbol("SOL/USDT?drop=true")
    except ValueError as exc:
        assert "Binance Spot pair" in str(exc)
    else:
        raise AssertionError("invalid symbol should be rejected")


def test_binance_invalid_symbol_error_explains_how_to_fix(monkeypatch) -> None:
    response = httpx.Response(
        400,
        json={"code": -1121, "msg": "Invalid symbol."},
        request=httpx.Request("GET", "https://api.binance.com/api/v3/ticker/price"),
    )
    client = Mock()
    client.get.return_value = response

    with pytest.raises(ValueError, match=r"does not recognize.*SOLONA.*SOL/USDT"):
        _get_json(client, "/api/v3/ticker/price", {"symbol": "SOLONA"})


def test_kline_parser_excludes_unclosed_candle() -> None:
    rows = [
        [0, "1", "2", "0.5", "1.5", "10", 999],
        [1000, "1.5", "2", "1", "1.8", "12", 2000],
    ]

    parsed = _parse_klines(rows, now_ms=2000)

    assert len(parsed) == 1
    assert parsed[0]["close"] == 1.5
    assert parsed[0]["volume"] == 10


def test_backtest_flat_market_produces_no_forced_trades() -> None:
    result = backtest_combined_strategy(flat_bars(), symbol="SOLUSDT")

    assert result["summary"]["trades"] == 0
    assert result["trades"] == []
    assert result["summary"]["total_net_r"] == 0
    assert "next candle open" in result["strategy"]["entry"].lower()


def test_backtest_requires_sweep_break_retest_then_next_bar_entry(monkeypatch) -> None:
    candles = flat_bars(32)
    candles[26].update({"open": 99.5, "high": 102.0, "low": 99.0, "close": 101.0})
    candles[27].update({"open": 101.5, "high": 102.0, "low": 101.0, "close": 101.5})
    candles[28].update({"open": 101.5, "high": 109.0, "low": 101.0, "close": 108.0})
    zone = {
        "id": "test-demand",
        "direction": "BULLISH",
        "_departure_index": 24,
        "_origin_index": 23,
        "_origin_end_index": 23,
        "structural_break": True,
        "is_order_block": True,
        "has_fvg": False,
        "lower": 98.0,
        "upper": 100.0,
        "source": "ORDER_BLOCK",
    }
    monkeypatch.setattr(backtest_module, "_find_pivots", lambda bars: [])
    monkeypatch.setattr(
        backtest_module,
        "_events_and_liquidity",
        lambda bars, pivots, bias: (
            [{"timestamp": candles[25]["timestamp"], "direction": "BULLISH"}],
            [{"timestamp": candles[22]["timestamp"], "direction": "SELL_SIDE"}],
        ),
    )
    monkeypatch.setattr(backtest_module, "_fvg_candidates", lambda bars: [])
    monkeypatch.setattr(backtest_module, "_discover_zones", lambda *args: [zone])
    monkeypatch.setattr(backtest_module, "_structure", lambda pivots: ("BULLISH", "BULLISH TREND"))
    monkeypatch.setattr(
        backtest_module,
        "_zone_lifecycle",
        lambda *args: {"status": "PARTIALLY MITIGATED", "retests": 1},
    )

    result = backtest_combined_strategy(
        candles, symbol="SOLUSDT", fee_bps=0, slippage_bps=0
    )

    assert result["summary"]["trades"] == 1
    trade = result["trades"][0]
    assert trade["structure_event_time"] == candles[25]["timestamp"]
    assert trade["signal_time"] == candles[26]["timestamp"]
    assert trade["entry_time"] == candles[27]["timestamp"]
    assert trade["exit_reason"] == "TARGET"
    assert trade["net_r_multiple"] == 2.0


def test_live_endpoint_uses_read_only_market_snapshot(monkeypatch) -> None:
    candles = flat_bars()
    monkeypatch.setattr(
        "app.main.fetch_live_market",
        lambda symbol, limit: {
            "symbol": "SOL/USDT",
            "exchange_symbol": "SOLUSDT",
            "live_price": 123.45,
            "as_of_utc": "2026-01-30T00:00:00+00:00",
            "source": "Binance Spot public market data",
            "timeframes": {"1H": candles},
        },
    )
    response = TestClient(app).get("/api/live-analysis?symbol=SOL%2FUSDT")

    assert response.status_code == 200
    assert response.json()["live_market"]["live_price"] == 123.45
    assert response.json()["analysis"]["current_price"] == 100


def test_root_serves_built_frontend_when_available(tmp_path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text("<html>Sentinel test page</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Sentinel" in response.text


def test_live_endpoint_returns_actionable_invalid_symbol_error(monkeypatch) -> None:
    def reject_symbol(symbol, limit):
        raise InvalidMarketSymbol(
            "Binance does not recognize Spot symbol 'SOLONA'. Enter a valid Binance Spot pair, for example SOL/USDT."
        )

    monkeypatch.setattr("app.main.fetch_live_market", reject_symbol)
    response = TestClient(app).get("/api/live-analysis?symbol=SOLONA")

    assert response.status_code == 422
    assert "SOL/USDT" in response.json()["detail"]


def test_backtest_endpoint_calls_historical_simulator(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.fetch_historical_bars",
        lambda symbol, timeframe, limit: {"symbol": "SOLUSDT", "timeframe": timeframe, "bars": flat_bars()},
    )
    monkeypatch.setattr(
        "app.main.backtest_combined_strategy",
        lambda bars, **kwargs: {"symbol": kwargs["symbol"], "summary": {"trades": 0}},
    )

    response = TestClient(app).get("/api/backtest?symbol=SOLUSDT&timeframe=1H")

    assert response.status_code == 200
    assert response.json() == {"symbol": "SOLUSDT", "summary": {"trades": 0}}

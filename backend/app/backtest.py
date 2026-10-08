"""Causal, OHLC-only validation of combined structure and zone setups."""

from __future__ import annotations

from statistics import mean
from typing import Any

from app.analyzer import (
    _discover_zones,
    _events_and_liquidity,
    _find_pivots,
    _fvg_candidates,
    _structure,
    _zone_lifecycle,
    normalize_bars,
)


def backtest_combined_strategy(
    raw_bars: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str = "1H",
    risk_reward: float = 2.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    sweep_window: int = 8,
    retest_window: int = 5,
) -> dict[str, Any]:
    """Simulate setups using only information available at each signal candle.

    Setup order: opposing-side liquidity sweep, displacement/structural break from
    an OB/FVG candidate, then a rejection on the zone retest. Entry occurs at the
    next candle open. A 2R target and distal-zone stop are used;
    if both stop and target lie within one candle, the stop is assumed first.
    """
    if not 0.5 <= risk_reward <= 10:
        raise ValueError("risk_reward must be between 0.5 and 10")
    if not 0 <= fee_bps <= 100 or not 0 <= slippage_bps <= 100:
        raise ValueError("fee_bps and slippage_bps must be between 0 and 100")
    if not 1 <= sweep_window <= 30 or not 1 <= retest_window <= 30:
        raise ValueError("sweep_window and retest_window must be between 1 and 30")

    bars = normalize_bars(raw_bars)
    if len(bars) < 30:
        raise ValueError("At least 30 completed candles are required for the backtest")

    pivots = _find_pivots(bars)
    events, sweeps = _events_and_liquidity(bars, pivots, "NEUTRAL")
    fvg_list = _fvg_candidates(bars)
    zones = _discover_zones(timeframe, bars, pivots, fvg_list, sweeps)
    index_by_timestamp = {bar["timestamp"]: index for index, bar in enumerate(bars)}
    events_by_index: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        event_index = index_by_timestamp.get(event["timestamp"])
        if event_index is not None:
            events_by_index.setdefault(event_index, []).append(event)
    sweep_indices = [
        (index_by_timestamp[sweep["timestamp"]], sweep)
        for sweep in sweeps
        if sweep["timestamp"] in index_by_timestamp
    ]

    event_setups = []
    for event_index, event_list in events_by_index.items():
        available_pivots = [pivot for pivot in pivots if pivot["confirmed_at"] < event_index]
        bias_at_event, _ = _structure(available_pivots)
        if bias_at_event not in ("BULLISH", "BEARISH"):
            continue
        for event in event_list:
            direction = event["direction"]
            expected_sweep = "SELL_SIDE" if direction == "BULLISH" else "BUY_SIDE"
            recent_sweeps = [
                (sweep_index, sweep)
                for sweep_index, sweep in sweep_indices
                if sweep["direction"] == expected_sweep
                and 0 < event_index - sweep_index <= sweep_window
            ]
            if not recent_sweeps:
                continue
            for zone in zones:
                departure_index = zone["_departure_index"]
                if (
                    zone["direction"] == direction
                    and zone["structural_break"]
                    and (zone["is_order_block"] or zone["has_fvg"])
                    and any(sweep_index < departure_index <= event_index for sweep_index, _ in recent_sweeps)
                ):
                    event_setups.append(
                        {
                            "event_index": event_index,
                            "direction": direction,
                            "event": event,
                            "zone": zone,
                        }
                    )

    trades: list[dict[str, Any]] = []
    skipped_signals = 0
    used_setups: set[tuple[int, str]] = set()
    index = 25
    slip = slippage_bps / 10_000

    while index < len(bars) - 1:
        signal_bar = bars[index]
        eligible_setups = []
        for setup in event_setups:
            zone = setup["zone"]
            direction = setup["direction"]
            event_index = setup["event_index"]
            setup_key = (event_index, zone["id"])
            if setup_key in used_setups or not event_index < index <= event_index + retest_window:
                continue
            touches_zone = signal_bar["low"] <= zone["upper"] and signal_bar["high"] >= zone["lower"]
            rejected_directionally = (
                signal_bar["close"] > zone["upper"] and signal_bar["close"] > signal_bar["open"]
                if direction == "BULLISH"
                else signal_bar["close"] < zone["lower"] and signal_bar["close"] < signal_bar["open"]
            )
            if not touches_zone or not rejected_directionally:
                continue
            lifecycle = _zone_lifecycle(
                bars[: index + 1], zone["_origin_index"], zone["_origin_end_index"],
                zone["lower"], zone["upper"], direction,
            )
            if lifecycle["status"] != "INVALIDATED":
                eligible_setups.append(setup)
        if not eligible_setups:
            index += 1
            continue

        setup = min(
            eligible_setups,
            key=lambda item: min(abs(signal_bar["close"] - item["zone"]["lower"]), abs(signal_bar["close"] - item["zone"]["upper"])),
        )
        direction = setup["direction"]
        chosen_zone = setup["zone"]
        entry_index = index + 1
        next_open = bars[entry_index]["open"]
        if direction == "BULLISH":
            entry = next_open * (1 + slip)
            stop = chosen_zone["lower"]
            if stop >= entry:
                skipped_signals += 1
                used_setups.add((setup["event_index"], chosen_zone["id"]))
                index += 1
                continue
            risk = entry - stop
            target = entry + risk * risk_reward
        else:
            entry = next_open * (1 - slip)
            stop = chosen_zone["upper"]
            if stop <= entry:
                skipped_signals += 1
                used_setups.add((setup["event_index"], chosen_zone["id"]))
                index += 1
                continue
            risk = stop - entry
            target = entry - risk * risk_reward

        exit_index = len(bars) - 1
        exit_reason = "END_OF_DATA"
        exit_reference = bars[-1]["close"]
        for future_index in range(entry_index, len(bars)):
            future = bars[future_index]
            if direction == "BULLISH":
                stop_hit = future["low"] <= stop
                target_hit = future["high"] >= target
            else:
                stop_hit = future["high"] >= stop
                target_hit = future["low"] <= target
            if stop_hit:
                exit_index, exit_reference, exit_reason = future_index, stop, "STOP"
                break
            if target_hit:
                exit_index, exit_reference, exit_reason = future_index, target, "TARGET"
                break

        exit_fill = exit_reference * (1 - slip) if direction == "BULLISH" else exit_reference * (1 + slip)
        gross_pct = (
            (exit_fill - entry) / entry * 100
            if direction == "BULLISH"
            else (entry - exit_fill) / entry * 100
        )
        fees_pct = fee_bps * (entry + exit_fill) / entry / 100
        net_pct = gross_pct - fees_pct
        risk_pct = risk / entry * 100
        net_r = net_pct / risk_pct if risk_pct > 0 else 0.0
        trades.append(
            {
                "direction": direction,
                "structure_event_time": bars[setup["event_index"]]["timestamp"],
                "signal_time": bars[index]["timestamp"],
                "entry_time": bars[entry_index]["timestamp"],
                "entry_price": round(entry, 8),
                "zone": [chosen_zone["lower"], chosen_zone["upper"]],
                "zone_source": chosen_zone["source"],
                "stop": round(stop, 8),
                "target": round(target, 8),
                "exit_time": bars[exit_index]["timestamp"],
                "exit_price": round(exit_fill, 8),
                "exit_reason": exit_reason,
                "gross_return_pct": round(gross_pct, 4),
                "fees_pct": round(fees_pct, 4),
                "net_return_pct": round(net_pct, 4),
                "net_r_multiple": round(net_r, 4),
            }
        )
        used_setups.add((setup["event_index"], chosen_zone["id"]))
        index = exit_index + 1

    wins = [trade for trade in trades if trade["net_return_pct"] > 0]
    losses = [trade for trade in trades if trade["net_return_pct"] <= 0]
    positive_returns = sum(trade["net_return_pct"] for trade in wins)
    negative_returns = abs(sum(trade["net_return_pct"] for trade in losses))
    cumulative_r = 0.0
    peak_r = 0.0
    max_drawdown_r = 0.0
    for trade in trades:
        cumulative_r += trade["net_r_multiple"]
        peak_r = max(peak_r, cumulative_r)
        max_drawdown_r = max(max_drawdown_r, peak_r - cumulative_r)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data_range": [bars[0]["timestamp"], bars[-1]["timestamp"]],
        "bars_tested": len(bars),
        "strategy": {
            "name": "Structure + Liquidity Sweep + Retested OB/FVG",
            "signal": "Opposing-side liquidity sweep, then same-direction displacement/structural break from an OB/FVG candidate, then a directional candle-close rejection on that zone's retest.",
            "entry": "Next candle open; no same-candle fills are assumed.",
            "stop": "Beyond the zone's distal boundary.",
            "target": f"{risk_reward:g}R fixed target.",
            "fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slippage_bps,
            "same_candle_stop_and_target": "STOP FIRST (conservative)",
            "lookahead_policy": "Pivots are used only after their right-side confirmation bars; signals use completed candles and enter on the next bar.",
        },
        "summary": {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "total_net_return_pct_constant_notional": round(sum(trade["net_return_pct"] for trade in trades), 4),
            "expectancy_net_r": round(mean(trade["net_r_multiple"] for trade in trades), 4) if trades else 0.0,
            "total_net_r": round(sum(trade["net_r_multiple"] for trade in trades), 4),
            "max_drawdown_r": round(max_drawdown_r, 4),
            "profit_factor": round(positive_returns / negative_returns, 4) if negative_returns else None,
            "skipped_invalid_risk_signals": skipped_signals,
        },
        "trades": trades,
        "limitations": [
            "This is a historical simulation, not a backtest on a live quote. The live ticker is fetched and reported separately.",
            "The tested strategy uses only the selected execution timeframe; higher-timeframe alignment is not an entry filter in this first implementation.",
            "Fees and slippage are configurable assumptions and may differ from actual account/exchange execution costs.",
            "Short trades are theoretical directional simulations; they are not spot-market orders and no orders are submitted.",
            "No funding, latency, partial fills, market impact, or exchange-specific quantity filters are modeled.",
            "Historical results do not predict future performance.",
        ],
    }

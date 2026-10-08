"""Deterministic multi-timeframe supply/demand and price-action analysis."""

from __future__ import annotations

from math import isfinite
from statistics import mean
from typing import Any

TIMEFRAMES = ("1M", "1W", "1D", "4H", "1H")
TIMEFRAME_RANK = {timeframe: index for index, timeframe in enumerate(TIMEFRAMES)}
TIMEFRAME_WEIGHTS = {"1M": 30, "1W": 25, "1D": 20, "4H": 15, "1H": 10}
PIVOT_RADIUS = 2
EQUAL_LEVEL_TOLERANCE = 0.001
MAX_ZONES_PER_SIDE = 20


def _as_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def normalize_bars(raw_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and chronologically sort OHLCV rows without fabricating fields."""
    if not raw_bars:
        return []

    bars: list[dict[str, Any]] = []
    for row_number, row in enumerate(raw_bars, start=1):
        timestamp = row.get("timestamp", row.get("time", row.get("date")))
        if timestamp is None or str(timestamp).strip() == "":
            raise ValueError(f"row {row_number}: timestamp is required")
        bar = {
            "timestamp": str(timestamp),
            "open": _as_number(row.get("open"), f"row {row_number} open"),
            "high": _as_number(row.get("high"), f"row {row_number} high"),
            "low": _as_number(row.get("low"), f"row {row_number} low"),
            "close": _as_number(row.get("close"), f"row {row_number} close"),
            "volume": None,
        }
        if row.get("volume") not in (None, ""):
            bar["volume"] = _as_number(row["volume"], f"row {row_number} volume")
        if bar["high"] < max(bar["open"], bar["close"], bar["low"]):
            raise ValueError(f"row {row_number}: high is below another OHLC value")
        if bar["low"] > min(bar["open"], bar["close"], bar["high"]):
            raise ValueError(f"row {row_number}: low is above another OHLC value")
        bars.append(bar)

    bars.sort(key=lambda bar: bar["timestamp"])
    timestamps = [bar["timestamp"] for bar in bars]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("duplicate timestamps are not allowed")
    return bars


def _true_range(bar: dict[str, Any], previous_close: float | None) -> float:
    if previous_close is None:
        return bar["high"] - bar["low"]
    return max(
        bar["high"] - bar["low"],
        abs(bar["high"] - previous_close),
        abs(bar["low"] - previous_close),
    )


def _atr(bars: list[dict[str, Any]], index: int, period: int = 14) -> float:
    start = max(0, index - period)
    ranges = [
        _true_range(bars[i], bars[i - 1]["close"] if i > 0 else None)
        for i in range(start, index)
    ]
    return mean(ranges) if ranges else 0.0


def _find_pivots(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pivots: list[dict[str, Any]] = []
    radius = PIVOT_RADIUS
    if len(bars) < radius * 2 + 1:
        return pivots

    for index in range(radius, len(bars) - radius):
        bar = bars[index]
        left = bars[index - radius : index]
        right = bars[index + 1 : index + radius + 1]
        is_high = bar["high"] >= max(item["high"] for item in left) and bar["high"] > max(
            item["high"] for item in right
        )
        is_low = bar["low"] <= min(item["low"] for item in left) and bar["low"] < min(
            item["low"] for item in right
        )
        if is_high:
            pivots.append(
                {
                    "index": index,
                    "confirmed_at": index + radius,
                    "timestamp": bar["timestamp"],
                    "price": bar["high"],
                    "side": "HIGH",
                }
            )
        if is_low:
            pivots.append(
                {
                    "index": index,
                    "confirmed_at": index + radius,
                    "timestamp": bar["timestamp"],
                    "price": bar["low"],
                    "side": "LOW",
                }
            )

    pivots.sort(key=lambda pivot: (pivot["index"], pivot["side"]))
    previous: dict[str, float] = {}
    for pivot in pivots:
        side = pivot["side"]
        prior_price = previous.get(side)
        if prior_price is None:
            pivot["type"] = f"INITIAL_{side}"
        elif side == "HIGH":
            pivot["type"] = "HH" if pivot["price"] > prior_price else "LH"
        else:
            pivot["type"] = "HL" if pivot["price"] > prior_price else "LL"
        previous[side] = pivot["price"]
    return pivots


def _structure(pivots: list[dict[str, Any]]) -> tuple[str, str]:
    highs = [pivot for pivot in pivots if pivot["side"] == "HIGH"]
    lows = [pivot for pivot in pivots if pivot["side"] == "LOW"]
    bullish = len(highs) >= 2 and len(lows) >= 2 and highs[-1]["type"] == "HH" and lows[-1]["type"] == "HL"
    bearish = len(highs) >= 2 and len(lows) >= 2 and highs[-1]["type"] == "LH" and lows[-1]["type"] == "LL"
    if bullish:
        return "BULLISH", "BULLISH TREND"
    if bearish:
        return "BEARISH", "BEARISH TREND"
    return "NEUTRAL", "RANGING" if len(pivots) >= 4 else "UNCLEAR"


def _events_and_liquidity(
    bars: list[dict[str, Any]], pivots: list[dict[str, Any]], bias: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    last_broken: set[tuple[int, str]] = set()
    last_swept: set[tuple[int, str]] = set()

    for index, bar in enumerate(bars):
        available = [pivot for pivot in pivots if pivot["confirmed_at"] < index]
        for side in ("HIGH", "LOW"):
            prior = [pivot for pivot in available if pivot["side"] == side]
            if not prior:
                continue
            pivot = prior[-1]
            key = (pivot["index"], side)
            level = pivot["price"]
            if side == "HIGH":
                swept = bar["high"] > level and bar["close"] <= level
                broken = bar["close"] > level
                direction = "BULLISH"
            else:
                swept = bar["low"] < level and bar["close"] >= level
                broken = bar["close"] < level
                direction = "BEARISH"

            if swept and key not in last_swept:
                sweep = {
                    "level": level,
                    "direction": "BUY_SIDE" if side == "HIGH" else "SELL_SIDE",
                    "sweep_price": bar["high"] if side == "HIGH" else bar["low"],
                    "timeframe": "",
                    "timestamp": bar["timestamp"],
                    "pivot_timestamp": pivot["timestamp"],
                    "resulting_displacement": False,
                }
                sweeps.append(sweep)
                last_swept.add(key)
            if broken and key not in last_broken:
                event_type = (
                    "BOS" if direction == bias else "CHoCH" if bias in ("BULLISH", "BEARISH") else "POTENTIAL_BREAK"
                )
                events.append(
                    {
                        "type": event_type,
                        "direction": direction,
                        "level": level,
                        "timestamp": bar["timestamp"],
                        "close": bar["close"],
                        "confirmation": "CANDLE_CLOSE",
                        "pivot_timestamp": pivot["timestamp"],
                    }
                )
                last_broken.add(key)

    for sweep in sweeps:
        sweep_index = next(
            (i for i, bar in enumerate(bars) if bar["timestamp"] == sweep["timestamp"]), len(bars)
        )
        after = bars[sweep_index + 1 : sweep_index + 4]
        if after:
            sweep["resulting_displacement"] = any(
                abs(bar["close"] - bar["open"]) >= 1.2 * max(_atr(bars, sweep_index + j + 1), 1e-12)
                for j, bar in enumerate(after)
            )
    return events, sweeps


def _fvg_candidates(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for index in range(2, len(bars)):
        first, middle, third = bars[index - 2], bars[index - 1], bars[index]
        if first["high"] < third["low"]:
            direction = "BULLISH"
            lower, upper = first["high"], third["low"]
            later_lows = [bar["low"] for bar in bars[index + 1 :]]
            penetration = max(0.0, min(upper - min(later_lows), upper - lower)) if later_lows else 0.0
        elif first["low"] > third["high"]:
            direction = "BEARISH"
            lower, upper = third["high"], first["low"]
            later_highs = [bar["high"] for bar in bars[index + 1 :]]
            penetration = max(0.0, min(max(later_highs) - lower, upper - lower)) if later_highs else 0.0
        else:
            continue

        width = upper - lower
        fill_pct = round(min(100.0, penetration / width * 100.0), 2) if width > 0 else 100.0
        if fill_pct <= 0:
            status = "FRESH FVG"
        elif fill_pct >= 100:
            status = "FULLY FILLED FVG"
        else:
            status = "PARTIALLY FILLED FVG"
        gaps.append(
            {
                "direction": direction,
                "lower": lower,
                "upper": upper,
                "timeframe": "",
                "created_at": middle["timestamp"],
                "status": status,
                "fill_pct": fill_pct,
                "creation_index": index,
            }
        )
    return gaps


def _base_index(bars: list[dict[str, Any]], index: int, direction: str, atr: float) -> tuple[int, int, bool] | None:
    for candidate in range(index - 1, max(-1, index - 4), -1):
        bar = bars[candidate]
        is_opposite = bar["close"] < bar["open"] if direction == "BULLISH" else bar["close"] > bar["open"]
        if is_opposite:
            return candidate, candidate, True
    start = max(0, index - 2)
    prior = bars[start:index]
    if prior and atr > 0 and all((bar["high"] - bar["low"]) <= atr for bar in prior):
        return start, index - 1, False
    return None


def _zone_lifecycle(
    bars: list[dict[str, Any]], start: int, end: int, lower: float, upper: float, direction: str
) -> dict[str, Any]:
    following = bars[end + 1 :]
    touch_count = 0
    in_zone = False
    wick_through = False
    closes_through = False
    penetration = 0.0
    width = max(upper - lower, 1e-12)
    for bar in following:
        touched = bar["low"] <= upper and bar["high"] >= lower
        if touched and not in_zone:
            touch_count += 1
        in_zone = touched
        if direction == "BULLISH":
            if touched:
                penetration = max(penetration, min(1.0, max(0.0, (upper - bar["low"]) / width)))
            wick_through = wick_through or bar["low"] < lower
            closes_through = closes_through or bar["close"] < lower
        else:
            if touched:
                penetration = max(penetration, min(1.0, max(0.0, (bar["high"] - lower) / width)))
            wick_through = wick_through or bar["high"] > upper
            closes_through = closes_through or bar["close"] > upper

    if closes_through:
        status = "INVALIDATED"
    elif touch_count == 0:
        status = "FRESH / UNMITIGATED"
    elif touch_count >= 3 or penetration >= 0.9:
        status = "HEAVILY MITIGATED"
    else:
        status = "PARTIALLY MITIGATED"
    return {
        "status": status,
        "retests": touch_count,
        "fill_pct": round(penetration * 100.0, 2),
        "risk_state": "AT RISK" if wick_through and not closes_through else "NORMAL",
        "invalidation": lower if direction == "BULLISH" else upper,
    }


def _discover_zones(
    timeframe: str,
    bars: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    fvg_list: list[dict[str, Any]],
    sweeps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    high_pivots = [pivot for pivot in pivots if pivot["side"] == "HIGH"]
    low_pivots = [pivot for pivot in pivots if pivot["side"] == "LOW"]
    fvg_by_index = {gap["creation_index"]: gap for gap in fvg_list}

    for index in range(3, len(bars)):
        bar = bars[index]
        atr = _atr(bars, index)
        candle_range = bar["high"] - bar["low"]
        body = abs(bar["close"] - bar["open"])
        if atr <= 0 or candle_range <= 0 or body < 1.2 * atr or body / candle_range < 0.6:
            continue
        direction = "BULLISH" if bar["close"] > bar["open"] else "BEARISH"
        confirmed_highs = [pivot for pivot in high_pivots if pivot["confirmed_at"] < index]
        confirmed_lows = [pivot for pivot in low_pivots if pivot["confirmed_at"] < index]
        broke_structure = (
            bool(confirmed_highs) and bar["close"] > confirmed_highs[-1]["price"]
            if direction == "BULLISH"
            else bool(confirmed_lows) and bar["close"] < confirmed_lows[-1]["price"]
        )
        if not broke_structure and body < 2.0 * atr:
            continue

        base = _base_index(bars, index, direction, atr)
        if base is None:
            continue
        start, end, is_order_block = base
        origin = bars[start : end + 1]
        lower = min(item["low"] for item in origin)
        upper = max(item["high"] for item in origin)
        if upper <= lower:
            continue

        fvg = fvg_by_index.get(index)
        expected_sweep = "SELL_SIDE" if direction == "BULLISH" else "BUY_SIDE"
        recent_sweep = next(
            (sweep for sweep in reversed(sweeps) if sweep["direction"] == expected_sweep and 0 < index - next(
                (i for i, item in enumerate(bars) if item["timestamp"] == sweep["timestamp"]), index
            ) <= 5),
            None,
        )
        lifecycle = _zone_lifecycle(bars, start, end, lower, upper, direction)
        zones.append(
            {
                "id": f"{timeframe}-{direction}-{bars[start]['timestamp']}",
                "timeframe": timeframe,
                "type": "DEMAND" if direction == "BULLISH" else "SUPPLY",
                "direction": direction,
                "lower": lower,
                "upper": upper,
                "distal": lower if direction == "BULLISH" else upper,
                "proximal": upper if direction == "BULLISH" else lower,
                "zone_range": [lower, upper],
                "created_at": bars[start]["timestamp"],
                "displacement_at": bar["timestamp"],
                "source": "ORDER_BLOCK" if is_order_block else "BASE",
                "is_order_block": is_order_block,
                "fvg_id": fvg.get("created_at") if fvg else None,
                "has_fvg": fvg is not None,
                "liquidity_sweep_before_departure": recent_sweep is not None,
                "swept_level": recent_sweep["level"] if recent_sweep else None,
                "structural_break": broke_structure,
                "displacement_atr": round(body / atr, 2),
                "clean_base": (upper - lower) <= 1.5 * atr,
                "parent_zone": None,
                "nested_zones": [],
                "score": 0,
                "score_breakdown": {},
                **lifecycle,
                "_origin_index": start,
                "_origin_end_index": end,
                "_departure_index": index,
            }
        )

    unique: dict[str, dict[str, Any]] = {}
    for zone in zones:
        unique[zone["id"]] = zone
    result = list(unique.values())
    for direction in ("BULLISH", "BEARISH"):
        side = [zone for zone in result if zone["direction"] == direction]
        side.sort(key=lambda zone: zone["_origin_index"])
        if len(side) > MAX_ZONES_PER_SIDE:
            retained = side[-MAX_ZONES_PER_SIDE:]
            result = [zone for zone in result if zone["direction"] != direction] + retained
    return result


def _liquidity_pools(pivots: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    for side, direction in (("HIGH", "BUY_SIDE"), ("LOW", "SELL_SIDE")):
        side_pivots = [pivot for pivot in pivots if pivot["side"] == side]
        for index, pivot in enumerate(side_pivots):
            matches = [pivot]
            for other in side_pivots[index + 1 :]:
                denominator = max(abs(pivot["price"]), abs(other["price"]), 1e-12)
                if abs(pivot["price"] - other["price"]) / denominator <= EQUAL_LEVEL_TOLERANCE:
                    matches.append(other)
            if len(matches) >= 2:
                pools.append(
                    {
                        "side": direction,
                        "type": "EQUAL_HIGHS" if side == "HIGH" else "EQUAL_LOWS",
                        "level": round(mean(item["price"] for item in matches), 8),
                        "timeframe": timeframe,
                        "timestamps": [item["timestamp"] for item in matches],
                    }
                )
        if side_pivots:
            latest = side_pivots[-1]
            pools.append(
                {
                    "side": direction,
                    "type": "PREVIOUS_MAJOR_HIGH" if side == "HIGH" else "PREVIOUS_MAJOR_LOW",
                    "level": latest["price"],
                    "timeframe": timeframe,
                    "timestamps": [latest["timestamp"]],
                }
            )
    return pools


def _market_structure_details(
    bars: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    bias: str,
    structure: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    highs = [pivot for pivot in pivots if pivot["side"] == "HIGH"]
    lows = [pivot for pivot in pivots if pivot["side"] == "LOW"]
    protected_low = next((pivot["price"] for pivot in reversed(lows) if pivot["type"] == "HL"), None) if bias == "BULLISH" else None
    protected_high = next((pivot["price"] for pivot in reversed(highs) if pivot["type"] == "LH"), None) if bias == "BEARISH" else None
    latest_event = events[-1] if events else None
    if latest_event and latest_event["type"] == "CHoCH":
        if latest_event["direction"] == "BEARISH":
            protected_low = None
        else:
            protected_high = None

    recent_types = [pivot["type"] for pivot in pivots[-6:]]
    directional_count = sum(
        pivot_type in (("HH", "HL") if bias == "BULLISH" else ("LH", "LL"))
        for pivot_type in recent_types
    ) if bias in ("BULLISH", "BEARISH") else 0
    if bias in ("BULLISH", "BEARISH"):
        strength = "VERY STRONG" if directional_count >= 5 and latest_event and latest_event["type"] == "BOS" else "STRONG" if directional_count >= 4 else "MODERATE"
        confidence = "HIGH" if len(pivots) >= 8 and latest_event else "MEDIUM" if len(pivots) >= 4 else "LOW"
    else:
        strength = "WEAK" if structure == "RANGING" else "UNCLEAR"
        confidence = "LOW" if structure == "UNCLEAR" else "MEDIUM"

    current = bars[-1]["close"]
    latest_high = highs[-1]["price"] if highs else None
    latest_low = lows[-1]["price"] if lows else None
    if latest_high is not None and current > latest_high:
        location = "ABOVE_LAST_CONFIRMED_SWING_HIGH"
    elif latest_low is not None and current < latest_low:
        location = "BELOW_LAST_CONFIRMED_SWING_LOW"
    elif latest_high is not None and latest_low is not None:
        location = "INSIDE_RECENT_SWING_RANGE"
    else:
        location = "INSUFFICIENT_STRUCTURAL_EVIDENCE"

    if bias == "BULLISH" and protected_low is not None:
        primary = f"Bullish structure remains intact while closes hold above protected low {protected_low}."
        alternative = f"A decisive close below {protected_low} would invalidate this bullish structure and raise a bearish CHoCH possibility."
    elif bias == "BEARISH" and protected_high is not None:
        primary = f"Bearish structure remains intact while closes hold below protected high {protected_high}."
        alternative = f"A decisive close above {protected_high} would invalidate this bearish structure and raise a bullish CHoCH possibility."
    else:
        primary = "No directional continuation scenario is established; wait for a candle-close break and follow-through beyond a confirmed swing."
        alternative = "A wick-only breach remains a possible liquidity sweep, not a confirmed structure reversal."

    return {
        "protected_high": protected_high,
        "protected_low": protected_low,
        "invalidation_level": protected_low if bias == "BULLISH" else protected_high if bias == "BEARISH" else None,
        "support_levels": list(dict.fromkeys(pivot["price"] for pivot in reversed(lows)))[:3],
        "resistance_levels": list(dict.fromkeys(pivot["price"] for pivot in reversed(highs)))[:3],
        "current_location": location,
        "trend_strength": strength,
        "confidence": confidence,
        "primary_scenario": primary,
        "alternative_scenario": alternative,
    }


def _analyze_timeframe(timeframe: str, raw_bars: list[dict[str, Any]]) -> dict[str, Any]:
    bars = normalize_bars(raw_bars)
    if len(bars) < 5:
        return {
            "timeframe": timeframe,
            "status": "INSUFFICIENT DATA",
            "bars": len(bars),
            "data_range": [bars[0]["timestamp"], bars[-1]["timestamp"]] if bars else [],
            "current_price": bars[-1]["close"] if bars else None,
            "bias": "UNCLEAR",
            "structure": "UNCLEAR",
            "pivot_sequence": [],
            "pivots": [],
            "supply_zones": [],
            "demand_zones": [],
            "order_blocks": [],
            "fvgs": [],
            "liquidity": [],
            "sweeps": [],
            "events": [],
            "latest_event": None,
            "protected_high": None,
            "protected_low": None,
            "invalidation_level": None,
            "support_levels": [],
            "resistance_levels": [],
            "current_location": "INSUFFICIENT_STRUCTURAL_EVIDENCE",
            "trend_strength": "UNCLEAR",
            "confidence": "LOW",
            "primary_scenario": "INSUFFICIENT DATA",
            "alternative_scenario": "INSUFFICIENT DATA",
        }

    pivots = _find_pivots(bars)
    bias, structure = _structure(pivots)
    events, sweeps = _events_and_liquidity(bars, pivots, bias)
    gaps = _fvg_candidates(bars)
    zones = _discover_zones(timeframe, bars, pivots, gaps, sweeps)
    for item in gaps:
        item["timeframe"] = timeframe
        item.pop("creation_index", None)
    for sweep in sweeps:
        sweep["timeframe"] = timeframe
    for event in events:
        event["timeframe"] = timeframe
    if events and events[-1]["type"] == "CHoCH":
        structure = "TRANSITIONING"
    structure_details = _market_structure_details(bars, pivots, bias, structure, events)

    return {
        "timeframe": timeframe,
        "status": "OK",
        "bars": len(bars),
        "data_range": [bars[0]["timestamp"], bars[-1]["timestamp"]],
        "current_price": bars[-1]["close"],
        "bias": bias,
        "structure": structure,
        "pivot_sequence": [pivot["type"] for pivot in pivots],
        "pivots": [
            {"timestamp": p["timestamp"], "price": p["price"], "type": p["type"], "side": p["side"]}
            for p in pivots
        ],
        "supply_zones": [zone for zone in zones if zone["type"] == "SUPPLY"],
        "demand_zones": [zone for zone in zones if zone["type"] == "DEMAND"],
        "order_blocks": [zone for zone in zones if zone["is_order_block"]],
        "fvgs": gaps[-30:],
        "liquidity": _liquidity_pools(pivots, timeframe),
        "sweeps": sweeps[-20:],
        "events": events[-20:],
        "latest_event": events[-1] if events else None,
        **structure_details,
        "_zones": zones,
        "_bars": bars,
    }


def _score_zones(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    all_zones: list[dict[str, Any]] = []
    for timeframe in TIMEFRAMES:
        result = results[timeframe]
        zones = result.get("_zones", [])
        higher_biases = [
            results[higher]["bias"]
            for higher in TIMEFRAMES[: TIMEFRAME_RANK[timeframe]]
            if results[higher]["status"] == "OK" and results[higher]["bias"] in ("BULLISH", "BEARISH")
        ]
        current_price = result.get("current_price")
        for zone in zones:
            aligned = bool(higher_biases) and higher_biases[-1] == zone["direction"]
            score = {
                "htf_alignment": 20 if aligned else 0,
                "displacement": 15 if zone["displacement_atr"] >= 2.0 else 8,
                "structure_break": 15 if zone["structural_break"] else 0,
                "fvg": 10 if zone["has_fvg"] else 0,
                "liquidity_sweep": 10 if zone["liquidity_sweep_before_departure"] else 0,
                "freshness": 10 if zone["status"] == "FRESH / UNMITIGATED" else 6 if zone["status"] == "PARTIALLY MITIGATED" else 2 if zone["status"] == "HEAVILY MITIGATED" else 0,
                "clean_base": 5 if zone["clean_base"] else 0,
                "nested": 0,
                "liquidity_proximity": 0,
            }
            zone["score_breakdown"] = score
            zone["score"] = sum(score.values())
            zone["_htf_aligned"] = aligned
            if current_price is not None:
                zone["distance_to_price"] = round(min(abs(current_price - zone["lower"]), abs(current_price - zone["upper"])), 8)
            all_zones.append(zone)

    for zone in all_zones:
        rank = TIMEFRAME_RANK[zone["timeframe"]]
        parents = [
            parent for parent in all_zones
            if TIMEFRAME_RANK[parent["timeframe"]] < rank
            and parent["direction"] == zone["direction"]
            and max(parent["lower"], zone["lower"]) <= min(parent["upper"], zone["upper"])
        ]
        if parents:
            parent = min(parents, key=lambda item: TIMEFRAME_RANK[item["timeframe"]])
            zone["parent_zone"] = parent["id"]
            parent["nested_zones"].append(zone["id"])
            zone["score_breakdown"]["nested"] = 10
            zone["score"] += 10

        tf_result = results[zone["timeframe"]]
        atr = _atr(tf_result.get("_bars", []), max(0, len(tf_result.get("_bars", [])) - 1)) if tf_result.get("_bars") else 0
        pools = tf_result.get("liquidity", [])
        near_pool = atr > 0 and any(
            min(abs(zone["lower"] - pool["level"]), abs(zone["upper"] - pool["level"])) <= 2.5 * atr
            for pool in pools
        )
        if near_pool:
            zone["score_breakdown"]["liquidity_proximity"] = 5
            zone["score"] += 5

    for zone in all_zones:
        zone["score"] = min(100, zone["score"])
        zone.pop("_origin_index", None)
        zone.pop("_origin_end_index", None)
        zone.pop("_departure_index", None)
        zone.pop("_htf_aligned", None)
    return all_zones


def _score_label(score: int) -> str:
    if score >= 90:
        return "EXCEPTIONAL"
    if score >= 80:
        return "VERY STRONG"
    if score >= 70:
        return "STRONG"
    if score >= 60:
        return "MODERATE"
    if score >= 50:
        return "WEAK"
    return "LOW QUALITY"


def _weighted_bias(
    results: dict[str, dict[str, Any]], timeframes: tuple[str, ...]
) -> tuple[str, dict[str, int]]:
    scores = {"BULLISH": 0, "BEARISH": 0}
    directional = []
    for timeframe in timeframes:
        result = results[timeframe]
        bias = result["bias"]
        if result["status"] == "OK" and bias in scores:
            scores[bias] += TIMEFRAME_WEIGHTS[timeframe]
            directional.append((timeframe, bias))
    if not directional:
        return "UNCLEAR", scores
    if scores["BULLISH"] == scores["BEARISH"]:
        return min(directional, key=lambda item: TIMEFRAME_RANK[item[0]])[1], scores
    return ("BULLISH" if scores["BULLISH"] > scores["BEARISH"] else "BEARISH"), scores


def analyze_market(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze supplied timeframe OHLC rows and return a JSON-safe market map."""
    instrument = str(payload.get("instrument") or "")
    raw_timeframes = payload.get("timeframes") or {}
    unknown = set(raw_timeframes) - set(TIMEFRAMES)
    if unknown:
        raise ValueError(f"unsupported timeframe(s): {', '.join(sorted(unknown))}")

    results = {
        timeframe: _analyze_timeframe(timeframe, raw_timeframes.get(timeframe, []))
        for timeframe in TIMEFRAMES
    }
    zones = _score_zones(results)

    for timeframe, result in results.items():
        result["supply_zones"] = [zone for zone in zones if zone["timeframe"] == timeframe and zone["type"] == "SUPPLY"]
        result["demand_zones"] = [zone for zone in zones if zone["timeframe"] == timeframe and zone["type"] == "DEMAND"]
        result["order_blocks"] = [zone for zone in zones if zone["timeframe"] == timeframe and zone["is_order_block"]]
        result.pop("_zones", None)
        result.pop("_bars", None)

    htf_bias, _ = _weighted_bias(results, ("1M", "1W", "1D"))
    ltf_bias, _ = _weighted_bias(results, ("4H", "1H"))
    if htf_bias == "UNCLEAR":
        htf_bias = ltf_bias
    if ltf_bias == "UNCLEAR":
        ltf_bias = htf_bias
    overall_bias, weighted_scores = _weighted_bias(results, TIMEFRAMES)
    directional_results = [
        (tf, results[tf]["bias"], TIMEFRAME_WEIGHTS[tf])
        for tf in TIMEFRAMES
        if results[tf]["status"] == "OK" and results[tf]["bias"] in ("BULLISH", "BEARISH")
    ]
    total_directional_weight = sum(item[2] for item in directional_results)
    conflicting_weight = sum(item[2] for item in directional_results if item[1] != overall_bias)
    if overall_bias == "UNCLEAR":
        alignment = "LOW"
        overall_summary = "INSUFFICIENT DATA: no supplied timeframe has a sufficiently clear directional structure."
    elif conflicting_weight == 0 and total_directional_weight == sum(TIMEFRAME_WEIGHTS.values()):
        alignment = "HIGH"
        overall_summary = f"All five timeframe structures align {overall_bias.lower()}."
    elif conflicting_weight == 0:
        alignment = "MODERATE"
        overall_summary = f"Available directional evidence aligns {overall_bias.lower()}, but some requested timeframes are missing or unclear."
    else:
        alignment = "MODERATE" if conflicting_weight / max(total_directional_weight, 1) <= 0.25 else "LOW"
        conflict_text = ", ".join(f"{tf} {bias}" for tf, bias, _ in directional_results if bias != overall_bias)
        overall_summary = (
            f"Weighted structure is {overall_bias.lower()} ({weighted_scores['BULLISH']} bullish / "
            f"{weighted_scores['BEARISH']} bearish points); conflicting timeframe(s): {conflict_text}."
        )

    ranked_zones = sorted(zones, key=lambda zone: (zone["score"], zone["created_at"]), reverse=True)
    long_zone = next((zone for zone in ranked_zones if zone["direction"] == "BULLISH" and zone["status"] != "INVALIDATED"), None)
    short_zone = next((zone for zone in ranked_zones if zone["direction"] == "BEARISH" and zone["status"] != "INVALIDATED"), None)
    liquidity = [pool for tf in TIMEFRAMES for pool in results[tf]["liquidity"]]
    sweeps = [sweep for tf in TIMEFRAMES for sweep in results[tf]["sweeps"]]

    for zone in zones:
        zone["score_label"] = _score_label(zone["score"])
    for tf in TIMEFRAMES:
        results[tf]["supply_zones"] = [zone for zone in zones if zone["timeframe"] == tf and zone["type"] == "SUPPLY"]
        results[tf]["demand_zones"] = [zone for zone in zones if zone["timeframe"] == tf and zone["type"] == "DEMAND"]
        results[tf]["order_blocks"] = [zone for zone in zones if zone["timeframe"] == tf and zone["is_order_block"]]

    latest_timeframes = [
        (results[tf]["data_range"][-1], tf, results[tf]["current_price"])
        for tf in TIMEFRAMES
        if results[tf]["status"] == "OK"
    ]
    if latest_timeframes:
        analysis_date, price_timeframe, current_price = max(latest_timeframes)
    else:
        analysis_date, price_timeframe, current_price = "", "", None

    def compact_poi(zone: dict[str, Any] | None) -> dict[str, Any] | None:
        if zone is None:
            return None
        return {
            "zone": zone["id"],
            "lower": zone["lower"],
            "upper": zone["upper"],
            "timeframe": zone["timeframe"],
            "score": zone["score"],
            "score_label": zone["score_label"],
            "freshness": zone["status"],
            "confluence": [
                label for label, enabled in (
                    ("ORDER_BLOCK", zone["is_order_block"]),
                    ("FVG", zone["has_fvg"]),
                    ("LIQUIDITY_SWEEP", zone["liquidity_sweep_before_departure"]),
                    ("STRUCTURAL_BREAK", zone["structural_break"]),
                    ("NESTED_ZONE", zone["parent_zone"] is not None),
                ) if enabled
            ],
            "confirmation_required": "POI touch is not entry confirmation; require liquidity response, displacement, a candle-close structure break, and a retest.",
            "invalidation": zone["invalidation"],
        }

    directional_biases = {
        results[tf]["bias"]
        for tf in TIMEFRAMES
        if results[tf]["status"] == "OK" and results[tf]["bias"] in ("BULLISH", "BEARISH")
    }
    if any(results[tf]["structure"] == "TRANSITIONING" for tf in TIMEFRAMES if results[tf]["status"] == "OK"):
        combined_state = "TRANSITIONING"
    elif len(directional_biases) > 1:
        combined_state = "TRANSITIONING"
    elif directional_biases:
        combined_state = "TRENDING"
    elif any(results[tf]["structure"] == "RANGING" for tf in TIMEFRAMES if results[tf]["status"] == "OK"):
        combined_state = "RANGING"
    else:
        combined_state = "UNCLEAR"

    context_timeframe = next(
        (tf for tf in TIMEFRAMES if results[tf]["status"] == "OK" and results[tf]["bias"] == overall_bias and overall_bias in ("BULLISH", "BEARISH")),
        next((tf for tf in reversed(TIMEFRAMES) if results[tf]["status"] == "OK"), None),
    )
    structure_context = results[context_timeframe] if context_timeframe else None
    combined_structure = {
        "state": combined_state,
        "primary_bias": overall_bias,
        "source_timeframe": context_timeframe,
        "protected_high": structure_context["protected_high"] if structure_context else None,
        "protected_low": structure_context["protected_low"] if structure_context else None,
        "invalidation_level": structure_context["invalidation_level"] if structure_context else None,
        "support_levels": structure_context["support_levels"] if structure_context else [],
        "resistance_levels": structure_context["resistance_levels"] if structure_context else [],
        "current_location": structure_context["current_location"] if structure_context else "INSUFFICIENT_STRUCTURAL_EVIDENCE",
        "trend_strength": structure_context["trend_strength"] if structure_context else "UNCLEAR",
        "confidence": structure_context["confidence"] if structure_context else "LOW",
        "primary_scenario": structure_context["primary_scenario"] if structure_context else "INSUFFICIENT DATA",
        "alternative_scenario": structure_context["alternative_scenario"] if structure_context else "INSUFFICIENT DATA",
        "timeframes": {
            tf: {
                "bias": results[tf]["bias"],
                "structure": results[tf]["structure"],
                "pivot_sequence": results[tf]["pivot_sequence"],
                "latest_event": results[tf]["latest_event"],
                "protected_high": results[tf]["protected_high"],
                "protected_low": results[tf]["protected_low"],
                "invalidation_level": results[tf]["invalidation_level"],
                "support_levels": results[tf]["support_levels"],
                "resistance_levels": results[tf]["resistance_levels"],
                "current_location": results[tf]["current_location"],
                "trend_strength": results[tf]["trend_strength"],
                "confidence": results[tf]["confidence"],
            }
            for tf in TIMEFRAMES
        },
    }

    return {
        "instrument": instrument,
        "current_price": current_price,
        "analysis_date": analysis_date,
        "price_timeframe": price_timeframe,
        "available_timeframes": [tf for tf in TIMEFRAMES if results[tf]["status"] == "OK"],
        "timeframes": results,
        "market_structure": combined_structure,
        "ranked_zones": ranked_zones[:15],
        "primary_long_poi": compact_poi(long_zone),
        "primary_short_poi": compact_poi(short_zone),
        "liquidity": {
            "buy_side": [pool for pool in liquidity if pool["side"] == "BUY_SIDE"],
            "sell_side": [pool for pool in liquidity if pool["side"] == "SELL_SIDE"],
            "recent_sweeps": sweeps[-30:],
        },
        "market_bias": {
            "htf": htf_bias,
            "mtf": results["4H"]["bias"],
            "ltf": results["1H"]["bias"],
            "overall": overall_bias,
            "alignment": alignment,
            "weighted_scores": weighted_scores,
            "timeframe_weights": TIMEFRAME_WEIGHTS,
        },
        "final_summary": overall_summary,
        "limitations": [
            "Analysis uses only the supplied OHLC rows; no exchange data or live prices are fetched.",
            "Order blocks are deterministic candidates: the final opposite candle/base before a qualifying displacement and structural break or 2x ATR move.",
            "Equal-high/low detection uses a 0.1% price tolerance; change the configured tolerance for the instrument's tick size and volatility.",
            "Missing timeframes are reported as INSUFFICIENT DATA; no timeframe is synthesized or resampled.",
            "Scores are rule-based confluence scores, not probabilities or trade recommendations.",
        ],
    }

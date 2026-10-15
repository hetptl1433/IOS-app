"""Leakage-aware, five-minute EV charging backtests (never vehicle control)."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .data import _dt, _iso, dataset_summary, hourly_records

UTC = timezone.utc
STEP = timedelta(minutes=5)
STEP_HOURS = 1 / 12

DEFAULT_CONFIG = {
    "arrival": "2026-10-14T18:00:00-05:00",
    "departure": "2026-10-15T07:00:00-05:00",
    "battery_kwh": 75, "initial_soc": 30, "target_soc": 80,
    "charger_kw": 7.2, "efficiency": 0.9, "delivery_cents": 4,
    "max_price_cents": None, "price_policy": "ready", "reserve_minutes": 30,
    "min_session_minutes": 15, "replan_minutes": 5, "forecast_context_hours": 336,
}


def _validated_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object.")
    unknown = set(raw) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("Unknown simulation setting(s): " + ", ".join(sorted(unknown)))
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(raw)
    ranges = {"battery_kwh": (1, 250), "initial_soc": (0, 100), "target_soc": (0, 100),
              "charger_kw": (0.1, 50), "efficiency": (0.5, 1), "delivery_cents": (0, 100),
              "reserve_minutes": (0, 180), "min_session_minutes": (5, 120),
              "replan_minutes": (5, 60), "forecast_context_hours": (24, 8760)}
    integer_keys = {"reserve_minutes", "min_session_minutes", "replan_minutes", "forecast_context_hours"}
    for key, (low, high) in ranges.items():
        value = cfg[key]
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number between {low} and {high}.")
        try:
            value = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{key} must be a number between {low} and {high}.") from exc
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be finite and between {low} and {high}.")
        if key in integer_keys:
            if value != int(value):
                raise ValueError(f"{key} must be a whole number.")
            value = int(value)
        cfg[key] = value
    for key in ("reserve_minutes", "min_session_minutes", "replan_minutes"):
        if cfg[key] % 5:
            raise ValueError(f"{key} must be a multiple of five minutes.")
    if cfg["target_soc"] < cfg["initial_soc"]:
        raise ValueError("target_soc must be at least initial_soc; this simulator cannot discharge the battery.")
    if cfg["price_policy"] not in ("ready", "strict"):
        raise ValueError("price_policy must be ready or strict.")
    if cfg["max_price_cents"] is not None:
        try:
            cap = float(cfg["max_price_cents"])
        except (ValueError, TypeError) as exc:
            raise ValueError("max_price_cents must be a finite number or null.") from exc
        if isinstance(cfg["max_price_cents"], bool) or not math.isfinite(cap) or not -10000 <= cap <= 10000:
            raise ValueError("max_price_cents must be between -10000 and 10000 cents/kWh, or null.")
        cfg["max_price_cents"] = cap
    arrival, departure = _dt(cfg["arrival"], "arrival"), _dt(cfg["departure"], "departure")
    if departure <= arrival or departure - arrival > timedelta(hours=48):
        raise ValueError("Departure must be after arrival and within 48 hours.")
    for key, stamp in (("arrival", arrival), ("departure", departure)):
        if stamp.minute % 5 or stamp.second or stamp.microsecond:
            raise ValueError(f"{key} must fall on a five-minute boundary.")
        cfg[key] = _iso(stamp)
    return cfg, arrival, departure


def _hour(stamp):
    return stamp.replace(minute=0, second=0, microsecond=0)


def _choose_slots(indices, prices, needed_kwh, step_kwh, cap):
    """Cheapest equal-capacity slots, chronological ties favor long blocks.

    All slots in an hour share the same estimated *hourly* price. There is no
    selecting cheap actual five-minute observations inside a billing hour.
    """
    eligible = [i for i in indices if cap is None or prices[i] <= cap]
    count = max(0, math.ceil((needed_kwh - 1e-10) / step_kwh))
    # Hourly ties are stable. A fractional last interval is handled at execution.
    return set(sorted(eligible, key=lambda i: (prices[i], i))[:count])


def _plan(index, timeline, predictions, remaining_grid, cfg, preferred_deadline):
    step_kwh = cfg["charger_kw"] * STEP_HOURS
    cap = cfg["max_price_cents"]
    before_reserve = [i for i in range(index, len(timeline)) if timeline[i] < preferred_deadline]
    all_remaining = list(range(index, len(timeline)))
    # Reserve is a soft timing buffer. Relax it if needed to preserve readiness
    # or if the only affordable slots are later under a strict price policy.
    eligible = [i for i in before_reserve if cap is None or predictions[i] <= cap]
    if len(eligible) * step_kwh + 1e-9 >= remaining_grid:
        return _choose_slots(before_reserve, predictions, remaining_grid, step_kwh, cap)
    eligible_all = [i for i in all_remaining if cap is None or predictions[i] <= cap]
    if cfg["price_policy"] == "strict":
        return _choose_slots(all_remaining, predictions, remaining_grid, step_kwh, cap)
    if len(eligible_all) * step_kwh + 1e-9 >= remaining_grid:
        return _choose_slots(all_remaining, predictions, remaining_grid, step_kwh, cap)
    # Ready policy preserves the goal even when the soft energy threshold is
    # unaffordable. Prefer available cheap slots, then fill the missing capacity.
    chosen = set(eligible_all)
    missing = max(0, remaining_grid - len(chosen) * step_kwh)
    expensive = [i for i in all_remaining if i not in chosen]
    chosen |= _choose_slots(expensive, predictions, missing, step_kwh, None)
    return chosen


def _collapse_schedule(rows, charger_kw):
    schedule = []
    for row in rows:
        if row["energy_kwh"] <= 1e-12:
            continue
        start = _dt(row["interval_start"])
        end = start + timedelta(hours=row["energy_kwh"] / charger_kw)
        if schedule and _dt(schedule[-1]["end"]) == start:
            block = schedule[-1]
            block["end"] = _iso(end)
            block["energy_kwh"] += row["energy_kwh"]
            block["battery_added_kwh"] += row["battery_added_kwh"]
            block["cost"] += row["cost"]
        else:
            schedule.append({"start": _iso(start), "end": _iso(end),
                             "energy_kwh": row["energy_kwh"],
                             "battery_added_kwh": row["battery_added_kwh"], "cost": row["cost"]})
    for block in schedule:
        block["duration_minutes"] = (_dt(block["end"]) - _dt(block["start"])).total_seconds() / 60
        for key in ("energy_kwh", "battery_added_kwh", "cost"):
            block[key] = round(block[key], 6)
    return schedule


def run_simulation(dataset, config, model_ids, *, artifacts_dir=None):
    """Compare unmanaged charging, model-based control and an ideal oracle.

    Forecasts are issued again on the chosen cadence. Each call is supplied only
    complete historical hours actually published by that issue and already
    published day-ahead values. Partial current-hour actual readings are used
    separately to update the expected *whole-hour* average. Execution costs are
    settled against final actual hourly means, including partial charging hours.
    """
    from .models import forecast, get_model_catalog

    cfg, arrival, departure = _validated_config(config)
    if not isinstance(model_ids, list) or any(not isinstance(m, str) for m in model_ids):
        raise ValueError("model_ids must be a list of model identifiers.")
    if len(model_ids) > 6 or len(set(model_ids)) != len(model_ids):
        raise ValueError("Select at most six distinct model strategies.")
    catalog = {m["id"]: m for m in get_model_catalog(artifacts_dir=artifacts_dir)}
    for model_id in model_ids:
        if model_id not in catalog:
            raise ValueError(f"Unknown model: {model_id}.")
        if not catalog[model_id].get("available"):
            raise ValueError(f"{catalog[model_id]['label']} is unavailable: {catalog[model_id].get('reason', 'install or train this model first')}.")
    hours = hourly_records(dataset)
    by_hour = {_dt(row["timestamp"]): row for row in hours}
    points_by_hour = defaultdict(list)
    for point in dataset.get("points", []):
        points_by_hour[_hour(_dt(point["timestamp"]))].append(point)
    stamps = []
    stamp = arrival
    while stamp < departure:
        stamps.append(stamp)
        stamp += STEP
    required_hours = sorted({_hour(t) for t in stamps})
    missing = [h for h in required_hours if h not in by_hour]
    if missing:
        raise ValueError(f"Simulation needs twelve five-minute observations in every billing hour. Missing/incomplete hour: {_iso(missing[0])}. Import a complete session window (including the rest of its first and last hours).")
    available_history = [h for h in hours if _dt(h["available_at"]) <= arrival and _dt(h["timestamp"]) < _hour(arrival)]
    if model_ids and len(available_history) < 24:
        raise ValueError("Model comparison needs at least 24 complete hourly observations available before arrival. Import earlier history or move arrival later; no future prices are used to warm up a model.")
    warnings = list(dataset.get("warnings", []))
    required_battery = cfg["battery_kwh"] * (cfg["target_soc"] - cfg["initial_soc"]) / 100
    required_grid = required_battery / cfg["efficiency"]
    capacity = len(stamps) * cfg["charger_kw"] * STEP_HOURS
    if required_grid > capacity + 1e-9:
        warnings.append("The requested battery target is physically infeasible before departure at this constant charging power. Every strategy will report its remaining shortfall.")
    if cfg["max_price_cents"] is not None:
        warnings.append("The price threshold applies to expected hourly energy prices, excluding delivery. Final billed hourly means may exceed that threshold; a predicted cap is not a billing guarantee.")
    preferred_deadline = departure - timedelta(minutes=cfg["reserve_minutes"])
    if preferred_deadline <= arrival:
        preferred_deadline = departure
        warnings.append("The charging window is shorter than the reserve buffer; the simulator uses the actual departure deadline.")
    actual = [by_hour[_hour(t)]["price"] for t in stamps]
    first_predictions = {}
    strategies = []

    for strategy_id in ["charge_now", *model_ids, "oracle"]:
        is_model = strategy_id not in ("charge_now", "oracle")
        label = catalog[strategy_id]["label"] if is_model else ("Charge immediately" if strategy_id == "charge_now" else "Perfect foresight · ideal lower bound")
        remaining_grid = required_grid
        charged_grid, battery_added, cost = 0.0, 0.0, 0.0
        on, changed_at, switches = False, -100000, 0
        predictions = {}
        plan, first_forecast, rows, events = set(), {}, [], []
        last_issue = -100000
        override_announced = False
        oracle_allocation = {}
        if strategy_id == "oracle":
            # Explicit ideal lower bound: future true hourly prices are known.
            # Oracle has no reserve or switching dwell constraints. It obeys the
            # same physical power/energy/deadline and strict actual-price cap.
            oracle_prices = {i: actual[i] for i in range(len(stamps))}
            oracle_cap = cfg["max_price_cents"] if cfg["price_policy"] == "strict" else None
            budget = required_grid
            eligible = [i for i in range(len(stamps)) if oracle_cap is None or actual[i] <= oracle_cap]
            # Allocate energy in price order, including the fractional most
            # expensive selected slot. Clipping the chronologically last slot
            # instead would not be a true lower bound when cheap hours are late.
            for slot in sorted(eligible, key=lambda i: (actual[i], i)):
                if budget <= 1e-10:
                    break
                oracle_allocation[slot] = min(budget, cfg["charger_kw"] * STEP_HOURS)
                budget -= oracle_allocation[slot]
            plan = set(oracle_allocation)
        for index, now in enumerate(stamps):
            hour = _hour(now)
            if is_model and remaining_grid > 1e-10 and (index == 0 or index - last_issue >= cfg["replan_minutes"] // 5 or now.minute == 0):
                hist = [r for r in hours if _dt(r["available_at"]) <= now and _dt(r["timestamp"]) < hour]
                hist = hist[-cfg["forecast_context_hours"]:]
                future_hours = [h for h in required_hours if h >= hour]
                future = []
                for future_hour in future_hours:
                    record = by_hour[future_hour]
                    da_available = record.get("day_ahead_available_at")
                    known_da = (record.get("day_ahead") is not None and da_available is not None and _dt(da_available) <= now)
                    future.append({"timestamp": record["timestamp"],
                                   "day_ahead": record["day_ahead"] if known_da else None,
                                   "day_ahead_available_at": da_available if known_da else None})
                try:
                    values = forecast(strategy_id, hist, future, artifact_dir=artifacts_dir, as_of=_iso(now))
                except (ValueError, RuntimeError, ImportError) as exc:
                    raise ValueError(f"{label} could not forecast at {_iso(now)}: {exc}") from exc
                if len(values) != len(future) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise ValueError(f"{label} returned invalid or misaligned hourly forecasts.")
                hour_forecasts = {_dt(r["timestamp"]): float(value) for r, value in zip(future, values)}
                if not first_forecast:
                    first_forecast = dict(hour_forecasts)
                    first_predictions[strategy_id] = dict(hour_forecasts)
                # Known observations are not a forecast of the next interval.
                # Blend only already-published readings into current-hour mean.
                observed = [p["price"] for p in points_by_hour[hour]
                            if _dt(p.get("published_at") or _iso(_dt(p["timestamp"]) + STEP)) <= now
                            and _dt(p["timestamp"]) + STEP <= now]
                if observed:
                    hour_forecasts[hour] = (sum(observed) + (12 - len(observed)) * hour_forecasts[hour]) / 12
                predictions = {i: hour_forecasts[_hour(stamps[i])] for i in range(index, len(stamps))}
                plan = _plan(index, stamps, predictions, remaining_grid, cfg, preferred_deadline)
                last_issue = index
            predicted = predictions.get(index) if is_model else (actual[index] if strategy_id == "oracle" else None)
            want = remaining_grid > 1e-10 and (strategy_id == "charge_now" or index in plan)
            if is_model and remaining_grid > 1e-10:
                cap_exceeded = cfg["max_price_cents"] is not None and predicted > cfg["max_price_cents"]
                # Reserve makes latest-start decisions conservative. If reserve
                # is infeasible, remaining physical capacity is used instead.
                effective_deadline = preferred_deadline
                remaining_slots = sum(t < effective_deadline for t in stamps[index:])
                if remaining_slots * cfg["charger_kw"] * STEP_HOURS + 1e-9 < remaining_grid:
                    effective_deadline = departure
                    remaining_slots = len(stamps) - index
                urgent = remaining_grid > max(0, remaining_slots - 1) * cfg["charger_kw"] * STEP_HOURS + 1e-9
                if cfg["price_policy"] == "strict" and cap_exceeded:
                    want = False  # A hard expected cap overrides minimum-on dwell.
                elif cfg["price_policy"] == "ready" and urgent:
                    want = True
                elif want != on and index - changed_at < cfg["min_session_minutes"] // 5:
                    want = on
                if want and cap_exceeded and cfg["price_policy"] == "ready" and not override_announced:
                    events.append({"timestamp": _iso(now), "type": "price_override", "message": "Readiness policy exceeded the soft expected energy-price threshold to preserve the charging target."})
                    override_announced = True
            # Goal completion always overrides dwell. No strategy overcharges.
            if remaining_grid <= 1e-10:
                want = False
            if want != on:
                switches += 1
                on, changed_at = want, index
                events.append({"timestamp": _iso(now), "type": "start" if on else "pause", "message": "Charging started." if on else ("Charging target reached." if remaining_grid <= 1e-10 else "Charging paused by schedule or price policy.")})
            grid_kwh = min(remaining_grid, cfg["charger_kw"] * STEP_HOURS) if on else 0.0
            if strategy_id == "oracle":
                grid_kwh = min(grid_kwh, oracle_allocation.get(index, 0.0))
            interval_battery = grid_kwh * cfg["efficiency"]
            interval_cost = grid_kwh * (actual[index] + cfg["delivery_cents"]) / 100
            remaining_grid = max(0.0, remaining_grid - grid_kwh)
            charged_grid += grid_kwh
            battery_added += interval_battery
            cost += interval_cost
            soc = cfg["initial_soc"] + battery_added / cfg["battery_kwh"] * 100
            rows.append({"timestamp": _iso(now + STEP), "interval_start": _iso(now),
                         "soc": round(min(cfg["target_soc"], soc), 6), "charging": grid_kwh > 0,
                         "price_actual": actual[index], "price_predicted": predicted,
                         "power_kw": cfg["charger_kw"] if grid_kwh > 0 else 0,
                         "energy_kwh": grid_kwh, "battery_added_kwh": interval_battery,
                         "cost": interval_cost, "cumulative_cost": cost})
            if on and (remaining_grid <= 1e-10 or grid_kwh < cfg["charger_kw"] * STEP_HOURS - 1e-10):
                # Fractional intervals end as soon as their allocated energy is
                # delivered, rather than five minutes later at the next tick.
                switches += 1
                on, changed_at = False, index
                stop_at = now + timedelta(hours=grid_kwh / cfg["charger_kw"])
                complete = remaining_grid <= 1e-10
                events.append({"timestamp": _iso(stop_at), "type": "complete" if complete else "pause",
                               "message": "Charging target reached." if complete else "Ideal oracle interval allocation delivered."})
        if on:
            switches += 1
            stop_at = departure
            if remaining_grid <= 1e-10:
                active_rows = [r for r in rows if r["energy_kwh"] > 0]
                if active_rows:
                    last = active_rows[-1]
                    stop_at = _dt(last["interval_start"]) + timedelta(hours=last["energy_kwh"] / cfg["charger_kw"])
            events.append({"timestamp": _iso(stop_at), "type": "complete" if remaining_grid <= 1e-10 else "departure", "message": "Charging target reached." if remaining_grid <= 1e-10 else "Departure reached; charging session ended."})
        ready = remaining_grid * cfg["efficiency"] < 1e-7
        if not ready:
            events.append({"timestamp": _iso(departure), "type": "shortfall", "message": f"Target missed by {remaining_grid * cfg['efficiency']:.2f} battery kWh under this schedule and policy."})
        mae = None
        if first_forecast:
            mae = sum(abs(first_forecast[h] - by_hour[h]["price"]) for h in first_forecast) / len(first_forecast)
        strategies.append({"id": strategy_id, "label": label, "cost": round(cost, 6),
                           "energy_kwh": round(charged_grid, 6), "battery_added_kwh": round(battery_added, 6),
                           "final_soc": round(min(cfg["target_soc"], cfg["initial_soc"] + battery_added / cfg["battery_kwh"] * 100), 6),
                           "ready": ready, "shortfall_kwh": round(remaining_grid * cfg["efficiency"], 6),
                           "savings": None, "savings_pct": None, "mae": round(mae, 6) if mae is not None else None,
                           "switches": switches, "schedule": _collapse_schedule(rows, cfg["charger_kw"]),
                           "timeline": rows, "events": events,
                           "is_oracle": strategy_id == "oracle", "is_baseline": strategy_id == "charge_now"})
    baseline_cost = strategies[0]["cost"]
    for strategy in strategies:
        strategy["savings"] = round(baseline_cost - strategy["cost"], 6)
        # At zero or negative baseline supply cost percentage savings misleads.
        strategy["savings_pct"] = round(100 * strategy["savings"] / baseline_cost, 4) if baseline_cost > 1e-9 else None
    # Never name a cheap undercharged run a winner. Oracle is an explanatory
    # lower bound, not a candidate deployed forecasting strategy.
    strict_threshold = cfg["price_policy"] == "strict" and cfg["max_price_cents"] is not None
    candidates = [s for s in strategies if s["ready"] and not s["is_oracle"]
                  and not (strict_threshold and s["is_baseline"])]
    if strict_threshold:
        warnings.append("The unmanaged charge-immediately baseline ignores the strict expected-price threshold, so it is not eligible to win this comparison.")
    winner = min(candidates, key=lambda s: (s["cost"], s["switches"])) if candidates else None
    if winner is None:
        warnings.append("No eligible deployable strategy reached the charging target under the chosen policy. Review shortfalls, the expected-price threshold, power and departure time.")
    if any(not s["ready"] for s in strategies if not s["is_oracle"]):
        warnings.append("Costs and savings for strategies that miss the target are not equal-energy comparisons; readiness is required to win.")
    if baseline_cost <= 1e-9:
        warnings.append("Percentage savings is undefined for a zero or negative baseline cost; dollar differences remain available.")
    chart = [{"timestamp": _iso(h), "actual": by_hour[h]["price"], "day_ahead": by_hour[h].get("day_ahead"),
              "forecasts": {m: preds[h] for m, preds in first_predictions.items() if h in preds}} for h in required_hours]
    events = [{**e, "strategy_id": s["id"], "strategy_label": s["label"]} for s in strategies for e in s["events"]]
    events.sort(key=lambda e: (e["timestamp"], e["strategy_id"]))
    assumptions = [
        "Simulation only: no Tesla credentials, vehicle commands, charger hardware or internet connectivity are exercised.",
        "Prices are cents per grid kWh. Costs are dollars, settled using the final mean of all twelve five-minute observations in each billing hour plus the configured delivery adder. Five-minute spot-price arbitrage is not modeled.",
        "Constant grid charging power and fixed efficiency; grid kWh differs from battery kWh. No battery taper, temperature derating, command latency, vehicle sleep, charger faults or demand/capacity/tax charges are modeled.",
        "Models receive only complete historical hours available at each issue and already-published day-ahead values. Current-hour estimates blend only readings published by that issue; future final actual prices are used solely for settlement and evaluation.",
        "Forecast MAE uses the initial issued hourly forecast, before rolling current-hour updates. Chart forecast lines show that initial issue; charging decisions replan throughout the session.",
        "Minimum on/off residence reduces rapid switching; target completion, a strict expected price threshold and readiness urgency can override it. Reserve time is a soft early-finish buffer and relaxes if necessary.",
        "Charge immediately is an unmanaged baseline and ignores price thresholds. Perfect foresight is a non-deployable ideal cost lower bound with true future hourly prices, no reserve/dwell constraints, the same power/target/deadline, and an actual-price cap in strict mode.",
        "The winner excludes the oracle and any strategy that misses its target. Under a strict non-null threshold, the unmanaged baseline is also ineligible because it ignores that policy. Savings on an undercharged session do not demonstrate model quality.",
    ]
    return {"config": cfg, "dataset": dataset_summary(dataset), "assumptions": assumptions,
            "warnings": list(dict.fromkeys(warnings)), "strategies": strategies, "chart": chart, "events": events,
            "summary": {"winner_id": winner["id"] if winner else None,
                        "best_cost": winner["cost"] if winner else None, "baseline_cost": baseline_cost,
                        "savings": winner["savings"] if winner else None,
                        "savings_pct": winner["savings_pct"] if winner else None}}

import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sparc_lab.data import parse_csv_dataset
from sparc_lab.simulator import run_simulation

UTC = timezone.utc
ARRIVAL = datetime(2026, 10, 2, tzinfo=UTC)


def fixture(hours=30, future=None, partial_prices=None, da_at="2026-10-01T00:00:00Z"):
    start = ARRIVAL - timedelta(hours=24)
    rows = ["timestamp,price,day_ahead,day_ahead_available_at"]
    for hour in range(hours):
        offset = hour - 24
        price = (future or {}).get(offset, 5)
        for minute in range(0, 60, 5):
            stamp = start + timedelta(hours=hour, minutes=minute)
            reading = (partial_prices or {}).get((offset, minute), price)
            rows.append(f"{stamp.isoformat()},{reading},{price},{da_at}")
    return parse_csv_dataset("\n".join(rows))


def config(**kwargs):
    result = {"arrival": ARRIVAL.isoformat(), "departure": (ARRIVAL + timedelta(hours=3)).isoformat(),
              "battery_kwh": 60, "initial_soc": 20, "target_soc": 30,
              "charger_kw": 6, "efficiency": 1, "delivery_cents": 0,
              "reserve_minutes": 0, "min_session_minutes": 5, "replan_minutes": 5}
    result.update(kwargs)
    return result


def strategies(result):
    return {s["id"]: s for s in result["strategies"]}


def model_patch(fn):
    catalog = [{"id": "test-model", "label": "Test forecaster", "available": True}]
    return patch("sparc_lab.models.get_model_catalog", return_value=catalog), patch("sparc_lab.models.forecast", side_effect=fn)


class SimulatorTests(unittest.TestCase):
    def test_hourly_billing_no_five_minute_arbitrage(self):
        # Charging only during the first 5 cheap minutes is still billed at the
        # completed whole-hour mean, not the observed cheap five-minute price.
        prices = {(0, minute): (0 if minute == 0 else 12) for minute in range(0, 60, 5)}
        result = run_simulation(fixture(partial_prices=prices), config(target_soc=20 + 0.5/60*100), [])
        baseline = strategies(result)["charge_now"]
        self.assertAlmostEqual(baseline["energy_kwh"], 0.5)
        self.assertAlmostEqual(baseline["cost"], 0.5 * 11 / 100)

    def test_efficiency_and_fractional_last_interval(self):
        result = run_simulation(fixture(), config(target_soc=21, charger_kw=7.2, efficiency=0.9), [])
        baseline = strategies(result)["charge_now"]
        self.assertAlmostEqual(baseline["battery_added_kwh"], 0.6, places=5)
        self.assertAlmostEqual(baseline["energy_kwh"], 0.6 / 0.9, places=5)
        self.assertAlmostEqual(baseline["final_soc"], 21)
        charged_rows = [r for r in baseline["timeline"] if r["charging"]]
        self.assertLess(charged_rows[-1]["energy_kwh"], 7.2/12)
        self.assertEqual(len(baseline["schedule"]), 1)

    def test_oracle_is_explicit_lower_bound(self):
        result = run_simulation(fixture(future={0: 20, 1: 2, 2: 7}), config(), [])
        all_strategies = strategies(result)
        self.assertAlmostEqual(all_strategies["charge_now"]["cost"], 1.2)
        self.assertAlmostEqual(all_strategies["oracle"]["cost"], 0.12)
        self.assertEqual(result["summary"]["winner_id"], "charge_now")
        self.assertTrue(all_strategies["oracle"]["is_oracle"])
        self.assertEqual(all_strategies["oracle"]["schedule"][0]["start"], "2026-10-02T01:00:00Z")

    def test_forecaster_never_receives_actual_future_or_unpublished_da(self):
        calls = []
        dataset = fixture(da_at="2026-10-02T02:00:00Z")
        def predict(model, history, future, **kwargs):
            calls.append((history, future))
            for row in future:
                self.assertEqual(set(row), {"timestamp", "day_ahead", "day_ahead_available_at"})
                self.assertNotIn("price", row)
            return [5.0] * len(future)
        p1, p2 = model_patch(predict)
        with p1, p2:
            run_simulation(dataset, config(), ["test-model"])
        self.assertTrue(calls)
        self.assertTrue(all(r["day_ahead"] is None for r in calls[0][1]))
        self.assertTrue(all(r["timestamp"] < "2026-10-02T00:00:00Z" for r in calls[0][0]))
        self.assertTrue(all(r["available_at"] <= "2026-10-02T00:00:00Z" for r in calls[0][0]))

    def test_current_hour_nowcast_uses_published_readings_only(self):
        dataset = fixture(partial_prices={(0, 0): 100, (0, 5): -1000})
        p1, p2 = model_patch(lambda m, h, f, **kw: [4.0] * len(f))
        with p1, p2:
            result = run_simulation(dataset, config(target_soc=49, max_price_cents=None), ["test-model"])
        rows = strategies(result)["test-model"]["timeline"]
        self.assertEqual(rows[0]["price_predicted"], 4)
        # At 00:05, the 00:00 interval is known; 00:05's -1000 is not yet known.
        self.assertAlmostEqual(rows[1]["price_predicted"], (100 + 11 * 4) / 12)
        self.assertEqual(result["chart"][0]["forecasts"]["test-model"], 4)

    def test_mutating_unobserved_future_does_not_change_first_decision(self):
        datasets = [fixture(future={0: 3, 1: 100}), fixture(future={0: 500, 1: -30})]
        observed = []
        for dataset in datasets:
            p1, p2 = model_patch(lambda m, h, f, **kw: [4.0] * len(f))
            with p1, p2:
                result = run_simulation(dataset, config(), ["test-model"])
            observed.append(strategies(result)["test-model"]["timeline"][0]["charging"])
        self.assertEqual(observed[0], observed[1])

    def test_strict_cap_reports_shortfall_and_cannot_win(self):
        p1, p2 = model_patch(lambda m, h, f, **kw: [30.0] * len(f))
        with p1, p2:
            result = run_simulation(fixture(future={0: 30, 1: 30, 2: 30}), config(max_price_cents=10, price_policy="strict"), ["test-model"])
        model = strategies(result)["test-model"]
        self.assertFalse(model["ready"])
        self.assertEqual(model["energy_kwh"], 0)
        self.assertAlmostEqual(model["shortfall_kwh"], 6)
        self.assertIsNone(result["summary"]["winner_id"])
        self.assertTrue(any("not eligible" in w for w in result["warnings"]))

    def test_ready_policy_overrides_soft_cap_to_meet_deadline(self):
        p1, p2 = model_patch(lambda m, h, f, **kw: [30.0] * len(f))
        with p1, p2:
            result = run_simulation(fixture(future={0: 30, 1: 30, 2: 30}), config(max_price_cents=10, price_policy="ready"), ["test-model"])
        model = strategies(result)["test-model"]
        self.assertTrue(model["ready"])
        self.assertTrue(any(e["type"] == "price_override" for e in model["events"]))

    def test_rolling_prediction_chooses_cheaper_hour(self):
        def predict(m, history, future, **kw):
            return [2 if r["timestamp"] == "2026-10-02T01:00:00Z" else 20 for r in future]
        p1, p2 = model_patch(predict)
        with p1, p2:
            result = run_simulation(fixture(future={0: 20, 1: 2, 2: 20}), config(), ["test-model"])
        model = strategies(result)["test-model"]
        self.assertTrue(model["ready"])
        self.assertAlmostEqual(model["cost"], 0.12)
        self.assertEqual(result["summary"]["winner_id"], "test-model")
        self.assertAlmostEqual(model["mae"], 0)

    def test_zero_target_never_charges_and_negative_cost_has_no_percentage(self):
        zero = run_simulation(fixture(), config(target_soc=20), [])
        self.assertTrue(all(s["energy_kwh"] == 0 for s in zero["strategies"]))
        self.assertIsNone(zero["summary"]["savings_pct"])
        negative = run_simulation(fixture(future={0: -5, 1: -2, 2: 1}), config(), [])
        self.assertLess(negative["summary"]["baseline_cost"], 0)
        self.assertIsNone(negative["summary"]["savings_pct"])

    def test_shortfall_is_physical_not_hidden(self):
        result = run_simulation(fixture(), config(target_soc=90, charger_kw=1), [])
        self.assertTrue(all(not s["ready"] for s in result["strategies"]))
        self.assertIsNone(result["summary"]["winner_id"])
        self.assertAlmostEqual(strategies(result)["charge_now"]["battery_added_kwh"], 3)

    def test_gap_and_cold_start_rejected(self):
        dataset = fixture()
        dataset["points"].pop(24 * 12 + 1)
        with self.assertRaisesRegex(ValueError, "Missing/incomplete"):
            run_simulation(dataset, config(), [])
        dataset = fixture()
        dataset["points"] = dataset["points"][24 * 12:]
        p1, p2 = model_patch(lambda m, h, f, **kw: [4] * len(f))
        with p1, p2:
            with self.assertRaisesRegex(ValueError, "at least 24"):
                run_simulation(dataset, config(), ["test-model"])


    def test_oracle_fractional_energy_is_allocated_by_price_not_execution_order(self):
        # 6.25 kWh: an entire late cheap hour plus 0.25 expensive early kWh.
        # Charging the full expensive 5-min slot then clipping a cheap late slot
        # would produce the wrong claimed lower bound.
        result = run_simulation(fixture(future={0: 20, 1: 1, 2: 30}), config(target_soc=20 + 6.25 / 60 * 100), [])
        oracle = strategies(result)["oracle"]
        self.assertAlmostEqual(oracle["cost"], (6 * 1 + 0.25 * 20) / 100)
        early = [r for r in oracle["timeline"] if r["interval_start"].startswith("2026-10-02T00:")]
        self.assertAlmostEqual(sum(r["energy_kwh"] for r in early), 0.25)
        self.assertTrue(oracle["ready"])

    def test_minimum_on_and_off_residence_reduce_switching(self):
        # The current-hour estimate will rise after five minutes, but a soft
        # threshold/readiness policy does not turn an ordinary 15-min on block
        # into a five-minute block merely because that forecast moved.
        def predict(m, h, future, **kw):
            minute = datetime.fromisoformat(kw["as_of"].replace("Z", "+00:00")).minute
            current = future[0]["timestamp"]
            return [1 if minute == 0 or r["timestamp"] != current else 100 for r in future]
        p1, p2 = model_patch(predict)
        with p1, p2:
            result = run_simulation(fixture(), config(target_soc=50, min_session_minutes=15), ["test-model"])
        rows = strategies(result)["test-model"]["timeline"]
        self.assertTrue(all(r["charging"] for r in rows[:3]))
        # The first model call has the actual issue, subsequent calls advance.
        self.assertEqual(rows[0]["interval_start"], "2026-10-02T00:00:00Z")

    def test_strict_expected_cap_overrides_minimum_on_residence(self):
        def predict(m, h, future, **kw):
            high = kw["as_of"] > "2026-10-02T00:00:00Z"
            return [100 if high else 1] * len(future)
        p1, p2 = model_patch(predict)
        with p1, p2:
            result = run_simulation(fixture(), config(max_price_cents=10, price_policy="strict", min_session_minutes=30), ["test-model"])
        rows = strategies(result)["test-model"]["timeline"]
        self.assertTrue(rows[0]["charging"])
        self.assertFalse(rows[1]["charging"])
        self.assertFalse(strategies(result)["test-model"]["ready"])

    def test_reserve_aims_for_early_completion_but_relaxes_when_infeasible(self):
        def predict(m, h, future, **kw):
            return [1 if r["timestamp"] == "2026-10-02T02:00:00Z" else 5 for r in future]
        p1, p2 = model_patch(predict)
        with p1, p2:
            result = run_simulation(fixture(future={2:1}), config(reserve_minutes=60), ["test-model"])
        self.assertTrue(strategies(result)["test-model"]["ready"])
        self.assertLessEqual(strategies(result)["test-model"]["schedule"][-1]["end"], "2026-10-02T02:00:00Z")
        p1, p2 = model_patch(predict)
        with p1, p2:
            result = run_simulation(fixture(future={2:1}), config(reserve_minutes=60, target_soc=50), ["test-model"])
        # Full three-hour window is physically necessary, so reserve relaxes.
        self.assertTrue(strategies(result)["test-model"]["ready"])
        self.assertEqual(strategies(result)["test-model"]["schedule"][-1]["end"], "2026-10-02T03:00:00Z")

    def test_delayed_current_hour_reading_is_not_used_early(self):
        dataset = fixture(partial_prices={(0, 0): 100})
        dataset["points"][24*12]["published_at"] = "2026-10-02T00:15:00Z"
        p1, p2 = model_patch(lambda m, h, f, **kw: [4.0] * len(f))
        with p1, p2:
            result = run_simulation(dataset, config(target_soc=49), ["test-model"])
        rows = strategies(result)["test-model"]["timeline"]
        self.assertEqual(rows[1]["price_predicted"], 4)
        self.assertGreater(rows[3]["price_predicted"], 4)

    def test_invalid_config_rejected(self):
        for changes in ({"efficiency": 0}, {"charger_kw": float("nan")}, {"initial_soc": 40, "target_soc": 20},
                        {"arrival": "2026-10-02T00:02:00Z"}, {"departure": "2026-10-08T00:00:00Z"},
                        {"replan_minutes": 7}, {"price_policy": "cheap"}, {"max_price_cents": float("inf")}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    run_simulation(fixture(), config(**changes), [])

    def test_bad_model_outputs_rejected(self):
        p1, p2 = model_patch(lambda m, h, f, **kw: [float("nan")] * len(f))
        with p1, p2:
            with self.assertRaisesRegex(ValueError, "invalid or misaligned"):
                run_simulation(fixture(), config(), ["test-model"])


if __name__ == "__main__":
    unittest.main()

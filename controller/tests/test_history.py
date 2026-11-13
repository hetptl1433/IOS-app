import base64
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sparc_controller.history import HistoryStore, immediate_buckets, instant, session_id
from sparc_controller.pricing import ComEdPricing

UTC = timezone.utc


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root/"state"
        self.logs = self.root/"logs"
        self.now = instant("2026-11-06T14:00:00Z")
        self.store = HistoryStore(self.state, self.logs, lambda: self.now)
        self.config = {"campaignId": "campaign-2026-11-05", "sessionId": "campaign-2026-11-05:2026-11-05",
                       "nightDate": "2026-11-05", "initialBatteryPercent": 20, "targetBatteryPercent": 80,
                       "start": "2026-11-05T22:00:00-06:00", "departure": "2026-11-06T07:00:00-06:00"}
        self.settings = {"overnightRun": self.config, "virtualCarEnabled": True,
                         "virtualCarState": {"batteryPercent": 80, "energyKWh": 23.0,
                                             "energyByHour": {"2026-11-06T06:00:00+00:00": 11.5,
                                                              "2026-11-06T07:00:00+00:00": 11.5},
                                             # These example-model amounts must never enter history.
                                             "cost": 999, "immediateCost": 999}}
        self.pricing = ComEdPricing(lambda: self.now)
        for hour, cents in ((4, 10), (5, 10), (6, 1), (7, 2)):
            self.pricing.settled_hourly[int(instant(f"2026-11-06T{hour:02}:00:00Z").timestamp())] = cents

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def log(self, folder, config=None, energy=23, cost=0.345, final=True, battery=80, first="2026-11-06T12:50:00Z"):
        config = copy.deepcopy(config or self.config)
        root = self.logs/folder
        self.save(root/"manifest.json", config)
        latest = {"timestamp": config["departure"], "observedAt": config["departure"], "batteryPercent": battery,
                  "energyKWh": energy, "energyCostUSD": cost, "costAvailable": cost is not None,
                  "costFinal": final, "unpricedEnergyKWh": 0 if cost is not None else energy}
        self.save(root/"summary.json", {"manifest": config, "campaignId": config.get("campaignId"),
                                       "sessionId": config.get("sessionId"), "latest": latest,
                                       "firstTargetObservedAt": first, "finishedAt": config["departure"]})
        return root

    def test_live_capture_is_durable_and_reprices_real_ledger_after_restart(self):
        self.now = instant("2026-11-06T12:59:00Z")
        self.assertEqual(self.store.capture(self.settings, self.pricing), 1)
        self.store = HistoryStore(self.state, self.logs, lambda: self.now)
        row = self.store.view({}, None)["sessions"][0]
        self.assertAlmostEqual(row["costUSD"], 0.345)
        self.assertAlmostEqual(row["baselineCostUSD"], 2.3)
        self.assertAlmostEqual(row["savingsUSD"], 1.955)
        self.assertTrue(row["costFinal"])
        self.assertTrue(row["targetReachedByDeadline"])
        self.assertEqual(row["id"], session_id(self.config))
        stored = json.loads(next((self.state/"history").glob("*.json")).read_text())
        self.assertEqual(stored["energyByHour"], self.settings["virtualCarState"]["energyByHour"])
        self.assertNotIn("999", json.dumps(stored))

    def test_capture_without_pricing_preserves_known_costs_and_never_imports_example_cost(self):
        self.store.capture(self.settings, self.pricing, force=True)
        self.now += timedelta(minutes=1)
        self.store.capture(self.settings, None, force=True)
        self.assertEqual(self.store.view({}, None)["sessions"][0]["costUSD"], 0.345)
        other = HistoryStore(self.root/"empty-state", self.logs, lambda: self.now)
        other.capture(self.settings, None)
        row = other.view({}, None)["sessions"][0]
        self.assertIsNone(row["costUSD"])
        self.assertIsNone(row["baselineCostUSD"])
        self.assertIsNone(row["savingsUSD"])
        self.assertTrue(row["provisional"])

    def test_changed_energy_does_not_reuse_a_stale_cost_total(self):
        self.store.capture(self.settings, self.pricing, force=True)
        changed = copy.deepcopy(self.settings)
        changed["virtualCarState"]["energyKWh"] = 24
        changed["virtualCarState"]["energyByHour"]["2026-11-06T08:00:00+00:00"] = 1
        self.store.capture(changed, None, force=True)
        row = self.store.view({}, None)["sessions"][0]
        self.assertEqual(row["energyKWh"], 24)
        self.assertIsNone(row["costUSD"])
        self.assertIsNone(row["baselineCostUSD"])

    def test_backfills_legacy_and_campaign_logs_without_duplicate_sessions(self):
        legacy = {key: value for key, value in self.config.items() if key not in ("campaignId", "sessionId", "nightDate")}
        legacy.update(start="2026-11-04T20:00:00-06:00", departure="2026-11-05T08:00:00-06:00")
        self.log("overnight-2026-11-04", legacy, cost=0.0, first="2026-11-05T12:00:00Z")
        self.log("campaign-2026-11-05/night-2026-11-05")
        self.log("campaign-copy/night-2026-11-05")
        self.store.capture({}, self.pricing, force=True)
        result = self.store.view({}, self.pricing)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(len({item["id"] for item in result["sessions"]}), 2)
        legacy_row = next(item for item in result["sessions"] if item["nightDate"] == "2026-11-04")
        self.assertEqual(legacy_row["costUSD"], 0)
        self.assertIsNone(legacy_row["baselineCostUSD"])
        self.assertTrue(legacy_row["targetReachedByDeadline"])
        self.assertEqual(result["week"]["unpricedSessions"], 1)
        self.assertIsNone(result["week"]["savingsUSD"])

    def test_imports_last_bounded_snapshot_if_summary_is_missing_or_old(self):
        root = self.logs/"campaign-2026-11-05/night-2026-11-05"
        self.save(root/"manifest.json", self.config)
        state = {"overnight": self.config, "batteryPercent": 77,
                 "snapshot": {"updatedAt": "2026-11-06T13:00:01Z", "energyKWh": 20,
                              "estimatedCost": -0.5, "costAvailable": True, "costFinal": False,
                              "unpricedEnergyKWh": 0}}
        with (root/"snapshots.jsonl").open("wb") as stream:
            stream.seek(55*1024*1024)  # sparse file: importer must not load the whole log
            stream.write(b"\n"+json.dumps({"recordedAt": "2026-11-06T13:00:01Z", "data": state}).encode()+b"\n{partial")
        with patch.object(self.store, "_last_snapshot", wraps=self.store._last_snapshot) as tail:
            one = self.store.view({}, self.pricing)
            self.store.view({}, self.pricing)
        self.assertEqual(tail.call_count, 1)
        self.assertEqual(one["sessions"][0]["costUSD"], -0.5)
        self.assertEqual(one["sessions"][0]["energyKWh"], 20)
        self.assertFalse(one["sessions"][0]["targetReachedByDeadline"])

    def test_wrong_campaign_tail_cannot_supply_another_nights_telemetry(self):
        root = self.logs/"campaign-2026-11-05/night-2026-11-05"
        self.save(root/"manifest.json", self.config)
        state = {"overnight": dict(self.config, sessionId="other"), "batteryPercent": 99,
                 "snapshot": {"energyKWh": 99, "estimatedCost": 2, "costAvailable": True}}
        (root/"snapshots.jsonl").write_text(json.dumps({"recordedAt": self.now.isoformat(), "data": state})+"\n")
        self.assertEqual(self.store.view({}, self.pricing)["sessions"], [])

    def test_zero_energy_and_zero_prices_are_available_without_fabricating_savings_percent(self):
        zero = copy.deepcopy(self.settings)
        zero["virtualCarState"].update(energyKWh=0, energyByHour={}, batteryPercent=20)
        row = self.store.view(zero, None)["sessions"][0]
        self.assertEqual(row["costUSD"], 0)
        self.assertEqual(row["baselineCostUSD"], 0)
        self.assertEqual(row["savingsUSD"], 0)
        self.assertIsNone(row["savingsPercent"])
        self.pricing.settled_hourly = {stamp: 0 for stamp in self.pricing.settled_hourly}
        row = self.store.view(self.settings, self.pricing)["sessions"][0]
        self.assertEqual(row["costUSD"], 0)
        self.assertIsNone(row["savingsPercent"])

    def test_negative_prices_and_negative_savings_remain_signed(self):
        for hour, cents in ((4, -10), (5, -10), (6, 1), (7, 2)):
            self.pricing.settled_hourly[int(instant(f"2026-11-06T{hour:02}:00:00Z").timestamp())] = cents
        row = self.store.view(self.settings, self.pricing)["sessions"][0]
        self.assertEqual(row["baselineCostUSD"], -2.3)
        self.assertEqual(row["savingsUSD"], -2.645)
        self.assertLess(row["savingsPercent"], 0)

    def test_incomplete_hourly_ledger_or_missing_prices_never_looks_fully_priced(self):
        partial = copy.deepcopy(self.settings)
        partial["virtualCarState"]["energyByHour"].pop("2026-11-06T07:00:00+00:00")
        row = self.store.view(partial, self.pricing)["sessions"][0]
        self.assertIsNone(row["costUSD"])
        self.assertEqual(row["unpricedEnergyKWh"], 23)
        self.assertIsNone(row["savingsUSD"])
        self.assertFalse(row["costFinal"])

    def test_week_buckets_use_chicago_start_day_across_utc_monday(self):
        self.now = instant("2026-11-09T06:10:00Z")  # Monday 00:10 Chicago
        sunday = dict(self.config, campaignId="week", sessionId="sunday", nightDate="2026-11-08",
                      start="2026-11-09T05:30:00Z", departure="2026-11-09T05:40:00Z")
        monday = dict(self.config, campaignId="week", sessionId="monday", nightDate="2026-11-09",
                      start="2026-11-09T06:05:00Z", departure="2026-11-09T06:08:00Z")
        self.log("campaign-week/night-2026-11-08", sunday, energy=1, cost=0.1, first=None)
        self.log("campaign-week/night-2026-11-09", monday, energy=2, cost=0.2, first=None)
        result = self.store.view({}, None)
        self.assertEqual(result["week"]["start"], "2026-11-09")
        self.assertEqual(result["week"]["end"], "2026-11-16")
        self.assertEqual(result["week"]["energyKWh"], 2)
        self.assertEqual(result["week"]["days"][0]["energyKWh"], 2)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(len(result["week"]["days"]), 7)

    def test_pagination_caps_payload_and_week_includes_every_page(self):
        for index in range(10):
            day = 2+index % 5
            config = dict(self.config, campaignId="pages", sessionId=str(index), nightDate=f"2026-11-{day:02}",
                          start=f"2026-11-{day:02}T20:00:00-06:00", departure=f"2026-11-{day+1:02}T07:00:00-06:00")
            self.log(f"campaign-{index}/night-2026-11-{day:02}", config, energy=1, first=None)
        first = self.store.view({}, None)
        second = self.store.view({}, None, cursor=first["nextCursor"])
        self.assertEqual(len(first["sessions"]), 8)
        self.assertEqual(len(second["sessions"]), 2)
        self.assertEqual(first["week"]["energyKWh"], 10)
        self.assertEqual(first["week"], second["week"])
        self.assertIsNone(second["nextCursor"])
        # Includes encrypted-envelope overhead and base64 expansion headroom.
        self.assertLess(len(base64.b64encode(json.dumps(first).encode()))+1024, 16384)
        for kwargs in ({"limit": 9}, {"limit": 0}, {"cursor": -1}, {"cursor": True}):
            with self.assertRaises(ValueError):
                self.store.view({}, None, **kwargs)

    def test_capture_throttles_but_finalization_and_force_flush(self):
        self.now = instant("2026-11-06T12:50:00Z")
        self.assertEqual(self.store.capture(self.settings, self.pricing), 1)
        self.now += timedelta(seconds=1)
        self.assertEqual(self.store.capture(self.settings, self.pricing), 0)
        self.assertEqual(self.store.capture(self.settings, self.pricing, force=True), 1)
        finalized = copy.deepcopy(self.settings)
        finalized["overnightRun"]["finalizedAt"] = self.now.isoformat()
        self.assertEqual(self.store.capture(finalized, self.pricing), 1)

    def test_price_upgrade_persists_for_closed_ledger_without_log_folder(self):
        self.pricing.settled_hourly.clear()
        # A complete twelve-sample observed average is final in the pricing adapter.
        for hour in (4, 5, 6, 7):
            stamp = int(instant(f"2026-11-06T{hour:02}:00:00Z").timestamp())
            self.pricing.samples[stamp] = 2
        self.store.capture(self.settings, self.pricing, force=True)
        self.assertFalse(self.store.view({}, None)["sessions"][0]["costFinal"])
        for hour in (4, 5, 6, 7):
            self.pricing.settled_hourly[int(instant(f"2026-11-06T{hour:02}:00:00Z").timestamp())] = 3
        self.store.capture({}, self.pricing, force=True)
        restarted = HistoryStore(self.state, self.logs, lambda: self.now)
        row = restarted.view({}, None)["sessions"][0]
        self.assertTrue(row["costFinal"])
        self.assertEqual(row["costUSD"], 0.69)

    def test_late_first_target_is_unknown_but_observed_miss_is_false(self):
        row = self.store.view(self.settings, self.pricing)["sessions"][0]
        self.assertIsNone(row["targetReachedByDeadline"])
        below = copy.deepcopy(self.settings)
        below["virtualCarState"]["batteryPercent"] = 79
        row = self.store.view(below, self.pricing)["sessions"][0]
        self.assertFalse(row["targetReachedByDeadline"])
        self.assertEqual(row["status"], "incomplete")

    def test_immediate_baseline_spans_utc_hours_and_dst_without_wall_clock_guessing(self):
        buckets = immediate_buckets("2026-11-01T01:30:00-05:00", 23)
        self.assertEqual(buckets, {"2026-11-01T06:00:00+00:00": 5.75,
                                   "2026-11-01T07:00:00+00:00": 11.5,
                                   "2026-11-01T08:00:00+00:00": 5.75})


if __name__ == "__main__":
    unittest.main()

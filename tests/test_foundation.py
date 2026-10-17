"""The one-click model test, with the built-in forecasts only (no AI model is downloaded or run)."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fakes import FakeComEd
from sparc import foundation
from sparc.clock import HOUR
from sparc.history import backfill
from sparc.store import Store

FIRST, LAST = date(2026, 1, 20), date(2026, 2, 20)


class FoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        store = Store(Path(folder.name) / "sparc.db")
        cls.addClassCleanup(store.close)
        backfill(store, date(2026, 1, 1), date(2026, 2, 28), FakeComEd(), log=lambda message: None,
                 today=date(2026, 3, 15))
        cls.market = foundation.market_for(store, FIRST, LAST)

    def test_every_model_has_a_loader_and_a_status(self):
        self.assertEqual(set(foundation.MODELS), set(foundation.LOADERS))
        rows = {row["id"]: row for row in foundation.status()}
        self.assertEqual(set(rows), set(foundation.MODELS))
        for ident in ("day-ahead", "seasonal-naive"):
            self.assertTrue(rows[ident]["builtin"])
            self.assertTrue(rows[ident]["ready"])
        for row in rows.values():
            if not row["builtin"]:
                self.assertGreater(row["size_mb"], 0)
                self.assertGreater(row["seconds_per_1000"], 0)

    def test_periods_need_a_full_history_before_the_first_night(self):
        options = foundation.periods(date(2012, 10, 29), date(2026, 10, 17))
        self.assertEqual(set(options), {"study", "year", "all"})
        self.assertEqual((options["study"]["first"], options["study"]["last"]), ("2025-04-02", "2026-10-14"))
        self.assertEqual(options["study"]["nights"], 561)
        self.assertEqual(options["year"]["last"], "2026-10-16")
        self.assertEqual(options["all"]["first"], "2013-01-25")
        self.assertEqual(foundation.periods(date(2026, 1, 1), date(2026, 2, 28)), {})
        self.assertEqual(foundation.periods(None, None), {})

    def test_history_is_the_hourly_mean_of_what_was_posted(self):
        history = foundation.History(self.market, context=48)
        decision = foundation.decisions(self.market, FIRST, FIRST)[0]
        series, ahead = history.series(decision.now)
        self.assertEqual(len(series), 48)
        before = decision.now - 2 * HOUR  # a full hour: the mean of its twelve 5-minute prices
        prices = [p for t, p in self.market.five_minute.items() if before <= t < before + HOUR]
        self.assertAlmostEqual(series[-2], sum(prices) / len(prices))
        self.assertEqual(ahead[-1], self.market.day_ahead[decision.now - HOUR])

    def test_a_test_of_the_built_in_forecasts(self):
        out = Path(self.enterContext(tempfile.TemporaryDirectory()))
        result = foundation.run_test(self.market, ["seasonal-naive"], FIRST, LAST, out_folder=out)
        self.assertEqual([r["id"] for r in result["rows"]], ["day-ahead", "seasonal-naive"])
        plan, naive = result["rows"]
        self.assertEqual(plan["rmse"], plan["day_ahead_rmse"])
        self.assertEqual(plan["vs_plan"]["Average"]["per_year"], 0)
        self.assertEqual(set(naive["saved"]), {"Average", "Moderate", "Heavy"})
        self.assertGreater(result["default_cost_per_year"]["Average"], plan["saved"]["Average"])
        self.assertLessEqual(plan["saved"]["Average"], result["hindsight"]["Average"] + 1e-9)
        self.assertEqual(result["nights"]["Average"], (LAST - FIRST).days + 1)
        self.assertTrue(result["verdict"])
        saved = foundation.list_tests(out)
        self.assertEqual([t["id"] for t in saved], [result["id"]])
        self.assertEqual(foundation.load_test(out, result["id"])["rows"][1]["name"], "Same hour yesterday")
        report = (out / f"{result['id']}.md").read_text()
        self.assertIn("| Same hour yesterday |", report)
        self.assertIn("Perfect hindsight", report)
        with self.assertRaises(LookupError):
            foundation.load_test(out, "../secrets")

    def test_saved_forecasts_are_reused(self):
        cache = Path(self.enterContext(tempfile.TemporaryDirectory())) / "seasonal-naive.json"
        batch = foundation.decisions(self.market, FIRST, FIRST + (LAST - FIRST) // 4)
        history = foundation.History(self.market)
        first = foundation.forecast_all("seasonal-naive", batch, history, cache)
        with mock.patch.dict(foundation.LOADERS, {"seasonal-naive": mock.Mock(side_effect=AssertionError("loaded"))}):
            again = foundation.forecast_all("seasonal-naive", batch, history, cache)
        self.assertEqual(first, again)

    def test_installing_needs_sparcs_own_environment(self):
        model = foundation.ModelInfo("missing", "Missing", "Nobody", "Not real.", ("nothing-at-all",),
                                     ("sparc_test_missing_module",), (("nobody/missing", "model.bin"),), 1)
        with mock.patch.dict(foundation.MODELS, {"missing": model}), mock.patch.object(sys, "prefix", sys.base_prefix):
            with self.assertRaisesRegex(ValueError, "start.command"):
                foundation.install(["missing"])
        self.assertEqual(foundation.install(["day-ahead", "seasonal-naive"]), {"installed": [], "downloaded": []})

    def test_verdicts(self):
        def row(ident, name, rmse, gap, error=1.0):
            return {"id": ident, "name": name, "rmse": rmse, "rmse_change": rmse / 6.0 - 1,
                    "vs_plan": {"Average": {"per_year": gap, "error_per_year": error}}}
        result = {"drivers": [{"name": "Average"}], "rows": [row("day-ahead", "Day-ahead price", 6.0, 0, 0)]}
        self.assertIn("Pick one or more models", foundation.verdict(result))
        result["rows"].append(row("a", "Model A", 5.0, -14.0))
        self.assertEqual(foundation.verdict(result),
                         "No model saved more than SPARC's day-ahead plan. The most accurate was Model A, 17% more "
                         "accurate than the day-ahead price, yet it saved $14 a year less for the Average driver.")
        result["rows"].append(row("b", "Model B", 5.5, 9.0))
        self.assertTrue(foundation.verdict(result).startswith("Model B saved more than SPARC's day-ahead plan: $9"))
        result["rows"] = result["rows"][:1] + [row("c", "Model C", 7.0, -3.0)]
        self.assertIn("No model beat the day-ahead price, on accuracy or on savings", foundation.verdict(result))
        self.assertEqual(foundation.compared({"per_year": 1.5, "error_per_year": 1.0}), "same")
        self.assertEqual(foundation.compared({"per_year": -3.0, "error_per_year": 1.0}), "worse")


if __name__ == "__main__":
    unittest.main()

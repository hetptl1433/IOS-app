import unittest
from datetime import date

from sparc_controller.billing import cents
from sparc_controller.replay_baselines import KEY, augment, night_costs, table_prices


def table(rows):
    cells = "".join(f"<tr><td>{label}</td><td>3.0¢</td><td>{price}</td></tr>" for label, price in rows)
    return f"<table>{cells}</table>".encode()


LABELS = ["12:00 a.m."] + [f"{h}:00 a.m." for h in range(1, 12)] + ["12:00 p.m."] + [f"{h}:00 p.m." for h in range(1, 12)]


class TablePricesTest(unittest.TestCase):
    def test_rows_are_keyed_by_their_hour_start(self):
        prices = table_prices(table([(label, f"{i}.0¢") for i, label in enumerate(LABELS)]), date(2026, 11, 2))
        self.assertEqual(len(prices), 24)
        self.assertEqual(prices["2026-11-02T05:00:00+00:00"], 0.0)   # 11 p.m.–midnight the evening before
        self.assertEqual(prices["2026-11-03T00:00:00+00:00"], 19.0)  # 6–7 p.m. CST

    def test_missing_prices_and_daylight_saving_hours_are_left_out(self):
        rows = [(label, "n/a" if label == "9:00 p.m." else "2.5¢") for label in LABELS]
        prices = table_prices(table(rows), date(2026, 11, 1))
        self.assertEqual(len(prices), 21)
        self.assertNotIn("2026-11-02T02:00:00+00:00", prices)        # 8–9 p.m. CST: no price
        self.assertEqual(prices["2026-11-01T08:00:00+00:00"], 2.5)   # 2–3 a.m. CST is unambiguous


class NightCostsTest(unittest.TestCase):
    def prices(self, day, value=2.0):
        return {k: value for k in table_prices(table([(l, "1.0¢") for l in LABELS]), day)} | \
            {k: value for k in table_prices(table([(l, "1.0¢") for l in LABELS]), date.fromordinal(day.toordinal() + 1))}

    def test_charges_right_away_from_six_pm(self):
        day = date(2026, 11, 2)
        result = night_costs(day, 11.5, 11.5, self.prices(day))
        hour = "2026-11-03T00:00:00+00:00"
        self.assertAlmostEqual(result["hourlyStandardCostUSD"], 11.5 * (2.0 + cents(hour, standard=True)) / 100, places=6)
        self.assertAlmostEqual(result["hourlyTODCostUSD"], 11.5 * (2.0 + cents(hour)) / 100, places=6)

    def test_a_missing_hour_leaves_the_night_unpriced(self):
        day = date(2026, 11, 2)
        prices = self.prices(day)
        del prices["2026-11-03T01:00:00+00:00"]
        self.assertIsNone(night_costs(day, 20.0, 11.5, prices))

    def test_augment_checks_the_replay_before_adding_six_pm(self):
        day = date(2026, 11, 2)
        prices = self.prices(day)
        arrival = night_costs(day, 30.0, 11.5, prices, 22 * 60)
        report = {"assumptions": {"powerKW": 11.5, "startMinutes": 22 * 60},
                  "nights": [{"date": "2026-11-02", "energyKWh": 30.0, **arrival}]}
        result = augment(report, prices)
        self.assertEqual(result["nights"][0][KEY], night_costs(day, 30.0, 11.5, prices))
        self.assertNotIn(KEY, report["nights"][0])
        report["nights"][0]["hourlyTODCostUSD"] += 0.02
        with self.assertRaises(ValueError):
            augment(report, prices)


if __name__ == "__main__":
    unittest.main()

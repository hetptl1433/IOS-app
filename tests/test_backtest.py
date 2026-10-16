import random
import unittest
from datetime import date, time

from sparc.backtest import Profile, run, simulate, summarize, usable
from sparc.clock import DAY, HOUR, SLOT, at, hour_start, local
from sparc.market import Market
from sparc.strategies import ChargeNow, DayAhead, Hindsight, Live, Session, Threshold, Timer, fit_decay
from sparc.tariff import STANDARD, TIME_OF_DAY

DAY0 = date(2026, 1, 12)  # a Monday in winter: no capacity-risk hours


def flat_market(first=DAY0, days=3, price=3.0, day_ahead=3.0):
    start, end = at(first, time(0)) - DAY, at(first, time(0)) + days * DAY
    five = {t: price for t in range(start, end, SLOT)}
    ahead = {h: day_ahead for h in range(start, end, HOUR)}
    return Market(five, ahead)


def with_hour(market, hour, prices=None, day_ahead=None):
    five, ahead = dict(market.five_minute), dict(market.day_ahead)
    if prices is not None:
        for i, p in enumerate(prices):
            five[hour + i * SLOT] = p
    if day_ahead is not None:
        ahead[hour] = day_ahead
    return Market(five, ahead)


def random_market(rng, first, days):
    start = at(first, time(0)) - DAY
    five, ahead = {}, {}
    for hour in range(start, start + (days + 1) * DAY, HOUR):
        wall = local(hour).hour
        level = 2.0 + 3.0 * (14 <= wall <= 20) - 1.5 * (1 <= wall <= 5) + rng.gauss(0, 0.8)
        ahead[hour] = round(level + rng.gauss(0, 0.7), 1)
        for i in range(12):
            spike = rng.uniform(15, 60) if rng.random() < 0.02 else 0.0
            five[hour + i * SLOT] = round(level + rng.gauss(0, 0.6) + spike, 1)
    return Market(five, ahead)


class SimulateTests(unittest.TestCase):
    def test_charge_now_billed_at_hourly_average(self):
        market = flat_market()
        evening = at(DAY0, time(18))
        market = with_hour(market, evening, prices=[0.0] + [12.0] * 11)  # hour averages 11.0
        session = Session(evening, evening + 2 * HOUR, energy_kwh=1.0, power_kw=6.0, reserve_s=0)
        outcome = simulate(session, ChargeNow(), market, STANDARD)
        self.assertAlmostEqual(outcome.supply, 1.0 * 11.0 / 100)
        self.assertAlmostEqual(outcome.delivery, 1.0 * 6.228 / 100)
        self.assertTrue(outcome.ready)
        self.assertEqual((outcome.starts, outcome.pauses), (1, 0))

    def test_timer_starts_at_its_time_and_finishes(self):
        session = Profile("t", time(18), time(7), 15, 7.7).session(DAY0)
        outcome = simulate(session, timer := Timer(time(23)), flat_market(), STANDARD)
        self.assertEqual(local(timer.begins).hour, 23)
        self.assertTrue(outcome.ready)

    def test_timer_starts_at_once_when_plugged_in_up_to_six_hours_after_its_time(self):
        late = Profile("t", time(22), time(6), 15, 7.7).session(DAY0)
        simulate(late, timer := Timer(time(21)), flat_market(), STANDARD)
        self.assertEqual(timer.begins, late.arrival)
        # Plugged in at 3:30 a.m., six and a half hours after 21:00: wait for tonight's.
        early = Profile("t", time(3, 30), time(23, 30), 7.7, 7.7).session(DAY0)
        simulate(early, timer, flat_market(), STANDARD)
        self.assertEqual(timer.begins, at(DAY0, time(21)))

    def test_timer_starts_early_when_its_time_is_too_late(self):
        session = Profile("t", time(22), time(6), 50, 7.7).session(DAY0)  # 55.6 kWh: 7 h 15 min
        timer = Timer(time(23))
        self.assertTrue(simulate(session, timer, flat_market(), STANDARD).ready)
        self.assertEqual(local(timer.begins).time(), time(22, 15))  # still done by 5:30

    def test_price_cap_pauses_while_prices_are_high_and_still_finishes(self):
        market = flat_market(price=2.0)
        spike = at(DAY0, time(19))
        market = with_hour(market, spike, prices=[9.0] * 12)
        session = Profile("t", time(18, 55), time(7), 15, 7.7).session(DAY0)
        cap = Threshold(3)
        cap.begin(session, market, STANDARD)
        # The 19:00 price is posted about 7 minutes in, so the charger stops at 19:10
        # and restarts once 20:00's price is posted.
        on = [t for t in range(session.arrival, session.departure, SLOT) if cap.energy(t, 10.0, True)]
        self.assertIn(at(DAY0, time(19, 5)), on)
        self.assertNotIn(at(DAY0, time(19, 10)), on)
        self.assertNotIn(at(DAY0, time(20, 5)), on)
        self.assertIn(at(DAY0, time(20, 10)), on)
        outcome = simulate(session, cap, market, STANDARD)
        self.assertTrue(outcome.ready)
        self.assertEqual(outcome.pauses, 1)

    def test_price_cap_starts_when_waiting_would_finish_too_late(self):
        session = Profile("t", time(22), time(6), 50, 7.7).session(DAY0)  # needs 7 h 15 min of 8 h
        outcome = simulate(session, Threshold(1), flat_market(price=4.0), STANDARD)
        self.assertTrue(outcome.ready)
        self.assertEqual(outcome.starts, 1)

    def test_pause_and_restart_are_counted(self):
        class OnOffOn:
            label = "test"

            def begin(self, *args):
                pass

            def energy(self, now, remaining, charging):
                return 0.0 if (now // SLOT) % 3 == 1 else 1.0

        session = Session(at(DAY0, time(18)), at(DAY0, time(19)), energy_kwh=2.0, power_kw=6.0)
        outcome = simulate(session, OnOffOn(), flat_market(), STANDARD)
        self.assertTrue(outcome.ready)
        self.assertGreaterEqual(outcome.pauses, 1)
        self.assertEqual(outcome.starts, outcome.pauses + 1)


class StrategyTests(unittest.TestCase):
    def test_live_prices_dodge_a_spike_the_day_ahead_plan_misses(self):
        # 1-2 a.m. looks cheapest a day ahead, then spikes in its first 15 minutes.
        market = flat_market()
        one_am = at(DAY0 + date.resolution, time(1))
        market = with_hour(market, one_am, prices=[30.0, 30.0, 30.0] + [1.5] * 9, day_ahead=1.0)
        session = Session(at(DAY0, time(18)), at(DAY0 + date.resolution, time(7)),
                          energy_kwh=7.7, power_kw=7.7)
        planned = simulate(session, DayAhead(), market, STANDARD)
        live = simulate(session, Live(decay=[0.5] * 6), market, STANDARD)
        self.assertTrue(planned.ready and live.ready)
        self.assertAlmostEqual(planned.supply, 7.7 * market.hourly[one_am] / 100)
        self.assertLess(live.supply, planned.supply * 0.6)

    def test_day_ahead_plan_waits_for_published_prices(self):
        # Tomorrow 3 a.m. is very cheap, but that is only known after the 4:30 p.m. release.
        market = flat_market(day_ahead=4.0)
        three_am = at(DAY0 + date.resolution, time(3))
        market = with_hour(market, three_am, day_ahead=-5.0)
        session = Profile("p", time(14), time(7), 6.93, 7.7).session(DAY0)
        strategy = DayAhead()
        strategy.begin(session, market, STANDARD)
        before = strategy.costs(at(DAY0, time(16)))
        after = strategy.costs(at(DAY0, time(17)))
        self.assertEqual(before[three_am] - STANDARD.cents(three_am), 4.0)
        self.assertEqual(after[three_am] - STANDARD.cents(three_am), -5.0)

    def test_hindsight_is_never_beaten(self):
        rng = random.Random(7)
        profiles = [Profile("a", time(18), time(7), 15, 7.7), Profile("b", time(14), time(7), 30, 7.7),
                    Profile("c", time(22), time(6), 50, 11.5)]
        first = date(2026, 7, 6)  # summer weekdays, so capacity-risk hours are in play
        market = random_market(rng, first, days=6)
        strategies = [ChargeNow(), Timer(time(23)), DayAhead(), Live([0.6 ** k for k in range(1, 13)]),
                      Hindsight()]
        days = [first + i * date.resolution for i in range(5)]
        nights, skipped = run(market, days, profiles, strategies, [STANDARD, TIME_OF_DAY])
        self.assertEqual(skipped, {})
        best = {}
        for n in nights:
            if n.strategy == "Perfect hindsight":
                best[n.day, n.profile, n.delivery] = n.outcome
        for n in nights:
            self.assertTrue(n.outcome.ready, n)
            ideal = best[n.day, n.profile, n.delivery]
            if n.strategy in ("Day-ahead plan", "Day-ahead + live prices"):
                # Same capacity-risk preference: never more risky energy, and never cheaper when equal.
                self.assertLessEqual(ideal.risk_kwh, n.outcome.risk_kwh + 1e-9)
                if abs(ideal.risk_kwh - n.outcome.risk_kwh) < 1e-9:
                    self.assertLessEqual(ideal.cost, n.outcome.cost + 1e-9, n)
        summaries = summarize(nights, baseline="Charge on plug-in", best="Perfect hindsight")
        shares = {(s.profile, s.delivery, s.strategy): s.share for s in summaries}
        self.assertEqual(shares["a", "Standard delivery", "Perfect hindsight"], 1.0)

    def test_nights_across_daylight_saving_changes(self):
        rng = random.Random(5)
        profile = Profile("n", time(18), time(7), 40, 7.7)
        for night, length in ((date(2026, 10, 31), 14), (date(2026, 3, 7), 12)):
            with self.subTest(night=night):
                market = random_market(rng, night - 2 * date.resolution, days=4)
                session = profile.session(night)
                self.assertEqual((session.departure - session.arrival) // HOUR, length)
                outcomes = {s.label: simulate(session, s, market, TIME_OF_DAY)
                            for s in (ChargeNow(), Timer(time(23)), DayAhead(),
                                      Live([0.5] * 12), Hindsight())}
                for label, outcome in outcomes.items():
                    self.assertTrue(outcome.ready, label)
                    self.assertAlmostEqual(outcome.energy_kwh, session.energy_kwh, places=9)
                    self.assertLessEqual(outcomes["Perfect hindsight"].cost, outcome.cost + 1e-9, label)

    def test_csv_round_trip(self):
        import csv
        import tempfile
        from pathlib import Path
        from sparc.backtest import write_csv
        market = flat_market()
        days = [DAY0, DAY0 + date.resolution]
        nights, _ = run(market, days, [Profile("p", time(18), time(7), 15, 7.7)], [ChargeNow()], [TIME_OF_DAY])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nights.csv"
            write_csv(nights, path)
            with path.open() as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tariff"], "Delivery Time-of-Day")
        self.assertAlmostEqual(float(rows[0]["cost_usd"]),
                               float(rows[0]["supply_usd"]) + float(rows[0]["delivery_usd"]), places=5)

    def test_sessions_with_missing_prices_are_skipped(self):
        market = flat_market()
        session = Profile("p", time(18), time(7), 15, 7.7).session(DAY0)
        self.assertTrue(usable(session, market))
        gap = dict(market.five_minute)
        del gap[at(DAY0, time(23, 5))]
        self.assertTrue(usable(session, Market(gap, market.day_ahead)))  # 11 readings still bill
        for minute in (10, 15):
            del gap[at(DAY0, time(23, minute))]
        self.assertFalse(usable(session, Market(gap, market.day_ahead)))


class DecayTests(unittest.TestCase):
    def test_recovers_persistence_of_deviations(self):
        rng = random.Random(3)
        start = at(DAY0, time(0))
        five, ahead, deviation = {}, {}, 0.0
        for k in range(20 * 288):
            t = start + k * SLOT
            deviation = 0.6 * deviation + rng.gauss(0, 1)
            ahead[hour_start(t)] = 3.0
            five[t] = 3.0 + deviation
        decay = fit_decay(Market(five, ahead), start, start + 20 * DAY, steps=3)
        for got, want in zip(decay, (0.6, 0.36, 0.216)):
            self.assertAlmostEqual(got, want, delta=0.03)


if __name__ == "__main__":
    unittest.main()

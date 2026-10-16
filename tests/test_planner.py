import unittest
from collections import defaultdict
from datetime import datetime, timezone

from sparc.clock import HOUR, SLOT
from sparc.planner import Need, plan

T0 = int(datetime(2026, 1, 5, 6, tzinfo=timezone.utc).timestamp())  # midnight CST, a Monday
FULL = 6.0 * SLOT / HOUR  # 6 kW for five minutes = 0.5 kWh


def by_hour(schedule):
    totals = defaultdict(float)
    for slot, kwh in schedule.items():
        totals[(slot - T0) // HOUR] += kwh
    return {hour: round(kwh, 9) for hour, kwh in totals.items()}


def need(hours=3, energy=9.0, reserve=0, now=T0):
    return Need(now=now, departure=T0 + hours * HOUR, energy_kwh=energy, power_kw=6.0, reserve_s=reserve)


class PlanTests(unittest.TestCase):
    def test_fills_cheapest_hours_first_with_exact_energy(self):
        cost = {T0: 5.0, T0 + HOUR: 1.0, T0 + 2 * HOUR: 3.0}
        schedule = plan(need(energy=8.75), cost)
        self.assertEqual(by_hour(schedule), {1: 6.0, 2: 2.75})
        self.assertAlmostEqual(sum(schedule.values()), 8.75)
        self.assertTrue(all(0 < kwh <= FULL + 1e-12 for kwh in schedule.values()))

    def test_partial_hour_continues_the_block_before_it(self):
        cost = {T0: 5.0, T0 + HOUR: 1.0, T0 + 2 * HOUR: 3.0}
        schedule = plan(need(energy=7.0), cost)
        third = sorted(s for s in schedule if s >= T0 + 2 * HOUR)
        self.assertEqual(third, [T0 + 2 * HOUR, T0 + 2 * HOUR + SLOT])

    def test_partial_hour_leads_into_the_block_after_it(self):
        cost = {T0: 3.0, T0 + HOUR: 1.0, T0 + 2 * HOUR: 5.0}
        schedule = plan(need(energy=7.0), cost)
        first = sorted(s for s in schedule if s < T0 + HOUR)
        self.assertEqual(first, [T0 + HOUR - 2 * SLOT, T0 + HOUR - SLOT])

    def test_reserve_is_kept_when_possible_and_given_up_when_not(self):
        cost = {T0: 9.0, T0 + HOUR: 9.0, T0 + 2 * HOUR: 1.0}
        kept = plan(need(energy=6.0, reserve=HOUR), cost)
        self.assertLess(max(kept), T0 + 2 * HOUR)  # the cheap hour sits inside the reserve
        self.assertAlmostEqual(sum(kept.values()), 6.0)
        relaxed = plan(need(energy=15.0, reserve=HOUR), cost)
        self.assertEqual(by_hour(relaxed), {0: 6.0, 1: 6.0, 2: 3.0})

    def test_blocked_hours_only_when_energy_would_not_fit(self):
        cost = {T0: 1.0, T0 + HOUR: 9.0, T0 + 2 * HOUR: 9.0}
        avoided = plan(need(energy=6.0), cost, blocked={T0})
        self.assertNotIn(0, by_hour(avoided))
        needed = plan(need(energy=15.0), cost, blocked={T0})
        self.assertEqual(by_hour(needed)[0], 3.0)

    def test_stickiness_holds_the_current_state(self):
        cost = {T0: 2.2, T0 + HOUR: 2.0}
        request = need(hours=2, energy=6.0)
        self.assertNotIn(T0, plan(request, cost))
        self.assertIn(T0, plan(request, cost, charging=True, stickiness=0.5))
        cost = {T0: 1.8, T0 + HOUR: 2.0}
        self.assertNotIn(T0, plan(request, cost, charging=False, stickiness=0.5))

    def test_running_charge_continues_in_partial_current_hour(self):
        cost = {T0: 1.0, T0 + HOUR: 5.0}
        now = T0 + 6 * SLOT
        schedule = plan(need(hours=2, energy=1.0, now=now), cost, charging=True)
        self.assertEqual(sorted(schedule), [now, now + SLOT])

    def test_infeasible_request_uses_every_slot(self):
        schedule = plan(need(hours=1, energy=50.0), {T0: 1.0})
        self.assertEqual(len(schedule), 12)
        self.assertAlmostEqual(sum(schedule.values()), 6.0)

    def test_nothing_to_do(self):
        self.assertEqual(plan(need(energy=0.0), {}), {})


if __name__ == "__main__":
    unittest.main()

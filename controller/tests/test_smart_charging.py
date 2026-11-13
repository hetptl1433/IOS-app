from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sparc_controller.smart_charging import SOURCE_COMMIT, make_plan
from sparc_controller.research.clock import SLOT, HOUR, hour_start
from sparc_controller.research.market import Market, latest_visible_interval
from sparc_controller.research.planner import Need, plan
from sparc_controller.research.strategies import Live, Session
from sparc_controller.research.tariff import Delivery

UTC = timezone.utc


def instant(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


class SmartChargingTests(unittest.TestCase):
    def setUp(self):
        self.now = instant('2026-11-05T02:00:00Z')  # November 4, 20:00 Chicago
        self.arrival = self.now
        self.deadline = instant('2026-11-05T14:00:00Z')
        beginning = hour_start(int(self.now.timestamp())) - 48*HOUR
        self.ahead = {hour: 2.0 for hour in range(beginning, beginning+4*24*HOUR, HOUR)}
        visible = latest_visible_interval(int(self.now.timestamp()))
        self.samples = {stamp: 2.0 for stamp in range(visible-24*HOUR+SLOT, visible+SLOT, SLOT)}

    def call(self, **changes):
        fields = dict(now=self.now, earliest=self.arrival, deadline=self.deadline,
                      battery_percent=20.0, target_percent=90.0,
                      day_ahead=self.ahead, live_samples=self.samples)
        fields.update(changes)
        return make_plan(**fields)

    def verify_physical_intervals(self, result, now=None, arrival=None, deadline=None, power=11.5):
        first = max(now or self.now, arrival or self.arrival)
        last = deadline or self.deadline
        previous = first
        for item in result['intervals']:
            start, end = instant(item['start']), instant(item['end'])
            self.assertGreaterEqual(start, first)
            self.assertGreaterEqual(start, previous)
            self.assertGreater(end, start)
            self.assertLessEqual(end, last)
            self.assertAlmostEqual((end-start).total_seconds()*power/HOUR,item['energyKWh'],places=7)
            previous = end

    def test_pinned_strategy_uses_the_forecast_cheapest_hours(self):
        end = self.now + timedelta(hours=4)
        hours = [int((self.now+timedelta(hours=i)).timestamp()) for i in range(4)]
        for hour, price in zip(hours,[10.0,1.0,1.0,20.0]):self.ahead[hour]=price
        result = self.call(deadline=end,target_percent=40,mode='day_ahead')
        self.assertTrue(result['feasible'])
        self.assertFalse(result['fallback'])
        self.assertEqual(result['sourceCommit'],SOURCE_COMMIT)
        self.assertEqual(result['strategy'],'Day-ahead plan')
        self.assertFalse(result['chargeNow'])
        self.assertAlmostEqual(sum(s['energyKWh'] for s in result['intervals']),20/0.9)
        self.assertAlmostEqual(result['estimatedCostUSD'],(20/0.9)*0.01)
        self.assertEqual({hour_start(int(instant(s['start']).timestamp())) for s in result['intervals']},set(hours[1:3]))
        self.verify_physical_intervals(result,deadline=end)

    def test_matches_exact_live_strategy_costs_and_aligned_plan(self):
        now = self.now + timedelta(minutes=35)
        visible = latest_visible_interval(int(now.timestamp()))
        self.samples.update({stamp:2.0 for stamp in range(max(self.samples)+SLOT,visible+SLOT,SLOT)})
        self.samples[visible]=35.0
        decay=[1.0]*24
        result=self.call(now=now,decay=decay)
        market=Market(dict(self.samples),dict(self.ahead))
        strategy=Live(decay)
        strategy.begin(Session(int(now.timestamp()),int(self.deadline.timestamp()),70/0.9,11.5,reserve_s=0),market,Delivery('Supply only',((0,0.0),)))
        expected_costs=strategy.costs(int(now.timestamp()))
        expected=plan(Need(int(now.timestamp()),int(self.deadline.timestamp()),70/0.9,11.5),expected_costs,strategy.blocked,stickiness=strategy.stickiness)
        self.assertEqual({int(instant(row['hour']).timestamp()):row['priceCents'] for row in result['forecastHourly']},expected_costs)
        self.assertAlmostEqual(sum(expected.values()),sum(s['energyKWh'] for s in result['intervals']))
        expected_total=sum(energy*expected_costs[hour_start(slot)]/100 for slot,energy in expected.items())
        self.assertAlmostEqual(result['estimatedCostUSD'],expected_total)
        self.verify_physical_intervals(result,now=now)

    def test_negative_prices_are_kept_and_can_be_selected(self):
        self.ahead[int((self.now+timedelta(hours=2)).timestamp())]=-4.0
        result=self.call(target_percent=25,mode='day_ahead')
        self.assertLess(result['estimatedCostUSD'],0)
        self.assertEqual({x['priceCents'] for x in result['intervals']},{-4.0})
        self.verify_physical_intervals(result)

    def test_delivery_changes_price_ranking_and_fallback_estimate_uses_both_parts(self):
        hours=list(range(hour_start(int(self.now.timestamp())),int(self.deadline.timestamp()),HOUR))
        self.ahead.update({h:2.0 for h in hours})
        delivery={h:10.0 if i==0 else 1.0 for i,h in enumerate(hours)}
        result=self.call(target_percent=25,mode='day_ahead',delivery_cents=delivery)
        self.assertEqual({x['priceCents'] for x in result['intervals']},{3.0})
        late=self.deadline-timedelta(minutes=10)
        fallback=self.call(now=late,delivery_cents=delivery)
        self.assertTrue(fallback['fallback'])
        self.assertTrue(all(x['priceCents']==3.0 for x in fallback['intervals']))
        self.assertIn('distribution',fallback['costScope'])
        with self.assertRaises(ValueError):self.call(delivery_cents={})

    def test_before_window_never_charges_early(self):
        earlier=self.now-timedelta(hours=2)
        result=self.call(now=earlier)
        self.assertFalse(result['chargeNow'])
        self.assertEqual(result['reason'],'before_window')
        self.assertLessEqual(instant(result['validUntil']),earlier+timedelta(minutes=5))
        self.verify_physical_intervals(result,now=earlier)

    def test_missing_forecast_has_honest_deadline_protection(self):
        result=self.call(day_ahead={})
        self.assertTrue(result['fallback'])
        self.assertEqual(result['reason'],'missing_forecast_deadline_protection')
        self.assertTrue(result['chargeNow'])
        self.assertIsNone(result['estimatedCostUSD'])
        self.assertTrue(all(x['priceCents'] is None for x in result['intervals']))
        self.assertAlmostEqual(sum(x['energyKWh'] for x in result['intervals']),70/0.9)
        self.verify_physical_intervals(result)

    def test_insufficient_time_reports_shortfall_and_uses_all_available_time(self):
        now=self.deadline-timedelta(minutes=10)
        result=self.call(now=now)
        self.assertFalse(result['feasible'])
        self.assertTrue(result['fallback'])
        self.assertTrue(result['chargeNow'])
        self.assertEqual(result['reason'],'infeasible_deadline')
        self.assertAlmostEqual(result['shortfallKWh'],70/0.9-11.5/6)
        self.assertEqual(instant(result['intervals'][-1]['end']),self.deadline)
        self.verify_physical_intervals(result,now=now)

    def test_restart_in_partial_slot_delivers_target_without_backdating(self):
        now=self.now+timedelta(minutes=2,seconds=31,microseconds=123456)
        before_ahead=dict(self.ahead);before_samples=dict(self.samples)
        result=self.call(now=now,previous_charging=True)
        self.assertAlmostEqual(sum(x['energyKWh'] for x in result['intervals']),70/0.9,places=6)
        self.verify_physical_intervals(result,now=now)
        self.assertEqual(self.ahead,before_ahead)
        self.assertEqual(self.samples,before_samples)

    def test_partial_deadline_and_partial_current_slot_are_rebalanced(self):
        now=instant('2026-11-05T13:58:23.123456Z')
        end=instant('2026-11-05T14:02:11.444444Z')
        self.ahead[hour_start(int(now.timestamp()))]=1.0
        self.ahead[hour_start(int(end.timestamp()))]=5.0
        result=self.call(now=now,deadline=end,battery_percent=89.6,target_percent=90,mode='day_ahead')
        self.assertTrue(result['feasible'])
        self.assertFalse(result['fallback'])
        self.assertAlmostEqual(sum(x['energyKWh'] for x in result['intervals']),0.4/0.9,places=6)
        self.verify_physical_intervals(result,now=now,deadline=end)

    def test_future_unpublished_live_prices_cannot_influence_a_plan(self):
        visible=latest_visible_interval(int(self.now.timestamp()))
        future=dict(self.samples);future[visible+SLOT]=10000.0
        a=self.call(decay=[1.0]*24)
        b=self.call(live_samples=future,decay=[1.0]*24)
        self.assertEqual(a,b)

    def test_supplied_fit_is_used_unchanged_with_its_audit_metadata(self):
        fitted={'sourceCommit':SOURCE_COMMIT,'trainingStartInclusive':'2026-08-06T05:00:00Z',
                'trainingEndExclusive':'2026-11-04T06:00:00Z','observations':23000,
                'pairsPerLag':[22000]*24,'decay':[0.25]*24}
        result=self.call(decay=fitted)
        self.assertEqual(result['training']['source'],'supplied_pinned_fit')
        self.assertEqual(result['training']['coefficients'],fitted['decay'])
        self.assertEqual(result['training']['observations'],23000)
        self.assertIn('decay',fitted)

    def test_recent_fit_reports_real_observation_and_pair_counts(self):
        result=self.call()
        training=result['training']
        self.assertEqual(training['source'],'fit_decay_recent_observed_history')
        self.assertGreater(training['observations'],200)
        self.assertEqual(len(training['pairsPerLag']),24)
        self.assertTrue(all(count>0 for count in training['pairsPerLag']))
        self.assertEqual(training['coefficients'],[0.0]*24)  # observed deviations are exactly zero

    def test_completed_and_expired_goals_never_add_charge(self):
        done=self.call(battery_percent=90)
        self.assertEqual(done['intervals'],[])
        self.assertEqual(done['reason'],'target_reached')
        self.assertTrue(done['feasible'])
        expired=self.call(now=self.deadline)
        self.assertEqual(expired['intervals'],[])
        self.assertEqual(expired['reason'],'deadline_passed')
        self.assertFalse(expired['feasible'])

    def test_explicit_ready_reserve_is_soft_and_never_makes_a_feasible_goal_short(self):
        end=self.now+timedelta(hours=1)
        result=self.call(deadline=end,target_percent=30.35,reserve_seconds=1800,mode='day_ahead')
        self.assertTrue(result['feasible'])
        self.assertFalse(result['fallback'])
        self.assertAlmostEqual(sum(x['energyKWh'] for x in result['intervals']),11.5)
        self.assertEqual(instant(result['intervals'][-1]['end']),end)
        self.verify_physical_intervals(result,deadline=end)

    def test_invalid_physical_inputs_or_forecast_coefficients_fail_explicitly(self):
        for changes in ({'battery_percent':True},{'efficiency':0},{'power_kw':0},
                        {'target_percent':101},{'now':self.now.replace(tzinfo=None)},
                        {'decay':[0.2]*23},{'decay':[float('nan')]*24},
                        {'decay':{'decay':[0.2]*24,'sourceCommit':'wrong'}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.call(**changes)

    def test_vendor_source_bytes_match_the_pinned_manifest(self):
        folder=Path(__file__).resolve().parents[1]/'sparc_controller'/'research'
        manifest=json.loads((folder/'provenance.json').read_text())
        self.assertEqual(manifest['commit'],SOURCE_COMMIT)
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),digest,name)


if __name__=='__main__':unittest.main()

from datetime import datetime, timedelta, timezone
from copy import deepcopy
import unittest
from sparc_controller.billing import cents, compare, summary, delivery_hours
from sparc_controller.sleep_calendar import normalized, updated, apply_window, public
DEFAULT_SETTINGS = {"weekly":{"targetPercent":80,"departureMinutes":420,"fixedStartMinutes":1320,"days":list(range(1,8)),"mode":"smart","enabled":True},"today":None}
from sparc_controller.app_test import AppTestControl, smart_plan_enabled, schedule_goal

NOW = datetime(2026,11,12,18,tzinfo=timezone.utc)
def window(name="one", start=None, end=None):
    return dict(id=name,start=(start or NOW+timedelta(hours=10)).isoformat(),departure=(end or NOW+timedelta(hours=19)).isoformat())
def body(windows=None, enabled=True): return dict(enabled=enabled,windows=[window()] if windows is None else windows,targetPercent=80)

class CombinedBillingTests(unittest.TestCase):
    def test_dated_tariffs_round_each_adjustment_and_change_at_november(self):
        self.assertEqual(cents('2026-11-12T06:00:00-06:00'),2.289)
        self.assertEqual(cents('2026-11-12T22:00:00-06:00'),1.790)
        self.assertEqual(cents('2026-11-12T22:00:00-06:00',standard=True),3.084)
        self.assertNotEqual(cents('2026-10-31T22:00:00-05:00'),cents('2026-11-01T22:00:00-06:00'))
        self.assertIsNone(cents('2027-01-01T00:00:00-06:00'))
    def test_three_gains_telescope_without_clamping_a_loss(self):
        buckets={'2026-11-12T22:00:00-06:00':10}
        row=compare(10,2.0,1.0,buckets,buckets)
        self.assertTrue(row['priced']);self.assertLess(row['schedulingSavingsUSD'],0)
        self.assertAlmostEqual(row['savingsUSD'],row['hourlySavingsUSD']+row['deliverySavingsUSD']+row['schedulingSavingsUSD'])
        self.assertAlmostEqual(row['baselineCostUSD']-row['systemCostUSD'],row['savingsUSD'])
    def test_unknown_prices_or_incomplete_energy_cannot_claim_savings(self):
        b={'2026-11-12T22:00:00-06:00':10}
        for row in (compare(10,None,1,b,b),compare(10,1,1,{},b),compare(10,1,1,b,{})):
            self.assertFalse(row['priced']);self.assertIsNone(row['savingsUSD'])
    def test_summary_is_energy_weighted_and_excludes_unpriced_nights(self):
        b={'2026-11-12T22:00:00-06:00':10};c={'2026-11-12T22:00:00-06:00':20}
        rows=[compare(10,.1,1,b,b),compare(20,3,2,c,c),compare(10,None,None,b,b)]
        t=summary(rows)
        self.assertEqual((t['nightCount'],t['pricedNightCount'],t['unpricedNightCount']),(3,2,1))
        self.assertEqual(t['energyKWh'],30)
        self.assertAlmostEqual(t['savingsPercent'],100*t['savingsUSD']/abs(t['baselineCostUSD']))
    def test_delivery_hours_preserves_both_fall_back_hours(self):
        first=datetime.fromisoformat('2026-11-01T00:00:00-05:00');last=datetime.fromisoformat('2026-11-01T04:00:00-06:00')
        self.assertEqual(len(delivery_hours(first,last)),5)

class SleepCalendarTests(unittest.TestCase):
    def settings(self):
        settings=deepcopy(DEFAULT_SETTINGS)
        settings['virtualCarEnabled']=True
        settings['virtualCarState']=AppTestControl(lambda:NOW).initial_virtual_state()
        settings['virtualCarState']['batteryPercent']=42
        return settings
    def test_enable_preserves_battery_and_uses_event_times_without_a_campaign(self):
        original=self.settings();result=apply_window(updated(original,body(),NOW,'phone-a'),NOW)
        self.assertEqual(result['virtualCarState']['batteryPercent'],42)
        self.assertEqual(original['weekly']['enabled'],DEFAULT_SETTINGS['weekly']['enabled'])
        self.assertFalse(result['weekly']['enabled']);self.assertTrue(smart_plan_enabled(result,NOW))
        self.assertNotIn('campaignId',result['overnightRun'])
        self.assertEqual(schedule_goal(result,NOW).ready_by,datetime.fromisoformat(window()['departure'])-timedelta(minutes=30))
    def test_edit_same_event_preserves_progress_and_replans(self):
        result=apply_window(updated(self.settings(),body(),NOW,'a'),NOW)
        result['virtualCarState'].update(energyKWh=5,cost=.4,energyByHour={'x':5})
        result['smartChargePlan']={'old':True}
        moved=body([window(end=NOW+timedelta(hours=20))])
        result=apply_window(updated(result,moved,NOW,'a'),NOW)
        self.assertEqual(result['virtualCarState']['energyKWh'],5);self.assertNotIn('smartChargePlan',result)
        self.assertEqual(result['overnightRun']['departure'],moved['windows'][0]['departure'])
    def test_next_event_advances_without_resetting_battery(self):
        windows=[window(),window('two',NOW+timedelta(days=1,hours=10),NOW+timedelta(days=1,hours=19))]
        result=apply_window(updated(self.settings(),body(windows),NOW,'a'),NOW)
        result['virtualCarState'].update(batteryPercent=79,energyKWh=40)
        result=apply_window(result,NOW+timedelta(hours=20))
        self.assertEqual(result['overnightRun']['calendarEventId'],'two')
        self.assertEqual(result['virtualCarState']['batteryPercent'],79);self.assertEqual(result['virtualCarState']['energyKWh'],0)
    def test_deleted_or_exhausted_event_holds_without_fake_times(self):
        result=apply_window(updated(self.settings(),body(),NOW,'a'),NOW)
        result=apply_window(updated(result,body([]),NOW,'a'),NOW)
        self.assertNotIn('overnightRun',result);self.assertIsNone(result['today']);self.assertIsNone(public(result,NOW)['nextWake'])
    def test_disconnect_restores_weekly_and_other_phone_cannot_take_over(self):
        original=self.settings();result=updated(original,body(),NOW,'a')
        with self.assertRaises(ValueError):updated(result,body(),NOW,'b')
        restored=updated(apply_window(result,NOW),body([],False),NOW,'a')
        self.assertEqual(restored['weekly'],original['weekly']);self.assertNotIn('overnightRun',restored)
    def test_invalid_private_overlapping_expired_and_naive_events_are_rejected(self):
        cases=[body([dict(window(),title='Private')]),body([window(),window('two')]),body([window(start=NOW-timedelta(hours=3),end=NOW-timedelta(hours=1))]),body([dict(window(),start='2026-11-12T22:00:00')])]
        for value in cases:
            with self.subTest(value=value),self.assertRaises(ValueError):normalized(value,NOW)

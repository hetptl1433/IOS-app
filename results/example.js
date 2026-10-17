// One real night for results/how-it-works.html, written by experiments/scenarios/worked_example.py.
// Every number comes from sparc.backtest.simulate.
window.SPARC_EXAMPLE = {
 "night": "2025-12-01",
 "weekday": "Monday",
 "driver": {
  "name": "Average",
  "battery_kwh": 15,
  "efficiency": 0.9,
  "grid_kwh": 16.667,
  "power_kw": 11.5,
  "slot_kwh": 0.9583,
  "arrive": "20:00",
  "depart": "07:00",
  "reserve_min": 30
 },
 "hours": [
  {
   "label": "8 p.m. to 9 p.m.",
   "price": 7.2,
   "day_ahead": 7.6,
   "standard": 6.228,
   "tod": 3.747,
   "default_kwh": 11.5,
   "plan_kwh": 0.0
  },
  {
   "label": "9 p.m. to 10 p.m.",
   "price": 5.0,
   "day_ahead": 6.8,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 5.1667,
   "plan_kwh": 0.0
  },
  {
   "label": "10 p.m. to 11 p.m.",
   "price": 4.6,
   "day_ahead": 6.0,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "11 p.m. to 12 a.m.",
   "price": 6.9,
   "day_ahead": 4.8,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "12 a.m. to 1 a.m.",
   "price": 4.0,
   "day_ahead": 4.3,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "1 a.m. to 2 a.m.",
   "price": 4.0,
   "day_ahead": 4.0,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "2 a.m. to 3 a.m.",
   "price": 4.1,
   "day_ahead": 3.9,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 11.5
  },
  {
   "label": "3 a.m. to 4 a.m.",
   "price": 3.5,
   "day_ahead": 3.9,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 5.1667
  },
  {
   "label": "4 a.m. to 5 a.m.",
   "price": 3.2,
   "day_ahead": 4.3,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "5 a.m. to 6 a.m.",
   "price": 4.2,
   "day_ahead": 6.1,
   "standard": 6.228,
   "tod": 2.984,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  },
  {
   "label": "6 a.m. to 7 a.m.",
   "price": 4.9,
   "day_ahead": 7.4,
   "standard": 6.228,
   "tod": 4.009,
   "default_kwh": 0.0,
   "plan_kwh": 0.0
  }
 ],
 "default": {
  "supply": 1.0863,
  "delivery": 1.038,
  "cost": 2.1243
 },
 "plan": {
  "supply": 0.6523,
  "delivery": 0.4973,
  "cost": 1.1497
 },
 "delivery_rates": {
  "standard": [
   [
    0,
    6.228
   ]
  ],
  "tod": [
   [
    0,
    2.984
   ],
   [
    6,
    4.009
   ],
   [
    13,
    10.712
   ],
   [
    19,
    3.747
   ],
   [
    21,
    2.984
   ]
  ]
 },
 "period": {
  "first": "2025-04-02",
  "last": "2026-10-14",
  "nights": 554,
  "default_mean": 1.80242,
  "plan_mean": 0.82845,
  "hindsight_mean": 0.72584,
  "saved_per_year": 355.4987
 }
};

// Every number on results/index.html, with the results file it comes from.
// The page reads window.SPARC_RESULTS. Edit a value here and reload the page to see it.
window.SPARC_RESULTS = {
  // experiments/README.md, experiments/scenarios/results/pitch_all_strategies.txt and
// experiments/battery_buffer/results/buffer_headline.txt. Pitch setup, nights 2025-04-02 to 2026-10-14.
// $ a year saved against the default setup (standard delivery, charging on plug-in), Time-of-Day delivery.
  headline: [
    {"driver": "Average", "kwh_per_day": 15, "miles_per_day": 37, "default_per_night": 1.8, "rate_alone": 165, "plan": 355, "plan_pct": 54, "hindsight": 393, "buffer": 410, "buffer_pct": 62, "buffer_vs_plan": 54, "nights": 554},
    {"driver": "Moderate", "kwh_per_day": 30, "miles_per_day": 75, "default_per_night": 3.43, "rate_alone": 363, "plan": 643, "plan_pct": 51, "hindsight": 697, "buffer": 697, "buffer_pct": 56, "buffer_vs_plan": 54, "nights": 554},
    {"driver": "Heavy", "kwh_per_day": 80, "miles_per_day": 200, "default_per_night": 8.21, "rate_alone": 1020, "plan": 1224, "plan_pct": 41, "hindsight": 1297, "buffer": 1233, "buffer_pct": 41, "buffer_vs_plan": 10, "nights": 554},
  ],
  // experiments/scenarios/results/pitch_all_strategies.txt; the battery buffer row from
// experiments/battery_buffer/results/buffer_headline.txt. $ a year and % cheaper against the default setup.
  strategies: [
    {"strategy": "Charge on plug-in (Time-of-Day rate alone)", "family": "Rate alone", "Average": 165, "Average_pct": 25, "Moderate": 363, "Moderate_pct": 29, "Heavy": 1020, "Heavy_pct": 34},
    {"strategy": "Timer 21:00", "family": "Timer", "Average": 247, "Average_pct": 38, "Moderate": 472, "Moderate_pct": 38, "Heavy": 1165, "Heavy_pct": 39},
    {"strategy": "Timer 22:00", "family": "Timer", "Average": 283, "Average_pct": 43, "Moderate": 534, "Moderate_pct": 43, "Heavy": 1210, "Heavy_pct": 40},
    {"strategy": "Timer 23:00", "family": "Timer", "Average": 305, "Average_pct": 46, "Moderate": 579, "Moderate_pct": 46, "Heavy": 1167, "Heavy_pct": 39},
    {"strategy": "Timer 00:00", "family": "Timer", "Average": 331, "Average_pct": 50, "Moderate": 616, "Moderate_pct": 49, "Heavy": 1167, "Heavy_pct": 39},
    {"strategy": "Timer 01:00", "family": "Timer", "Average": 346, "Average_pct": 53, "Moderate": 638, "Moderate_pct": 51, "Heavy": 1167, "Heavy_pct": 39},
    {"strategy": "Below 2¢", "family": "Price cap", "Average": 290, "Average_pct": 44, "Moderate": 552, "Moderate_pct": 44, "Heavy": 1166, "Heavy_pct": 39},
    {"strategy": "Below 3¢", "family": "Price cap", "Average": 290, "Average_pct": 44, "Moderate": 553, "Moderate_pct": 44, "Heavy": 1173, "Heavy_pct": 39},
    {"strategy": "Below 5¢", "family": "Price cap", "Average": 228, "Average_pct": 35, "Moderate": 465, "Moderate_pct": 37, "Heavy": 1147, "Heavy_pct": 38},
    {"strategy": "Day-ahead plan", "family": "SPARC plan", "Average": 355, "Average_pct": 54, "Moderate": 643, "Moderate_pct": 51, "Heavy": 1224, "Heavy_pct": 41},
    {"strategy": "Day-ahead + live prices", "family": "Live", "Average": 348, "Average_pct": 53, "Moderate": 641, "Moderate_pct": 51, "Heavy": 1228, "Heavy_pct": 41},
    {"strategy": "Forecast: Ridge, refit monthly", "family": "Forecast", "Average": 343, "Average_pct": 52, "Moderate": 635, "Moderate_pct": 51, "Heavy": 1222, "Heavy_pct": 41},
    {"strategy": "Forecast: Ridge + weather, refit monthly", "family": "Forecast", "Average": 343, "Average_pct": 52, "Moderate": 635, "Moderate_pct": 51, "Heavy": 1223, "Heavy_pct": 41},
    {"strategy": "Forecast: LightGBM, refit monthly", "family": "Forecast", "Average": 345, "Average_pct": 52, "Moderate": 636, "Moderate_pct": 51, "Heavy": 1225, "Heavy_pct": 41},
    {"strategy": "Forecast: LightGBM + weather, refit monthly", "family": "Forecast", "Average": 345, "Average_pct": 52, "Moderate": 636, "Moderate_pct": 51, "Heavy": 1226, "Heavy_pct": 41},
    {"strategy": "Battery buffer", "family": "Battery buffer", "Average": 410, "Average_pct": 62, "Moderate": 697, "Moderate_pct": 56, "Heavy": 1233, "Heavy_pct": 41},
    {"strategy": "Perfect hindsight", "family": "Hindsight", "Average": 393, "Average_pct": 60, "Moderate": 697, "Moderate_pct": 56, "Heavy": 1297, "Heavy_pct": 43},
  ],
  // experiments/strategies/results/sweep_2025-04-02_2026-01-01.txt (run 1): the top 20 strategies ranked
// on 2025 nights, scored on 2026 nights. $ a year against the day-ahead plan, ± one paired standard error.
  sweep: [
    {"rank": 1, "strategy": "Live, fitted persistence, stickiness 0, risk penalty 0.5", "family": "Live prices", "Average": -4, "Average_se": 6, "Moderate": -4, "Moderate_se": 10, "Heavy": 3, "Heavy_se": 10},
    {"rank": 2, "strategy": "Live, fitted persistence, stickiness 0, risk penalty 0.25", "family": "Live prices", "Average": -3, "Average_se": 6, "Moderate": -2, "Moderate_se": 10, "Heavy": 2, "Heavy_se": 10},
    {"rank": 3, "strategy": "Live, none persistence, stickiness 0", "family": "Live prices", "Average": -1, "Average_se": 6, "Moderate": 3, "Moderate_se": 7, "Heavy": 9, "Heavy_se": 7},
    {"rank": 4, "strategy": "Live, fitted persistence, stickiness 0.1", "family": "Live prices", "Average": -3, "Average_se": 6, "Moderate": -2, "Moderate_se": 10, "Heavy": 1, "Heavy_se": 11},
    {"rank": 5, "strategy": "Live, none persistence, stickiness 0.1", "family": "Live prices", "Average": -1, "Average_se": 6, "Moderate": 4, "Moderate_se": 7, "Heavy": 7, "Heavy_se": 7},
    {"rank": 6, "strategy": "Live, fitted persistence, stickiness 0", "family": "Live prices", "Average": -3, "Average_se": 6, "Moderate": -3, "Moderate_se": 10, "Heavy": 1, "Heavy_se": 11},
    {"rank": 7, "strategy": "Live, fitted persistence, stickiness 0, risk penalty 1", "family": "Live prices", "Average": -5, "Average_se": 6, "Moderate": -7, "Moderate_se": 10, "Heavy": 6, "Heavy_se": 9},
    {"rank": 8, "strategy": "Live, half persistence, stickiness 0.1", "family": "Live prices", "Average": -1, "Average_se": 6, "Moderate": -3, "Moderate_se": 10, "Heavy": 2, "Heavy_se": 9},
    {"rank": 9, "strategy": "Live, half persistence, stickiness 0", "family": "Live prices", "Average": -1, "Average_se": 6, "Moderate": -4, "Moderate_se": 10, "Heavy": 3, "Heavy_se": 9},
    {"rank": 10, "strategy": "Live, none persistence, stickiness 0.25", "family": "Live prices", "Average": -2, "Average_se": 6, "Moderate": 2, "Moderate_se": 7, "Heavy": 7, "Heavy_se": 7},
    {"rank": 11, "strategy": "Live, fitted persistence, stickiness 0.25", "family": "Live prices", "Average": -3, "Average_se": 6, "Moderate": -3, "Moderate_se": 10, "Heavy": 2, "Heavy_se": 11},
    {"rank": 12, "strategy": "Live, fitted persistence, stickiness 0.5, risk penalty 0.5", "family": "Live prices", "Average": -5, "Average_se": 6, "Moderate": -3, "Moderate_se": 10, "Heavy": 0, "Heavy_se": 10},
    {"rank": 13, "strategy": "Live, fitted persistence, stickiness 0.5, risk penalty 1", "family": "Live prices", "Average": -7, "Average_se": 6, "Moderate": -6, "Moderate_se": 10, "Heavy": 5, "Heavy_se": 9},
    {"rank": 14, "strategy": "Live, half persistence, stickiness 0.25", "family": "Live prices", "Average": -3, "Average_se": 6, "Moderate": -3, "Moderate_se": 10, "Heavy": 2, "Heavy_se": 9},
    {"rank": 15, "strategy": "Forecast ridge, weight 0.75, stickiness 0", "family": "Learned forecast", "Average": -2, "Average_se": 3, "Moderate": -1, "Moderate_se": 2, "Heavy": 2, "Heavy_se": 3},
    {"rank": 16, "strategy": "Forecast ridge, weight 1, stickiness 0", "family": "Learned forecast", "Average": -3, "Average_se": 3, "Moderate": -1, "Moderate_se": 2, "Heavy": 2, "Heavy_se": 3},
    {"rank": 17, "strategy": "Forecast ridge, weight 0.25, stickiness 0", "family": "Learned forecast", "Average": -2, "Average_se": 2, "Moderate": 0, "Moderate_se": 1, "Heavy": 2, "Heavy_se": 1},
    {"rank": 18, "strategy": "Live, fitted persistence, stickiness 0.5, risk penalty 0.25", "family": "Live prices", "Average": -8, "Average_se": 7, "Moderate": -3, "Moderate_se": 10, "Heavy": -1, "Heavy_se": 10},
    {"rank": 19, "strategy": "Forecast ridge, weight 0.5, stickiness 0", "family": "Learned forecast", "Average": -2, "Average_se": 3, "Moderate": -1, "Moderate_se": 1, "Heavy": 3, "Heavy_se": 2},
    {"rank": 20, "strategy": "Forecast ridge+weather, weight 0.75, stickiness 0", "family": "Learned forecast", "Average": -1, "Average_se": 3, "Moderate": 0, "Moderate_se": 1, "Heavy": 2, "Heavy_se": 3},
    {"rank": null, "strategy": "Perfect hindsight", "family": "Hindsight", "Average": 45, "Average_se": 6, "Moderate": 68, "Moderate_se": 9, "Heavy": 89, "Heavy_se": 9},
  ],
  // experiments/battery_buffer/results/buffer_skip_2024-04-20.txt, row 'fill-up + live q20 + skip q80'.
// $ a year over the nightly plan, ± one standard error from resampling months.
  buffer_years: [
    {"driver": "Average", "period": "2024", "gain": 29, "se": 7},
    {"driver": "Average", "period": "2025", "gain": 49, "se": 10},
    {"driver": "Average", "period": "2026", "gain": 75, "se": 30},
    {"driver": "Average", "period": "2026 without the storm", "gain": 55, "se": 14},
    {"driver": "Moderate", "period": "2024", "gain": 34, "se": 11},
    {"driver": "Moderate", "period": "2025", "gain": 51, "se": 12},
    {"driver": "Moderate", "period": "2026", "gain": 74, "se": 24},
    {"driver": "Moderate", "period": "2026 without the storm", "gain": 58, "se": 15},
    {"driver": "Heavy", "period": "2024", "gain": 7, "se": 2},
    {"driver": "Heavy", "period": "2025", "gain": 10, "se": 2},
    {"driver": "Heavy", "period": "2026", "gain": 12, "se": 3},
    {"driver": "Heavy", "period": "2026 without the storm", "gain": 12, "se": 2},
  ],
  // experiments/battery_buffer/results/buffer_skip_2024-04-20.txt, 'each rule against the one it adds to'.
  buffer_parts: [
    {"driver": "Average", "part": "Fill up on cheap nights", "y2024": 19, "y2025": 33, "y2026": 35},
    {"driver": "Average", "part": "Live top-ups, added to fill-up", "y2024": 7, "y2025": 14, "y2026": 34},
    {"driver": "Average", "part": "Skipping spikes, added to both", "y2024": 3, "y2025": 3, "y2026": 6},
    {"driver": "Moderate", "part": "Fill up on cheap nights", "y2024": 19, "y2025": 31, "y2026": 24},
    {"driver": "Moderate", "part": "Live top-ups, added to fill-up", "y2024": 11, "y2025": 16, "y2026": 41},
    {"driver": "Moderate", "part": "Skipping spikes, added to both", "y2024": 4, "y2025": 4, "y2026": 9},
    {"driver": "Heavy", "part": "Fill up on cheap nights", "y2024": 3, "y2025": 4, "y2026": 4},
    {"driver": "Heavy", "part": "Live top-ups, added to fill-up", "y2024": 3, "y2025": 5, "y2026": 6},
    {"driver": "Heavy", "part": "Skipping spikes, added to both", "y2024": 1, "y2025": 2, "y2026": 3},
  ],
  // experiments/battery_buffer/results/buffer_bound_2024-04-20.txt ($ a year over the nightly plan);
// 'rules' is the full buffer from buffer_skip_2024-04-20.txt.
  buffer_bound: [
    {"driver": "Average", "year": "2024", "rules": 29, "nightly_hindsight": 24, "buffer_hindsight": 71, "day_ahead_foresight": 26},
    {"driver": "Average", "year": "2025", "rules": 49, "nightly_hindsight": 39, "buffer_hindsight": 98, "day_ahead_foresight": 46},
    {"driver": "Average", "year": "2026", "rules": 75, "nightly_hindsight": 45, "buffer_hindsight": 130, "day_ahead_foresight": 28},
    {"driver": "Moderate", "year": "2024", "rules": 34, "nightly_hindsight": 33, "buffer_hindsight": 78, "day_ahead_foresight": 23},
    {"driver": "Moderate", "year": "2025", "rules": 51, "nightly_hindsight": 55, "buffer_hindsight": 116, "day_ahead_foresight": 50},
    {"driver": "Moderate", "year": "2026", "rules": 74, "nightly_hindsight": 68, "buffer_hindsight": 155, "day_ahead_foresight": 27},
    {"driver": "Heavy", "year": "2024", "rules": 7, "nightly_hindsight": 35, "buffer_hindsight": 44, "day_ahead_foresight": 7},
    {"driver": "Heavy", "year": "2025", "rules": 10, "nightly_hindsight": 62, "buffer_hindsight": 77, "day_ahead_foresight": 10},
    {"driver": "Heavy", "year": "2026", "rules": 12, "nightly_hindsight": 89, "buffer_hindsight": 107, "day_ahead_foresight": 11},
  ],
  // experiments/scenarios/results/plug_in_times.txt, Time-of-Day rows: $ a year over charging on plug-in.
  plug_in: [
    {"driver": "Average", "plug_in": "8 p.m.", "plan": 190, "live": 182, "hindsight": 228},
    {"driver": "Average", "plug_in": "11 p.m.", "plan": 51, "live": 46, "hindsight": 85},
    {"driver": "Moderate", "plug_in": "8 p.m.", "plan": 280, "live": 279, "hindsight": 334},
    {"driver": "Moderate", "plug_in": "11 p.m.", "plan": 66, "live": 67, "hindsight": 113},
    {"driver": "Heavy", "plug_in": "8 p.m.", "plan": 203, "live": 208, "hindsight": 276},
    {"driver": "Heavy", "plug_in": "11 p.m.", "plan": 27, "live": 30, "hindsight": 77},
  ],
  // Errors: experiments/foundation_models.py docstring and experiments/scenarios/results/pitch_all_strategies.txt.
// Savings: experiments/forecasting/README.md and pitch_all_strategies.txt. Cents per kWh; $ a year.
  forecasts: [
    {"model": "Day-ahead price", "family": "Published day-ahead", "mean_error": 1.872, "rms_error": 6.323, "Average": 355, "Moderate": 643, "Heavy": 1224},
    {"model": "Ridge", "family": "Trained by SPARC", "mean_error": 1.872, "rms_error": 5.632, "Average": 343, "Moderate": 635, "Heavy": 1222},
    {"model": "Ridge + weather", "family": "Trained by SPARC", "mean_error": 1.88, "rms_error": 5.595, "Average": 343, "Moderate": 635, "Heavy": 1223},
    {"model": "LightGBM", "family": "Trained by SPARC", "mean_error": 1.825, "rms_error": 5.976, "Average": 345, "Moderate": 636, "Heavy": 1225},
    {"model": "LightGBM + weather", "family": "Trained by SPARC", "mean_error": 1.842, "rms_error": 6.011, "Average": 345, "Moderate": 636, "Heavy": 1226},
    {"model": "Chronos-Bolt-small", "family": "Foundation model", "mean_error": 1.695, "rms_error": 5.501, "Average": 333, "Moderate": 620, "Heavy": 1205},
    {"model": "TiRex", "family": "Foundation model", "mean_error": 1.626, "rms_error": 5.579, "Average": 337, "Moderate": 624, "Heavy": 1208},
    {"model": "TimesFM 2.5", "family": "Foundation model", "mean_error": 1.67, "rms_error": 5.657, "Average": 337, "Moderate": 619, "Heavy": 1209},
    {"model": "Chronos-2, no covariates", "family": "Foundation model", "mean_error": 1.61, "rms_error": 5.352, "Average": 336, "Moderate": 623, "Heavy": 1210},
    {"model": "Chronos-2 + day-ahead", "family": "Foundation model", "mean_error": 1.523, "rms_error": 5.131, "Average": 340, "Moderate": 629, "Heavy": 1221},
    {"model": "TimesFM 3.0 + day-ahead", "family": "Foundation model", "mean_error": 1.514, "rms_error": 4.987, "Average": 341, "Moderate": 634, "Heavy": 1220},
  ],
  // RMS error, cents per kWh. Foundation models and seasonal naive: experiments/foundation_models.py docstring and
// experiments/forecasting/results/foundation_models_baselines.txt. Ridge and LightGBM: pitch_all_strategies.txt.
  accuracy: [
    {"model": "Day-ahead price", "family": "Published day-ahead", "rms_error": 6.323, "day_ahead_rms": 6.323},
    {"model": "Seasonal naive", "family": "Simple baseline", "rms_error": 7.358, "day_ahead_rms": 6.323},
    {"model": "Ridge", "family": "Trained by SPARC", "rms_error": 5.632, "day_ahead_rms": 6.32},
    {"model": "Ridge + weather", "family": "Trained by SPARC", "rms_error": 5.595, "day_ahead_rms": 6.32},
    {"model": "LightGBM", "family": "Trained by SPARC", "rms_error": 5.976, "day_ahead_rms": 6.32},
    {"model": "LightGBM + weather", "family": "Trained by SPARC", "rms_error": 6.011, "day_ahead_rms": 6.32},
    {"model": "Chronos-Bolt-small", "family": "Foundation model", "rms_error": 5.501, "day_ahead_rms": 6.323},
    {"model": "TiRex", "family": "Foundation model", "rms_error": 5.579, "day_ahead_rms": 6.323},
    {"model": "TimesFM 2.5", "family": "Foundation model", "rms_error": 5.657, "day_ahead_rms": 6.323},
    {"model": "Chronos-2, no covariates", "family": "Foundation model", "rms_error": 5.352, "day_ahead_rms": 6.323},
    {"model": "Chronos-2 + day-ahead", "family": "Foundation model", "rms_error": 5.131, "day_ahead_rms": 6.323},
    {"model": "TimesFM 3.0 + day-ahead", "family": "Foundation model", "rms_error": 4.987, "day_ahead_rms": 6.323},
  ],
};

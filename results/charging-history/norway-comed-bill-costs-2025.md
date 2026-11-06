# Norwegian sessions: effect of monthly ComEd bill components

33,784 identical completed sessions; 431245.357 kWh; 2025 ComEd scenario.

Savings use immediate hourly-supply charging on standard study delivery as the baseline, recalculated on each cost basis.

Baseline costs: existing study $44,354.70; with monthly volume components $57,528.35; with allocated account charges $71,432.13; also including Glenview $73,829.85.

| Method | Delivery | Existing study saving | Monthly volume components | With normalized account costs | With account costs + Glenview |
|---|---|---:|---:|---:|---:|
| Immediate charging | standard | $0.00 (0.00%) | $0.00 (0.00%) | $0.00 (0.00%) | $0.00 (0.00%) |
| Timer 01:00 | standard | $4,332.20 (9.77%) | $4,338.23 (7.54%) | $4,338.23 (6.07%) | $4,338.23 (5.88%) |
| SPARC day-ahead | standard | $5,414.63 (12.21%) | $5,422.99 (9.43%) | $5,422.99 (7.59%) | $5,422.99 (7.35%) |
| SPARC + live prices | standard | $5,879.48 (13.26%) | $5,883.25 (10.23%) | $5,883.25 (8.24%) | $5,883.25 (7.97%) |
| Perfect hindsight (reference) | standard | $7,534.75 (16.99%) | $7,536.79 (13.10%) | $7,536.79 (10.55%) | $7,536.79 (10.21%) |
| Immediate charging | time-of-day | $2,189.36 (4.94%) | $2,189.36 (3.81%) | $2,189.36 (3.06%) | $2,189.36 (2.97%) |
| Timer 01:00 | time-of-day | $12,987.35 (29.28%) | $12,993.39 (22.59%) | $12,993.39 (18.19%) | $12,993.39 (17.60%) |
| SPARC day-ahead | time-of-day | $14,697.09 (33.14%) | $14,705.77 (25.56%) | $14,705.77 (20.59%) | $14,705.77 (19.92%) |
| SPARC + live prices | time-of-day | $15,065.83 (33.97%) | $15,071.81 (26.20%) | $15,071.81 (21.10%) | $15,071.81 (20.41%) |
| Perfect hindsight (reference) | time-of-day | $16,469.69 (37.13%) | $16,471.91 (28.63%) | $16,471.91 (23.06%) | $16,471.91 (22.31%) |

## Component totals

See JSON for each method's itemized dollar components and CSV for all cost bases.

- Exactly the original 33,784 completed sessions and charging schedules; only the bill calculation changes. No replanning against the added monthly charges.
- The original settled hourly supply feeds and prior-period live calibration are retained; monthly-average market prices do not replace hourly variation.
- Primary comparison preserves the original study distribution-facilities rates (standard 6.228 cents/kWh and the existing time-of-day bands). Monthly distribution facilities are not added again.
- Added volume components follow the mapped charging calendar month: transmission, Illinois distribution charge, supplied riders and state electricity excise tax.
- Glenview tax is a separate sensitivity using the user-confirmed Illinois location, not a fact about the original Norwegian addresses.
- Tax normalization assumes a hypothetical 1,000 kWh household month, within the first state/Glenview tiers. The separate household scenarios are never aggregated into one tax account.
- Account-charge sensitivity allocates each session's kWh / 1,000 times one monthly service fee and the capacity rate times an assumed 2.5 kW obligation. The mapped plug-in month fixes this allocation identically across strategies; no monthly bill is charged once per session.
- Existing 2026 study delivery bands remain hypothetical in the 2025 scenario. historicalStandardDeliveryCost separately replaces standard 6.228 with monthly historical distribution; it has no invented historical time-of-day counterpart.
- Hourly transmission uses the supplied fixed-plan proxy; hourly-specific PEA and procurement charges and percentage franchise adjustments are unavailable. Monthly rate notes flag source assumptions.
- All costs aggregate independent sessions from multiple original Norwegian years, mapped to 2025. They are not annual household bills, an actual Norwegian tax calculation, or a simultaneous fleet bill.

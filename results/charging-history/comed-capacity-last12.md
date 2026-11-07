# SPARC: Historical ComEd capacity comparison (12 months)

**Period:** 2025-11 to 2026-10. **Method:** recomputation of capacity components in the checked-in monthly replay.
**Capacity assumption:** 2.5 kW billable capacity obligation (not measured PLC).

| Scenario | 12-month modeled EV-allocated cost | Savings vs fixed |
|---|---:|---:|
| Fixed immediate (capacity embedded in supply) | $1037.62 | Baseline |
| Hourly + SPARC, excluding capacity | $697.95 | $339.67 (32.74%) |
| Hourly + SPARC, with filed capacity | $809.25 | $228.37 (22.01%) |
| Hourly immediate, with filed capacity | $890.60 | $147.03 |

**Capacity impact:** +$111.30 EV-allocated cost versus excluding it. The estimated **whole-household** capacity charge is $272.12 for 12 months.
**SPARC optimization vs immediate hourly (both with capacity):** $81.35 (9.13%).

## Savings attribution: hourly switching vs SPARC optimization

| Benefit | Baseline comparison | 12-month modeled saving |
|---|---|---:|
| Switch fixed to hourly (no optimization) | Fixed − hourly immediate, both capacity-inclusive | $147.03 (14.17% of fixed baseline) |
| SPARC optimization | Hourly immediate − hourly SPARC, both capacity-inclusive | $81.35 (9.13% of hourly baseline) |
| **Combined benefit** | Fixed − hourly SPARC, both capacity-inclusive | **$228.37 (22.01%)** |

The plan-switching benefit is already included in the combined result. **Do not add a published ComEd average savings percentage again:** it would double-count plan-switching savings. These are modeled EV-allocated costs, not an observed whole-home tariff-change estimate.

### Monthly attribution

| Month | Switch to hourly | Additional SPARC | Combined |
|---|---:|---:|---:|
| 2025-11 | $0.43 | $5.02 | $5.46 |
| 2025-12 | $9.13 | $6.49 | $15.62 |
| 2026-01 | $16.30 | $1.06 | $17.36 |
| 2026-02 | $17.10 | $5.59 | $22.69 |
| 2026-03 | $12.64 | $10.16 | $22.81 |
| 2026-04 | $18.52 | $10.25 | $28.77 |
| 2026-05 | $60.33 | $9.69 | $70.02 |
| 2026-06 | $4.55 | $7.72 | $12.27 |
| 2026-07 | $0.16 | $16.17 | $16.34 |
| 2026-08 | $1.62 | $0.10 | $1.71 |
| 2026-09 | $6.49 | $6.95 | $13.44 |
| 2026-10 | $-0.26 | $2.14 | $1.89 |
| **Total** | **$147.03** | **$81.35** | **$228.37** |

## Monthly scenario costs

| Month | Filed capacity $/kW-month | Fixed with capacity embedded | SPARC hourly without capacity | SPARC hourly with capacity | EV-allocated capacity |
|---|---:|---:|---:|---:|---:|
| 2025-11 | 8.31935 | $94.34 | $78.68 | $88.88 | $10.20 |
| 2025-12 | 8.31935 | $98.49 | $73.22 | $82.87 | $9.65 |
| 2026-01 | 8.32925 | $75.00 | $48.89 | $57.64 | $8.75 |
| 2026-02 | 8.32925 | $112.09 | $79.25 | $89.40 | $10.14 |
| 2026-03 | 8.32925 | $98.13 | $65.78 | $75.32 | $9.53 |
| 2026-04 | 8.32925 | $105.18 | $66.62 | $76.40 | $9.78 |
| 2026-05 | 8.32925 | $151.28 | $71.18 | $81.26 | $10.09 |
| 2026-06 | 10.11236 | $70.45 | $48.48 | $58.18 | $9.70 |
| 2026-07 | 10.11236 | $69.82 | $44.62 | $53.48 | $8.86 |
| 2026-08 | 10.11236 | $38.58 | $30.17 | $36.87 | $6.69 |
| 2026-09 | 10.11236 | $65.41 | $42.99 | $51.98 | $8.99 |
| 2026-10 | 10.11236 | $58.86 | $48.06 | $56.98 | $8.91 |
| **Total** | | **$1037.62** | **$697.95** | **$809.25** | **$111.30** |

## Important limitations

- **Not a fresh charging-session simulation:** Reuses the saved replay's monthly scenario costs, then removes the original hourly capacity amount and substitutes the historically filed monthly rate. Capacity does not change the existing scheduling decisions under the fixed-obligation assumption.
- **Not a whole-household bill:** the reported EV capacity is allocated using the simulator's EV-kWh share, while the actual capacity line is billed to the entire account.
- **Unequal calendar-year kWh from overnight spillovers:** fixed/immediate has 5250.8 kWh and SPARC has 5274.4 kWh within these 12 calendar months. Underlying charging needs are shared, but sessions crossing a month/year boundary shift the billed energy. Do not claim this as a perfectly energy-matched annual billing comparison.
- Fixed-plan electricity supply already includes capacity; **do not add hourly capacity to fixed**. For hourly plans, monthly account capacity = filed dollars/kW-month × assumed billable obligation × billing-period coverage, then allocated by EV share solely for an EV-cost comparison.
- Does not demonstrate that SPARC reduces next year's capacity obligation or actual coincident peak demand. The assumed obligation is unchanged between hourly scenarios.
- Historical rate inputs other than capacity contain approximations. The original replay omits hourly-specific HPEA and procurement costs and uses fixed-plan transmission as an hourly proxy. Some charging days have missing prices and are excluded.
- Monthly calendar windows approximate ComEd billing cycles. This report is an estimate, not a verified ComEd bill.

## Official ComEd capacity tariff sources

- November–December 2025: $8.31935 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf).
- January–May 2026: $8.32925 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf).
- June–October 2026: $10.11236 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/399143.pdf).

To reproduce: `node night-shift/run-comed-costs.cjs && node night-shift/report-capacity-last12.cjs`.

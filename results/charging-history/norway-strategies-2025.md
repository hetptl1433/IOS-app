# Norway Dataset 1: every automated strategy (2025 ComEd scenario)

33,784 common completed sessions; 431245.357 kWh. Baseline: $44,354.70.

| Strategy | Standard cost | Timing saved | Time-of-day cost | Total saved |
|---|---:|---:|---:|---:|
| Immediate charging | $44,354.70 | 0.00% | $42,165.34 | 4.94% |
| Timer 00:00 | $40,220.36 | 9.32% | $31,516.70 | 28.94% |
| Timer 01:00 | $40,022.51 | 9.77% | $31,367.35 | 29.28% |
| Timer 02:00 | $40,313.16 | 9.11% | $31,746.46 | 28.43% |
| Timer 03:00 | $40,977.32 | 7.61% | $32,593.76 | 26.52% |
| Timer 04:00 | $42,100.38 | 5.08% | $34,028.96 | 23.28% |
| Timer 05:00 | $43,409.74 | 2.13% | $35,857.41 | 19.16% |
| Timer 06:00 | $43,986.03 | 0.83% | $37,093.38 | 16.37% |
| Timer 07:00 | $43,341.06 | 2.29% | $36,738.35 | 17.17% |
| Timer 08:00 | $43,211.00 | 2.58% | $37,228.54 | 16.07% |
| Timer 09:00 | $43,340.89 | 2.29% | $38,411.67 | 13.40% |
| Timer 10:00 | $43,647.85 | 1.59% | $40,410.83 | 8.89% |
| Timer 11:00 | $44,156.71 | 0.45% | $43,169.22 | 2.67% |
| Timer 12:00 | $44,780.53 | -0.96% | $45,440.15 | -2.45% |
| Timer 13:00 | $45,177.75 | -1.86% | $46,912.42 | -5.77% |
| Timer 14:00 | $45,385.71 | -2.32% | $46,606.83 | -5.08% |
| Timer 15:00 | $45,529.22 | -2.65% | $46,073.38 | -3.87% |
| Timer 16:00 | $45,665.75 | -2.96% | $45,389.66 | -2.33% |
| Timer 17:00 | $45,531.21 | -2.65% | $43,967.05 | 0.87% |
| Timer 18:00 | $45,024.42 | -1.51% | $41,114.73 | 7.30% |
| Timer 19:00 | $43,826.75 | 1.19% | $36,241.66 | 18.29% |
| Timer 20:00 | $42,683.13 | 3.77% | $34,680.85 | 21.81% |
| Timer 21:00 | $41,840.31 | 5.67% | $33,255.84 | 25.02% |
| Timer 22:00 | $41,125.03 | 7.28% | $32,447.49 | 26.85% |
| Timer 23:00 | $40,729.30 | 8.17% | $32,005.73 | 27.84% |
| Price cap 2¢/kWh | $41,577.45 | 6.26% | $36,112.47 | 18.58% |
| Price cap 3¢/kWh | $40,815.50 | 7.98% | $34,939.06 | 21.23% |
| Price cap 5¢/kWh | $41,758.27 | 5.85% | $37,672.40 | 15.07% |
| SPARC day-ahead | $38,940.08 | 12.21% | $29,657.61 | 33.14% |
| SPARC + live prices | $38,475.23 | 13.26% | $29,288.87 | 33.97% |
| Perfect hindsight (reference) | $36,819.96 | 16.99% | $27,885.01 | 37.13% |

Of 35,377 source reports, 33,784 have valid inputs/prices and enough charging capacity. 0 of those are excluded from all cost rows because at least one strategy did not complete.

Completion rates, switching frequency, energy in capacity-risk hours, source hashes, and live calibration are in the CSV and JSON.

- Norway source month/weekday and local plug-in clock mapped to 2025 ComEd dates; original elapsed connection duration.
- All cost rows use the same intersection of completed sessions across all 31 strategies and both tariffs.
- All savings compare with immediate charging on standard delivery. Standard rows isolate timing; time-of-day rows include rate changes.
- Charger power is a Dataset2 estimate; actual energy and unplug deadline are known retrospectively.
- Missing posted intervals use the existing hold-state fallback; the live model falls back to day-ahead without a visible signal.
- All timers can start early to meet the deadline and have a six-hour grace after their scheduled start.
- Thresholds use posted supply prices, with a deadline override. Planners prefer avoiding capacity-risk hours; capacity-charge dollars are excluded.
- Hindsight knows settled prices in advance and retains the planners' capacity-risk preference; it is a reference, not deployable.
- Manual actions and custom charging-window inputs do not define a fixed autonomous strategy and are not assigned invented results.
- Independent scenarios, not measured Norwegian bills, simultaneous fleet demand, or annual savings.

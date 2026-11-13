# Norway historical electricity prices

This directory contains **original-date Norwegian day-ahead spot prices**, used
to replay the available part of Dataset 1 charging history. It contains no
Chicago prices and does not move charging sessions to a different year.

Source: Hofmann, Matthias; Bjarghov, Sigurd; Nessa, Stian (2023).
*Norwegian hourly residential electricity demand data with consumer
characteristics during the European energy crisis*, v3.
[Zenodo, DOI 10.5281/zenodo.8423312](https://doi.org/10.5281/zenodo.8423312).
The record declares [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The [data article](https://pmc.ncbi.nlm.nih.gov/articles/PMC10618698/) describes
the price source and clock-change normalization in sections 3.2.6 and 4.2.

- `source/prices.csv`: original, checksum-verified download. All five areas,
  July 2019 through July 2022, 135,240 source rows.
- `hourly.csv`: normalized replay input for NO1, NO3, NO4 and NO5,
  July 1, 2019 through August 7, 2021, 73,440 hourly rows.
- `provenance.json`: sources, license, checksums, units, assumptions, coverage
  and the four omitted clock-change dates.

The normalized columns are:

| Column | Meaning |
| --- | --- |
| `area` | Norwegian bidding area: NO1, NO3, NO4 or NO5 |
| `hour_utc` | Start of the hourly delivery period, ISO 8601 UTC |
| `hour_local` | The same instant in Europe/Oslo, including its UTC offset |
| `price_nok_kwh` | Source `Price_NOK_MWh / 1000` in NOK/kWh |
| `published_assumed_utc` | Assumed release time: 13:00 Europe/Oslo on the previous date |

For example, NO1 at local midnight on July 1, 2019 is
`2019-06-30T22:00:00Z`, with a price of **0.2658 NOK/kWh**. The source's
`Price_NOK_kWh` column rounds this to 0.27, so the more precise MWh column is
used instead. Negative prices are retained.

These costs compare spot energy as provided by the research dataset. The
calculation adds no household grid fees, supplier markup, taxes, VAT
adjustments, subsidies or fixed charges. It does not reconstruct household
bills. Historical publication timestamps are absent from the source; the
13:00 release is a disclosed simulation assumption.

Source hour labels 1–24 are interpreted as the successive periods of the
local date, beginning at 00:00. The source inserts an averaged hour in spring
and removes the second repeated hour in autumn to force every date to have
24 rows. To avoid using those altered periods, the normalized file omits the
**entire** local dates October 27, 2019; March 29 and October 25, 2020; and
March 28, 2021. A replay must exclude sessions needing those prices, as well
as sessions before the source begins. It must not fill those gaps with prices
from another day or market.

Rebuild offline from the checked-in original:

```sh
python3 night-shift/download-norway-prices.py --source norway-price-data/source/prices.csv
```

To fetch and verify the pinned public source again:

```sh
python3 night-shift/download-norway-prices.py
```

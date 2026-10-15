# Price data, availability, and billing

All internal prices are cents/kWh. CSV timestamps are offset-aware ISO 8601 interval starts and normalized to UTC. Scheduling/display use America/Chicago, including daylight-saving changes. Use an explicit UTC offset for repeated autumn clock times.

## Observation versus forecast

The documented ComEd `type=5minutefeed` endpoint returns published five-minute price data; it does not promise a forward five-minute forecast. `type=currenthouraverage` is the current hour's observed running average. The eventual hourly average remains unknown until all of the hour's readings exist, and settlement can revise prices.

For every simulated five-minute action, cost is computed using the actual complete hourly price, not the individual five-minute tick. Missing observations must not be silently turned into valid full billing hours. The engine rejects uncovered/incomplete simulation hours.

## Publication timing

`timestamp` is when an interval begins. `published_at` is when its observation becomes usable. `day_ahead_available_at` is the publication time of the corresponding day-ahead forecast. The same event timestamp can be known at different times; the simulator checks availability before exposing it to a model.

The synthetic sample includes explicit, synthetic publication times. A CSV can include archived publication vintages. ComEd's historical API does not reconstruct the full revision history or historical receipt timestamps. Downloads therefore assume interval-end observation availability and label this limitation. A backtest on retrospectively revised prices can differ from a forward experiment.

When testing weather covariates, use the weather forecast vintage available when a decision was made. Do not add observed future temperature or a newer revised forecast as if it had already been known.

## Price and bill scope

The reported cost includes simulated charging's grid energy at the hourly energy price and the optional constant variable delivery adder. Fixed monthly charges, capacity obligations, demand charges, tax detail, location tariffs, and battery degradation are outside the model. Negative supply prices are preserved and do not imply a negative total household bill.

## Reproduction

Keep original imports plus source, units, publication assumptions, and timestamp conventions. Saved results record the dataset summary, inputs, model identifiers, warnings, and the resulting timelines. Train/validation information must precede the simulated session; the model layer enforces its artifact cutoff.

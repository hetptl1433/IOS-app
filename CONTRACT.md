# Internal implementation contract

SPARC Lab is a local, simulation-only Python standard-library service with a buildless browser UI. No Tesla credentials or real vehicle commands. Price units are cents/kWh and timestamps are ISO 8601 UTC. Browser schedule inputs are America/Chicago. Repo root is this directory.

## Data (`sparc_lab/data.py`)

- `demo_dataset() -> dict`
- `parse_csv_dataset(text: str, name: str = "Imported data") -> dict`
- `fetch_comed_dataset(start: str, end: str) -> dict` (YYYY-MM-DD, bounded requests/timeouts)
- `dataset_summary(dataset: dict) -> dict` excludes full points; includes id, name, source, is_synthetic, description, start, end, point_count, hourly_count, has_day_ahead, warnings.
- `hourly_records(dataset: dict) -> list[dict]`: complete hours only, sorted; each has timestamp, price (actual mean), open, high, low, close, day_ahead or null, available_at (hour observations available), day_ahead_available_at or null. Missing/incomplete hours must not silently become full billing hours.
- Dataset dict: id,name,source,is_synthetic,description,warnings,points. Point: timestamp (interval start),price (cents),day_ahead or null,published_at (availability timestamp),day_ahead_available_at or null. Duplicate timestamps reject; keep negative/spike prices. CSV is documented interval-start data. Raw ComEd timestamps treated as provider interval labels; warn historical publication/revision vintages unavailable.
- Demo deterministic, 180+ days, ends 2026-10-16T12:00:00Z, five-minute cadence; clearly SYNTHETIC, synthetic day-ahead vintage previous day 17:00 Chicago. Default session 2026-10-14 18:00 Chicago -> 2026-10-15 07:00 Chicago.

## Models (`sparc_lab/models.py`)

- `get_model_catalog(artifacts_dir=None) -> list[dict]`: id,label,description,available,reason,kind,requires_training. IDs seasonal-naive, day-ahead, ridge, lightgbm, chronos-bolt, kronos-mean. Optional models must never silently return another model's output.
- `forecast(model_id, history, future, *, artifact_dir=None) -> list[float]`: history complete hourly records available before issue, future records only timestamp and already-published day_ahead (no price). Aligned finite cents predictions. Artifact model trained_until must not exceed forecast origin; enforce or simulator must reject.
- `train_model(model_id, hourly, artifacts_dir, train_fraction=0.7, config=None) -> dict`: actual built-in ridge training; optional adapters can train when deps installed. Chronological validation and untouched test, no random split, records trained_until. Metadata JSON persistent. Unavailable adapters raise clear ValueError.
- `list_artifacts(artifacts_dir) -> list[dict]`.
- No unsafe pickle loading from UI. Optional weights are local trusted training artifacts.

## Simulator (`sparc_lab/simulator.py`)

- `run_simulation(dataset, config, model_ids, *, artifacts_dir=None) -> dict`.
- config keys arrival,departure (offset-aware ISO),battery_kwh,initial_soc,target_soc,charger_kw,efficiency,delivery_cents,max_price_cents (null allowed),price_policy (ready/strict),reserve_minutes,min_session_minutes,replan_minutes,forecast_context_hours.
- Validate meaningful ranges; overlap data; cap horizon 48h. Exact 5-minute simulation with fractional last energy. Charge kWh grid != battery energy. All charge_now/model/oracle costs billed on actual completed hourly means + optional delivery adder. No five-minute arbitrage. Model decisions never see current-hour final price or future actuals. Known observations can update current-hour estimate, separate from future forecast. Replan but only change action when needed. Hard price cap applies expected price, cannot promise final billing cap. Ready policy can override soft price threshold to preserve deadline; strict never intentionally exceeds predicted cap and reports shortfall.
- Always compare charge-now and perfect-foresight oracle (clearly labeled, not deployable), and selected model strategies. Effective power constant, no vehicle-specific taper; declare assumption. Forecast accuracy separate from cost. Train/test lookahead guards.
- Result config, dataset(summary), assumptions,warnings,strategies,chart,events,summary. Each strategy id,label,cost,energy_kwh,battery_added_kwh,final_soc,ready,shortfall_kwh,savings,savings_pct,mae (nullable),switches,schedule(list),timeline(list),events(list). Timeline timestamp,soc,charging,price_actual,price_predicted,power_kw,cost. Chart list timestamp,actual,day_ahead, forecasts {model_id:prediction}. summary winner_id,best_cost,baseline_cost,savings,savings_pct.

## HTTP / UI

GET /api/bootstrap -> {version,datasets,models,defaults,recent_runs,artifacts}
GET /api/datasets -> {datasets}
GET /api/datasets/{id}/preview -> {dataset,points (last 168 hourly records)}
POST /api/datasets/import {name,csv} -> {dataset}
POST /api/datasets/comed {start,end} -> {dataset}
POST /api/runs {dataset_id,config,model_ids} -> {job_id,status:"queued"}
POST /api/train {dataset_id,model_id,train_fraction,config} -> {job_id,status:"queued"}
GET /api/jobs/{id} -> {id,status:"queued"|"running"|"complete"|"failed",kind,result,error}
GET /api/runs -> {runs}
GET /api/runs/{id} -> full saved result (with id,created_at)
GET /api/runs/{id}/export.csv -> comparative summary CSV
GET /api/artifacts -> {artifacts}
Errors JSON {error:message}, HTTP 400/404/500. Server validates JSON and limits uploads. UI escapes imported/user strings. Long work executes in bounded background thread. Local JSON storage runtime/ ignored by git; loopback binding default. Runs save & reload; UI last selection localStorage.

## UI requirements

Polished responsive dashboard, deep navy sidebar with SPARC wordmark, warm light background, teal primary, legible typography, purposeful charts. Pages Simulator / Data / Models / Experiments (history). Primary workflow: schedule inputs, dataset, selected available models -> Run comparison -> job -> result cards, actual-vs-predicted price chart, per-model charge schedule, SOC chart, comparison table including readiness, cost, savings, forecast error, switches. Display synthetic label prominently, oracle label, forecast caps limitation, real optional-model availability, errors and progress. Data page CSV text/file import + ComEd date fetch. Models page real ridge train, optional installation guidance. Download results CSV. No fake model results. Vanilla HTML/CSS/JS no build/runtime package downloads required.

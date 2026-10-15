"""Five-minute price datasets. All internal prices are cents per grid kWh.

CSV timestamps explicitly mean interval START, and observations normally become
available at the END of that interval. Historical feed vintages are not invented.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
STEP = timedelta(minutes=5)
MAX_POINTS = 220_000


def _dt(value: str, label: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be an ISO 8601 timestamp with a UTC offset.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: {value!r}.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include Z or a UTC offset; local times are ambiguous.")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number in cents/kWh.")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a finite number in cents/kWh.") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite; NaN and infinity are invalid.")
    return result


def _assemble(points: list[dict], name: str, source: str, description: str,
              *, synthetic: bool = False, warnings: list[str] | None = None) -> dict:
    if not points:
        raise ValueError("The dataset contains no five-minute observations.")
    if len(points) > MAX_POINTS:
        raise ValueError(f"Datasets are limited to {MAX_POINTS:,} five-minute points.")
    points.sort(key=lambda p: p["timestamp"])
    notes = list(warnings or [])
    gaps = sum(_dt(b["timestamp"]) - _dt(a["timestamp"]) != STEP
               for a, b in zip(points, points[1:]))
    if gaps:
        notes.append(f"{gaps} gap(s) in five-minute data. Incomplete hours are excluded; simulations crossing them are rejected.")
    digest = hashlib.sha256(json.dumps(points, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return {"id": f"{'demo' if synthetic else 'data'}-{digest}", "name": str(name).strip()[:120] or "Imported data",
            "source": source, "is_synthetic": synthetic, "description": description,
            "warnings": notes, "points": points}


def parse_csv_dataset(text: str, name: str = "Imported data") -> dict:
    """Read explicit interval-start CSV, preserving negative prices and spikes.

    Required: timestamp,price (cents/kWh). Optional: published_at,day_ahead,
    day_ahead_available_at. A day-ahead value requires its historical publication
    timestamp; otherwise its availability cannot be established in a backtest.
    """
    if not isinstance(text, str) or len(text.encode("utf-8")) > 35_000_000:
        raise ValueError("CSV must be text and at most 35 MB.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    headers = reader.fieldnames or []
    if len(set(headers)) != len(headers):
        raise ValueError("CSV contains duplicate column names.")
    required = {"timestamp", "price"}
    allowed = required | {"published_at", "day_ahead", "day_ahead_available_at"}
    if not required.issubset(headers):
        raise ValueError("CSV requires timestamp and price columns. price must be in cents/kWh, not dollars or $/MWh.")
    unknown = set(headers) - allowed
    if unknown:
        raise ValueError("Unknown CSV column(s): " + ", ".join(sorted(unknown)) + ". Use timestamp,price,published_at,day_ahead,day_ahead_available_at.")
    points, seen, vintages = [], set(), {}
    assumed_publication = False
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(f"CSV row {row_number} has too many columns.")
        if not any(str(v or "").strip() for v in row.values()):
            continue
        try:
            stamp = _dt(row.get("timestamp"), "timestamp")
            if stamp.minute % 5 or stamp.second or stamp.microsecond:
                raise ValueError("timestamp must fall on a five-minute boundary.")
            normalized = _iso(stamp)
            if normalized in seen:
                raise ValueError("Duplicate timestamp (including equivalent UTC offsets).")
            seen.add(normalized)
            price = _number(row.get("price"), "price")
            raw_publication = (row.get("published_at") or "").strip()
            publication = _dt(raw_publication, "published_at") if raw_publication else stamp + STEP
            assumed_publication |= not bool(raw_publication)
            if publication < stamp + STEP:
                raise ValueError("published_at cannot precede the end of its observed five-minute interval.")
            raw_da = (row.get("day_ahead") or "").strip()
            raw_da_time = (row.get("day_ahead_available_at") or "").strip()
            if bool(raw_da) != bool(raw_da_time):
                raise ValueError("day_ahead and day_ahead_available_at must both be supplied or both be empty.")
            day_ahead = _number(raw_da, "day_ahead") if raw_da else None
            da_time = _dt(raw_da_time, "day_ahead_available_at") if raw_da_time else None
            hour_key = stamp.replace(minute=0, second=0, microsecond=0)
            if day_ahead is not None:
                vintage = (day_ahead, _iso(da_time))
                if hour_key in vintages and vintages[hour_key] != vintage:
                    raise ValueError("Each billing hour must have one consistent day_ahead value and publication timestamp.")
                vintages[hour_key] = vintage
            points.append({"timestamp": normalized, "price": price, "day_ahead": day_ahead,
                           "published_at": _iso(publication),
                           "day_ahead_available_at": _iso(da_time) if da_time else None})
        except ValueError as exc:
            raise ValueError(f"CSV row {row_number}: {exc}") from exc
        if len(points) > MAX_POINTS:
            raise ValueError(f"Datasets are limited to {MAX_POINTS:,} five-minute points.")
    notes = []
    if assumed_publication:
        notes.append("Missing published_at values assumed available at interval end. Real feed latency and revision vintages are unknown; this is an explicit backtest assumption.")
    if points and not any(p["day_ahead"] is not None for p in points):
        notes.append("No historically timestamped day-ahead prices were imported. The day-ahead strategy is unavailable for this dataset.")
    return _assemble(points, name, "csv", "Imported five-minute interval-start prices in cents/kWh.", warnings=notes)


def hourly_records(dataset: dict) -> list[dict]:
    """Return actual billing means only for twelve distinct complete intervals."""
    grouped = defaultdict(list)
    for point in dataset.get("points", []):
        stamp = _dt(point["timestamp"])
        grouped[stamp.replace(minute=0, second=0, microsecond=0)].append((stamp, point))
    result = []
    for hour, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item[0])
        expected = [hour + index * STEP for index in range(12)]
        if [stamp for stamp, _ in rows] != expected:
            continue
        prices = [p["price"] for _, p in rows]
        da_values = {(p["day_ahead"], p["day_ahead_available_at"]) for _, p in rows if p.get("day_ahead") is not None}
        # CSV rejects conflicting vintages. An arbitrary in-memory dataset must
        # not accidentally pick a convenient value from conflicting rows either.
        if len(da_values) > 1:
            raise ValueError(f"Conflicting day-ahead values in hour {_iso(hour)}.")
        da, da_at = next(iter(da_values)) if da_values else (None, None)
        result.append({"timestamp": _iso(hour), "price": sum(prices) / 12,
                       "open": prices[0], "high": max(prices), "low": min(prices), "close": prices[-1],
                       "day_ahead": da, "day_ahead_available_at": da_at,
                       "available_at": max(p.get("published_at") or _iso(s + STEP) for s, p in rows)})
    return result


def dataset_summary(dataset: dict) -> dict:
    points = dataset.get("points", [])
    hours = hourly_records(dataset)
    result = {key: dataset.get(key) for key in ("id", "name", "source", "is_synthetic", "description")}
    result.update({"start": points[0]["timestamp"] if points else None,
                   "end": _iso(_dt(points[-1]["timestamp"]) + STEP) if points else None,
                   "point_count": len(points), "hourly_count": len(hours),
                   "has_day_ahead": any(h["day_ahead"] is not None for h in hours),
                   "warnings": list(dataset.get("warnings", []))})
    incomplete = len({p["timestamp"][:13] for p in points}) - len(hours)
    if incomplete:
        result["warnings"].append(f"{incomplete} incomplete billing hour(s) excluded from hourly calculations.")
    return result


@lru_cache(maxsize=1)
def demo_dataset() -> dict:
    """Deterministic, deliberately imperfect forecasts for reproducible demos."""
    end = datetime(2026, 10, 16, 12, tzinfo=UTC)
    start = end - timedelta(days=190)
    rng = random.Random(20261015)
    points = []
    hour = start
    while hour < end:
        local = hour.astimezone(CHICAGO)
        h = local.hour
        daily = 3.1 + 2.5 * math.exp(-((h - 18) / 3.5) ** 2) - 1.8 * math.exp(-((h - 3) / 2.5) ** 2)
        season = 1.0 + 0.8 * math.sin((local.timetuple().tm_yday - 150) * 2 * math.pi / 365)
        weekday = 0.65 if local.weekday() < 5 else -0.25
        predictable = daily + season + weekday
        da_price = round(predictable + rng.gauss(0, 0.6), 4)
        # Unforeseen spikes, occasional negative overnight energy, correlated
        # errors within each hour. Synthetic DA is not derived from future truth.
        shock = rng.gauss(0, 0.95)
        if rng.random() < (0.025 if 14 <= h <= 20 else 0.006):
            shock += rng.uniform(9, 42)
        if h <= 5 and rng.random() < 0.09:
            shock -= rng.uniform(3, 8)
        da_day = local.date() - timedelta(days=1)
        da_available = datetime.combine(da_day, time(17), CHICAGO).astimezone(UTC)
        trend = rng.uniform(-1.1, 1.1)
        for i in range(12):
            stamp = hour + i * STEP
            price = round(predictable + shock + trend * (i / 11 - 0.5) + rng.gauss(0, 0.32), 4)
            points.append({"timestamp": _iso(stamp), "price": price, "day_ahead": da_price,
                           "published_at": _iso(stamp + STEP), "day_ahead_available_at": _iso(da_available)})
        hour += timedelta(hours=1)
    return _assemble(points, "SYNTHETIC · Chicago price laboratory", "synthetic", "190 deterministic days of invented prices and imperfect day-ahead forecasts. For software testing only; not historical ComEd observations.", synthetic=True,
                     warnings=["SYNTHETIC DATA: all prices and day-ahead forecasts are invented. Savings are illustrative and do not establish real-world model performance.",
                               "Synthetic day-ahead forecasts become available at 17:00 America/Chicago on the previous calendar day. Observations become available at interval end."])


def fetch_comed_dataset(start: str, end: str) -> dict:
    """Fetch an inclusive range of Chicago calendar dates from the official API.

    Seven-day request chunks, <=31 days, timeout and response-size limits keep
    this operation bounded. Provider UTC interval labels are retained; the API
    does not expose historical publication timestamps or revision vintages.
    """
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValueError("ComEd dates must use YYYY-MM-DD.") from exc
    days = (last - first).days + 1
    if days < 1 or days > 31:
        raise ValueError("Choose an inclusive ComEd date range from 1 to 31 days. Import a CSV for longer histories.")
    start_at = datetime.combine(first, time(), CHICAGO).astimezone(UTC)
    end_at = datetime.combine(last + timedelta(days=1), time(), CHICAGO).astimezone(UTC)
    if start_at > datetime.now(UTC):
        raise ValueError("ComEd historical data cannot start in the future.")
    by_stamp = {}
    chunk_date = first
    while chunk_date <= last:
        chunk_last = min(last, chunk_date + timedelta(days=6))
        query = urlencode({"type": "5minutefeed", "format": "json",
                           "datestart": chunk_date.strftime("%Y%m%d") + "0000",
                           "dateend": chunk_last.strftime("%Y%m%d") + "2355"})
        request = Request("https://hourlypricing.comed.com/api?" + query,
                          headers={"User-Agent": "SPARC-Lab/1.0 (research simulator)", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("ComEd response exceeded the 4 MB per-request limit.")
            payload = json.loads(raw)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"ComEd request failed: {exc}. Retry later or import a CSV.") from exc
        if not isinstance(payload, list):
            raise ValueError("ComEd returned an unexpected response; expected a JSON price array.")
        for record in payload:
            try:
                millis = _number(record["millisUTC"], "ComEd millisUTC")
                stamp = datetime.fromtimestamp(millis / 1000, UTC)
                price = _number(record["price"], "ComEd price")
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                raise ValueError(f"Invalid ComEd price record: {exc}") from exc
            if stamp.minute % 5 or stamp.second or stamp.microsecond:
                raise ValueError("ComEd returned a timestamp outside its five-minute grid.")
            if not start_at <= stamp < end_at:
                continue
            key = _iso(stamp)
            if key in by_stamp and by_stamp[key]["price"] != price:
                raise ValueError(f"ComEd returned conflicting duplicate timestamp {key}.")
            by_stamp[key] = {"timestamp": key, "price": price, "day_ahead": None,
                             "published_at": _iso(stamp + STEP), "day_ahead_available_at": None}
        chunk_date = chunk_last + timedelta(days=1)
    return _assemble(list(by_stamp.values()), f"ComEd · {start} to {end}", "comed",
                     "Official historical five-minute feed; cents/kWh, original provider UTC interval labels.",
                     warnings=["Historical publication latency and revision vintages are unavailable. published_at is assumed to be label + 5 minutes; historical backtests may differ from information received live.",
                               "Original provider interval labels are treated as interval starts for this simulator. Verify interval-label and billing conventions before making operational savings claims.",
                               "The documented historical feed has no day-ahead vintage series. Import timestamped day-ahead CSV data to test that strategy.",
                               "Fetched prices represent energy supply only; delivery, capacity, taxes and other tariff charges are not reconstructed."])

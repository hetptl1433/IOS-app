"""Real ComEd supply prices; missing data never becomes an example tariff."""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import html
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

URL = "https://hourlypricing.comed.com/api?type=currenthouraverage"
HISTORY_URL = "https://hourlypricing.comed.com/api?type=5minutefeed"
MAX_AGE_SECONDS = 20 * 60
TABLE_URL = "https://hourlypricing.comed.com/rrtp/ServletFeed"
TABLE_PAGE_URL = "https://hourlypricing.comed.com/pricing-table-today/"
DAY_AHEAD_REFRESH_SECONDS = 15 * 60
CHICAGO = ZoneInfo("America/Chicago")
HOUR = 3600


class ComEdError(RuntimeError):
    """A public hourly table is missing or changed its expected structure."""


@dataclass(frozen=True)
class HourRow:
    hour: int
    day_ahead: float | None
    real_time: float | None


def at(day, wall):
    return int(datetime.combine(day, wall, tzinfo=CHICAGO).timestamp())


def local(stamp):
    return datetime.fromtimestamp(stamp, CHICAGO)


def parse_price(payload, now):
    if not isinstance(payload, list) or not payload:
        raise ValueError("Missing ComEd price.")
    samples = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Invalid ComEd sample.")
        raw_price, raw_stamp = item.get("price"), item.get("millisUTC")
        if isinstance(raw_price, bool) or isinstance(raw_stamp, bool):
            raise ValueError("Invalid ComEd sample.")
        price, stamp = float(raw_price), float(raw_stamp) / 1000
        if not math.isfinite(price) or not math.isfinite(stamp):
            raise ValueError("Invalid ComEd sample.")
        if stamp > now.timestamp() + 300:
            raise ValueError("Future ComEd sample.")
        samples.append((stamp, price))
    stamp, price = max(samples)
    if now.timestamp() - stamp > MAX_AGE_SECONDS:
        raise ValueError("Stale ComEd sample.")
    return price, stamp


def fetch_bytes(url, maximum=65536, accept="application/json"):
    request = Request(url, headers={"Accept": accept, "Cache-Control": "no-cache", "User-Agent": "SPARC/1.0"})
    # One bounded attempt. Retrying happens asynchronously on the next refresh,
    # and no network operation runs under the controller settings lock.
    with urlopen(request, timeout=10) as response:
        body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("ComEd response too large.")
    return body


def fetch_json(url):
    return json.loads(fetch_bytes(url))


def fetch_price():
    return fetch_json(URL)


def hourly_table_url(day):
    return TABLE_URL + "?" + urlencode({"type": "pricingtabledual", "date": day.strftime("%Y%m%d")})


def fetch_hourly_table(day):
    body = fetch_bytes(hourly_table_url(day), maximum=131072, accept="text/html")
    rows = parse_hourly_table(body, day)
    expected = (at(day + timedelta(days=1), time(0)) - at(day, time(0))) // HOUR
    if len(rows) != expected:
        raise ComEdError("Incomplete ComEd hourly table.")
    if any(not math.isfinite(price) for row in rows for price in (row.day_ahead, row.real_time) if price is not None):
        raise ComEdError("Non-finite ComEd hourly price.")
    return body, rows


# The parser below is copied unchanged from repository main
# 28d838d0d68ee175b56861622dbd0847a9cdced1, sparc/comed.py.
_CELL = r"(n/a|-?\d+(?:\.\d+)?\s*¢)"
_ROW = re.compile(rf"(\d{{1,2}}):00\s*([ap])\.m\.\s*{_CELL}\s*{_CELL}", re.IGNORECASE)


def parse_hourly_table(body: bytes, day: date) -> list[HourRow]:
    """Rows are labelled by the hour's END in Chicago time and run in time order.

    The first row, "12:00 a.m.", is the hour from 11 p.m. the evening before to
    midnight. Each row's label is checked against that rule, so a table that does
    not follow it (for example on a daylight-saving change) is rejected, not misread.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("cp1252")
    text = re.sub(r"<[^>]*>", " ", html.unescape(text))
    rows = _ROW.findall(text)
    if not rows:
        raise ComEdError(f"no hourly rows in the pricing table for {day}")
    first = at(day, time(0)) - HOUR
    result = []
    for index, (hour_label, half, day_ahead, real_time) in enumerate(rows):
        hour = first + index * HOUR
        ends = local(hour + HOUR)
        if (int(hour_label) % 12, half.lower()) != (ends.hour % 12, "a" if ends.hour < 12 else "p"):
            raise ComEdError(f"pricing table for {day}: row {index + 1} is labelled "
                             f"{hour_label}:00 {half}.m., expected {ends:%I:00 %p}")
        result.append(HourRow(hour, _cents(day_ahead), _cents(real_time)))
    return result


def _cents(cell: str) -> float | None:
    return None if cell.lower() == "n/a" else float(cell.rstrip("¢ "))


def hour_key(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class ComEdPricing:
    def __init__(self, clock=None, cache=None, log=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.price = self.stamp = None
        self.samples = {}
        self.day_ahead = {}
        self.settled_hourly = {}
        self.day_ahead_feeds = {}
        self.history_fetched_at = None
        self._next_day_ahead_refresh = 0.0
        self._table_dates = ()
        self.cache = Path(cache) if cache else None
        self.log = Path(log) if log else None
        if self.cache and self.cache.exists():
            try:
                saved = json.loads(self.cache.read_text())
                self.samples = self.parse_history(saved.get("samples", []))
                self.day_ahead = self.parse_hour_cache(saved.get("dayAhead", {}))
                self.settled_hourly = self.parse_hour_cache(saved.get("settledHourly", {}))
                metadata = saved.get("dayAheadFeeds", {})
                if isinstance(metadata, dict):
                    self.day_ahead_feeds = {day: dict(value) for day, value in metadata.items() if isinstance(day, str) and isinstance(value, dict)}
                self.history_fetched_at = saved.get("historyFetchedAt")
                self.price, self.stamp = parse_price(saved.get("quote"), self.clock())
            except (ValueError, TypeError, KeyError, OSError, OverflowError):
                pass

    def state(self):
        fresh = self.stamp is not None and -300 <= self.clock().timestamp() - self.stamp <= MAX_AGE_SECONDS
        return {"source": "ComEd", "kind": "currentHourAverage", "status": "live" if fresh else "unavailable",
                "rateCents": self.price if fresh else None, "historySamples": len(self.samples),
                "observedAt": datetime.fromtimestamp(self.stamp, timezone.utc).isoformat() if self.stamp is not None else None,
                "historyFetchedAt": self.history_fetched_at,
                "dayAheadStatus": "available" if any(hour + HOUR > self.clock().timestamp() for hour in self.day_ahead) else "unavailable",
                "dayAheadSource": TABLE_PAGE_URL,
                "dayAheadHours": len(self.day_ahead), "settledHours": len(self.settled_hourly),
                "dayAheadFeeds": {day: dict(metadata) for day, metadata in self.day_ahead_feeds.items()}}

    def rate_history(self, day=None):
        """One Chicago calendar day of cached supply rates, bounded for BLE frames.

        No network request or accounting mutation. Missing hours remain null, and
        future day-ahead prices stay distinct from observed/settled history.
        """
        now = self.clock().astimezone(timezone.utc)
        today = now.astimezone(CHICAGO).date()
        try:
            chosen = today if day is None else date.fromisoformat(day)
            if day is not None and chosen.isoformat() != day:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Choose a calendar day for rate history.") from None
        if not today-timedelta(days=7) <= chosen <= today:
            raise ValueError("Choose today or one of the previous seven days.")
        start = datetime.combine(chosen, time(), CHICAGO).astimezone(timezone.utc)
        end = datetime.combine(chosen+timedelta(days=1), time(), CHICAGO).astimezone(timezone.utc)
        current = self.state()
        hours = []
        cursor = start
        while cursor < end:
            stamp = int(cursor.timestamp())
            value, status = None, "unavailable"
            if stamp+HOUR <= now.timestamp() and stamp in self.settled_hourly:
                value, status = self.settled_hourly[stamp], "settled"
            elif stamp <= now.timestamp() < stamp+HOUR:
                value = current["rateCents"]
                status = "current" if value is not None else "unavailable"
            elif stamp < now.timestamp():
                samples = [v for t,v in self.samples.items() if stamp <= t < stamp+HOUR and t <= now.timestamp()]
                if samples:
                    value = sum(samples)/len(samples)
                    status = "observed" if len(samples)==12 else "provisional"
            elif stamp in self.day_ahead:
                value, status = self.day_ahead[stamp], "dayAhead"
            hours.append({"start":cursor.isoformat(), "supplyCents":value, "status":status})
            cursor += timedelta(hours=1)
        return {"schemaVersion":1, "source":"ComEd", "timezone":"America/Chicago",
                "day":chosen.isoformat(), "generatedAt":now.isoformat(),
                "currentSupplyCents":current["rateCents"], "observedAt":current["observedAt"],
                "hours":hours}

    def parse_history(self, payload):
        if not isinstance(payload, list) or len(payload) > 3000:
            raise ValueError("Invalid ComEd history.")
        result = {}
        for row in payload:
            price, stamp = parse_price([row], datetime.fromtimestamp(float(row["millisUTC"])/1000, timezone.utc))
            if stamp % 300 or stamp > self.clock().timestamp() + 300:
                raise ValueError("Invalid five-minute timestamp.")
            if stamp >= self.clock().timestamp() - 7*86400:
                result[int(stamp)] = price
        return result

    def parse_hour_cache(self, payload):
        if not isinstance(payload, dict) or len(payload) > 3000:
            raise ValueError("Invalid cached hourly prices.")
        result = {}
        for raw_hour, price in payload.items():
            hour = int(raw_hour)
            if str(hour) != str(raw_hour) or hour % HOUR or type(price) not in (int, float) or not math.isfinite(price):
                raise ValueError("Invalid cached hourly price.")
            if self.clock().timestamp() - 7*86400 <= hour <= self.clock().timestamp() + 3*86400:
                result[hour] = float(price)
        return result

    def energy_cost(self, buckets):
        hours = {}
        for stamp, rate in self.samples.items():
            # API labels are interval starts. HH:00 through HH:55 belong to HH.
            hours.setdefault(hour_key(stamp), []).append(rate)
        cost, missing, final = 0.0, 0.0, True
        for hour, energy in buckets.items():
            stamp = int(datetime.fromisoformat(hour).timestamp())
            prices = hours.get(hour, [])
            if stamp in self.settled_hourly:
                cost += energy * self.settled_hourly[stamp] / 100
            elif not prices:
                missing += energy
                final = False
            else:
                cost += energy * (sum(prices)/len(prices)) / 100
                final = final and len(prices) == 12
        return {"cost": cost, "unpricedEnergyKWh": missing, "final": final}

    def record(self, value):
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a") as stream:
                stream.write(json.dumps({"receivedAt": self.clock().isoformat(), **value}, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    async def refresh(self):
        for kind, url in (("currentHourAverage", URL), ("fiveMinuteHistory", HISTORY_URL)):
            try:
                payload = await asyncio.to_thread(fetch_price if url == URL else lambda: fetch_json(url))
                if url == URL:
                    self.price, self.stamp = parse_price(payload, self.clock())
                else:
                    self.samples.update(self.parse_history(payload))
                    self.history_fetched_at = self.clock().isoformat()
                    cutoff = self.clock().timestamp() - 7*86400
                    self.samples = {stamp: rate for stamp, rate in self.samples.items() if stamp >= cutoff}
                self.record({"type": kind, "url": url, "data": payload})
            except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
                self.record({"type": "feedError", "feed": kind, "error": type(exc).__name__})
        await self.refresh_day_ahead()
        if self.cache:
            save_json(self.cache, {"quote": [] if self.stamp is None else [{"millisUTC": str(int(self.stamp*1000)), "price": str(self.price)}],
                                  "samples": [{"millisUTC": str(stamp*1000), "price": str(price)} for stamp, price in self.samples.items()],
                                  "dayAhead": self.day_ahead, "settledHourly": self.settled_hourly,
                                  "dayAheadFeeds": self.day_ahead_feeds, "historyFetchedAt": self.history_fetched_at})

    async def refresh_day_ahead(self):
        now = self.clock()
        today = now.astimezone(CHICAGO).date()
        dates = tuple(today + timedelta(days=offset) for offset in (-1, 0, 1))
        if dates == self._table_dates and now.timestamp() < self._next_day_ahead_refresh:
            return
        self._table_dates = dates
        retry = False
        for day in dates:
            key = day.isoformat()
            url = hourly_table_url(day)
            old_metadata = self.day_ahead_feeds.get(key, {})
            try:
                body, rows = await asyncio.to_thread(fetch_hourly_table, day)
                ahead = {row.hour: row.day_ahead for row in rows if row.day_ahead is not None}
                settled = {row.hour: row.real_time for row in rows if row.real_time is not None and row.hour + HOUR <= now.timestamp()}
                self.day_ahead.update(ahead)
                self.settled_hourly.update(settled)
                metadata = {"date": key, "source": url, "fetchedAt": self.clock().isoformat(),
                            "firstSeenAt": old_metadata.get("firstSeenAt") or self.clock().isoformat(),
                            "firstAvailableAt": old_metadata.get("firstAvailableAt") or (self.clock().isoformat() if ahead else None),
                            "status": "available" if ahead else "notPublished", "availableHours": len(ahead),
                            "settledHours": len(settled), "labels": "hourEndingChicago"}
                self.day_ahead_feeds[key] = metadata
                self.record({"type": "hourlyTable", "url": url, "metadata": metadata,
                             "data": body.decode("utf-8", errors="replace"),
                             "rows": [{"hour": row.hour, "dayAheadCents": row.day_ahead, "settledCents": row.real_time} for row in rows]})
                retry = retry or not ahead
            except (ComEdError, ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
                retry = True
                self.day_ahead_feeds[key] = {**old_metadata, "date": key, "source": url,
                                             "lastAttemptAt": self.clock().isoformat(), "lastError": type(exc).__name__}
                self.record({"type": "feedError", "feed": "hourlyTable", "date": key, "url": url, "error": type(exc).__name__})
        cutoff = now.timestamp() - 7*86400
        self.day_ahead = {hour: price for hour, price in self.day_ahead.items() if hour >= cutoff}
        self.settled_hourly = {hour: price for hour, price in self.settled_hourly.items() if hour >= cutoff}
        earliest = (today - timedelta(days=7)).isoformat()
        self.day_ahead_feeds = {key: metadata for key, metadata in self.day_ahead_feeds.items() if key >= earliest}
        self._next_day_ahead_refresh = now.timestamp() + (60 if retry else DAY_AHEAD_REFRESH_SECONDS)

    async def run(self):
        while True:
            await self.refresh()
            await asyncio.sleep(60)

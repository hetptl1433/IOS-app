"""ComEd Hourly Pricing data.

Two sources, both in cents/kWh:

* The documented 5-minute API (https://hourlypricing.comed.com/hp-api/). Each price
  is labelled with the start of its interval: averaging the labels HH:00-HH:55
  reproduces ComEd's published hourly prices (checked against 2026-10-16, when the
  1-2 a.m. CT hour came in at 7.9 cents).
* The hourly table behind https://hourlypricing.comed.com/pricing-table-today/. It
  is ComEd's only source of day-ahead prices, but it is not part of the documented
  API and can change without notice. PJM Data Miner is the official alternative.
"""
from __future__ import annotations

import html
import json
import math
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .clock import HOUR, SLOT, at, local

API_URL = "https://hourlypricing.comed.com/api"
TABLE_URL = "https://hourlypricing.comed.com/rrtp/ServletFeed"
USER_AGENT = "sparc/0.2 (home EV charging research; https://github.com/hetptl1433/sparc)"
MAX_RESPONSE = 8_000_000
RETRY_STATUS = {429, 500, 502, 503, 504}

Get = Callable[[str], bytes]


class ComEdError(RuntimeError):
    """A request failed, or the response did not have the expected shape."""


@dataclass(frozen=True)
class HourRow:
    hour: int  # hour start, epoch seconds
    day_ahead: float | None
    real_time: float | None  # None until the hour has passed


class Fetcher:
    """HTTP GET that identifies itself, spaces out requests and retries transient errors."""

    def __init__(self, min_interval: float = 1.0, timeout: float = 30, attempts: int = 4,
                 opener: Callable = urlopen, sleep: Callable[[float], None] = _time.sleep):
        self.min_interval = min_interval
        self.timeout = timeout
        self.attempts = attempts
        self.opener = opener
        self.sleep = sleep
        self._last = -math.inf

    def __call__(self, url: str) -> bytes:
        for attempt in range(1, self.attempts + 1):
            wait = self._last + self.min_interval - _time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = _time.monotonic()
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT})
                with self.opener(request, timeout=self.timeout) as response:
                    body = response.read(MAX_RESPONSE + 1)
            except HTTPError as exc:
                if exc.code not in RETRY_STATUS or attempt == self.attempts:
                    raise ComEdError(f"HTTP {exc.code} from {url}") from exc
            except (URLError, TimeoutError, ConnectionError) as exc:
                if attempt == self.attempts:
                    raise ComEdError(f"request to {url} failed: {exc}") from exc
            else:
                if len(body) > MAX_RESPONSE:
                    raise ComEdError(f"response from {url} is larger than {MAX_RESPONSE} bytes")
                return body
            self.sleep(2.0 ** attempt)
        raise AssertionError("unreachable")


def five_minute_prices(first: datetime, last: datetime, get: Get) -> list[tuple[int, float]]:
    """Prices labelled from `first` to `last` inclusive, both naive Chicago wall times."""
    query = urlencode({"type": "5minutefeed", "format": "json",
                       "datestart": first.strftime("%Y%m%d%H%M"),
                       "dateend": last.strftime("%Y%m%d%H%M")})
    return parse_five_minute(get(f"{API_URL}?{query}"))


def latest_five_minute_prices(get: Get) -> list[tuple[int, float]]:
    """Every 5-minute price from the last 24 hours."""
    return parse_five_minute(get(f"{API_URL}?type=5minutefeed&format=json"))


def parse_five_minute(body: bytes) -> list[tuple[int, float]]:
    """(interval start, cents/kWh) pairs in time order."""
    try:
        records = json.loads(body)
    except ValueError as exc:
        raise ComEdError(f"5-minute feed is not JSON: {exc}") from exc
    if not isinstance(records, list):
        raise ComEdError("5-minute feed did not return a list")
    prices: dict[int, float] = {}
    for record in records:
        try:
            millis = int(record["millisUTC"])
            price = float(record["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ComEdError(f"malformed 5-minute record {record!r}") from exc
        if millis % (SLOT * 1000) or not math.isfinite(price):
            raise ComEdError(f"malformed 5-minute record {record!r}")
        start = millis // 1000
        if prices.setdefault(start, price) != price:
            raise ComEdError(f"two different prices for {local(start):%Y-%m-%d %H:%M %Z}")
    return sorted(prices.items())


def hourly_table(day: date, get: Get) -> list[HourRow]:
    """Day-ahead and real-time hourly prices from ComEd's pricing table for one date."""
    query = urlencode({"type": "pricingtabledual", "date": day.strftime("%Y%m%d")})
    return parse_hourly_table(get(f"{TABLE_URL}?{query}"), day)


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


def day_chunks(first: date, last: date, days: int = 7):
    """Inclusive (start, end) date pairs covering first..last."""
    while first <= last:
        end = min(last, first + timedelta(days=days - 1))
        yield first, end
        first = end + timedelta(days=1)

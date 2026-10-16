"""Stand-ins for ComEd's two price feeds and Open-Meteo, serving made-up but self-consistent data."""
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sparc.clock import CT, HOUR, SLOT, at, local


def price(t):
    wall = local(t)
    return round(3 + 2 * math.sin(wall.hour / 24 * 2 * math.pi) + (20 if wall.minute == 10 and wall.hour == 1 else 0), 1)


def day_ahead(hour):
    return round(3 + 2 * math.sin(local(hour).hour / 24 * 2 * math.pi), 1)


def temperature(hour):
    """Made-up Chicago temperature: a daily swing around a seasonal mean (°C)."""
    wall = local(hour)
    season = -12 * math.cos(2 * math.pi * (wall.timetuple().tm_yday - 15) / 365.25)
    return round(10 + season + 5 * math.sin(2 * math.pi * (wall.hour - 9) / 24), 1)


def weather(query):
    """Open-Meteo's Previous Runs API for every hour of the requested UTC dates."""
    first = datetime.combine(date.fromisoformat(query["start_date"]), time(0), tzinfo=timezone.utc)
    last = datetime.combine(date.fromisoformat(query["end_date"]), time(23), tzinfo=timezone.utc)
    hours = list(range(int(first.timestamp()), int(last.timestamp()) + 1, HOUR))
    now = {"temperature_2m": temperature, "dew_point_2m": lambda h: temperature(h) - 5,
           "wind_speed_10m": lambda h: 12.0, "cloud_cover": lambda h: 50.0}
    hourly = {"time": hours}
    for name in query["hourly"].split(","):
        base = name.removesuffix("_previous_day1")
        offset = 0.5 if name.endswith("_previous_day1") and base != "cloud_cover" else 0.0
        hourly[name] = [round(now[base](h) + offset, 1) for h in hours]
    return json.dumps({"latitude": 41.88, "longitude": -87.63, "hourly": hourly}).encode()


class FakeComEd:
    def __init__(self):
        self.requests = []

    def __call__(self, url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.requests.append(query)
        if "open-meteo" in url:
            return weather(query)
        if query["type"] == "5minutefeed":
            first = datetime.strptime(query["datestart"], "%Y%m%d%H%M").replace(tzinfo=CT)
            last = datetime.strptime(query["dateend"], "%Y%m%d%H%M").replace(tzinfo=CT)
            labels = range(int(first.timestamp()), int(last.timestamp()) + 1, SLOT)
            return json.dumps([{"millisUTC": str(t * 1000), "price": str(price(t))}
                               for t in reversed(labels)]).encode()
        day = datetime.strptime(query["date"], "%Y%m%d").date()
        first_hour = at(day, time(0)) - HOUR
        cells = []
        for hour in range(first_hour, at(day + timedelta(days=1), time(0)) - HOUR, HOUR):
            ends = local(hour + HOUR)
            mean = sum(price(t) for t in range(hour, hour + HOUR, SLOT)) / 12
            cells.append(f"{ends:%I}".lstrip("0") + f":00 <small>{'a' if ends.hour < 12 else 'p'}.m.</small>"
                         f"{day_ahead(hour)}¢{round(mean, 1)}¢")
        return "".join(cells).encode("cp1252")

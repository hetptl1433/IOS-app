"""A stand-in for ComEd's two price feeds, serving made-up but self-consistent prices."""
import json
import math
from datetime import datetime, time, timedelta
from urllib.parse import parse_qs, urlparse

from sparc.clock import CT, HOUR, SLOT, at, local


def price(t):
    wall = local(t)
    return round(3 + 2 * math.sin(wall.hour / 24 * 2 * math.pi) + (20 if wall.minute == 10 and wall.hour == 1 else 0), 1)


def day_ahead(hour):
    return round(3 + 2 * math.sin(local(hour).hour / 24 * 2 * math.pi), 1)


class FakeComEd:
    def __init__(self):
        self.requests = []

    def __call__(self, url):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.requests.append(query)
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

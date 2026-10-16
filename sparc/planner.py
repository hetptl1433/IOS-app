"""Choose when to charge: the cheapest hours that still finish on time.

ComEd bills every kWh in an hour at that hour's average price, so the cost of a
plan is linear in the energy put into each hour, and filling the cheapest hours
first is optimal for a given set of hourly prices. Two preferences rank ahead of
price: stay out of blocked hours (capacity-charge risk), then try to finish
`reserve_s` before departure. Both give way when the energy would not fit otherwise.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from math import ceil

from .clock import HOUR, SLOT, hour_start

EPS = 1e-9


@dataclass(frozen=True)
class Need:
    now: int  # start of the current 5-minute slot
    departure: int
    energy_kwh: float  # grid energy still to deliver
    power_kw: float
    reserve_s: int = 0


def plan(need: Need, cost: Mapping[int, float], blocked: Collection[int] = (), *,
         charging: bool = False, stickiness: float = 0.0) -> dict[int, float]:
    """kWh to deliver in each 5-minute slot between `need.now` and departure.

    `cost` gives the expected cents/kWh of every hour in the window. `stickiness`
    (cents/kWh) favours the charger's current state in the current hour, so a small
    change in an estimate does not switch it on and off.
    """
    if need.energy_kwh <= EPS or need.now >= need.departure:
        return {}
    full = need.power_kw * SLOT / HOUR
    soft_deadline = need.departure - need.reserve_s
    current = hour_start(need.now)

    blocks = []  # the slots of each hour before the soft deadline, and those after it
    for hour in range(current, need.departure, HOUR):
        price = cost[hour]
        if hour == current:
            price += -stickiness if charging else stickiness
        first, end = max(hour, need.now), min(hour + HOUR, need.departure)
        split = first if soft_deadline <= first else min(end, first + ceil((soft_deadline - first) / SLOT) * SLOT)
        for late, part in ((False, range(first, split, SLOT)), (True, range(split, end, SLOT))):
            if part:
                blocks.append(((hour in blocked, late, price, hour), part))
    blocks.sort(key=lambda block: block[0])

    chosen = []
    left = need.energy_kwh
    for _, part in blocks:
        energy = min(left, len(part) * full)
        chosen.append((part, energy))
        left -= energy
        if left <= EPS:
            break

    schedule = {}
    *whole, (last, energy) = chosen
    for part, _ in whole:
        schedule.update(dict.fromkeys(part, full))
    count = max(1, min(len(last), ceil(energy / full - EPS)))
    if last[0] - SLOT in schedule or (last[0] == need.now and charging):
        slots = last[:count]  # continue the block that is already running
        partial = slots[-1]
    else:
        slots = last[-count:]  # lead into the next block, seeing more prices first
        partial = slots[0]
    schedule.update(dict.fromkeys(slots, full))
    schedule[partial] = energy - (count - 1) * full
    return schedule

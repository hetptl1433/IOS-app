"""Dated supply/distribution comparison; never a claim about a complete utility bill."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from zoneinfo import ZoneInfo

CHICAGO=ZoneInfo("America/Chicago")
DEFAULT_CLASS="singleFamilyElectric"
SOURCE="https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf?branch=prod_alias"
BASE={"singleFamilyElectric":([1.999,5.329,1.890,1.550],2.712,.069810,.061625),
      "singleFamilyNonElectric":([4.009,10.712,3.747,2.984],5.698,.007668,.003745),
      "multiFamilyElectric":([1.925,4.975,1.823,1.512],2.576,.064486,.052267),
      "multiFamilyNonElectric":([3.073,8.689,2.856,2.251],4.354,.011682,.007750)}


def cents(at,delivery_class=DEFAULT_CLASS,standard=False):
    """Info sheets 64.1/67,9,18,20,56,62,65. October–December 2026 only."""
    if delivery_class not in BASE: raise ValueError("Choose a residential delivery class.")
    if isinstance(at,str):at=datetime.fromisoformat(at.replace("Z","+00:00"))
    if at.tzinfo is None:raise ValueError("Use an absolute tariff timestamp.")
    local=at.astimezone(CHICAGO)
    if not "2026-10-01"<=local.date().isoformat()<="2026-12-31":return None
    rates,flat,old_rba,new_rba=BASE[delivery_class]
    band=3 if local.hour<6 or local.hour>=21 else 0 if local.hour<13 else 1 if local.hour<19 else 2
    base=Decimal(str(flat if standard else rates[band]))
    factors=[".0217",".006785" if local.month==10 else ".001391",".065510" if local.month==10 else ".029432",str(old_rba if local.month==10 else new_rba)]
    return float(base+sum((base*Decimal(v)).quantize(Decimal(".001"),rounding=ROUND_HALF_UP) for v in factors)+Decimal(".062"))


def fixed_supply(at):
    if isinstance(at,str):at=datetime.fromisoformat(at.replace("Z","+00:00"))
    return 8.413 if "2026-10-01"<=at.astimezone(CHICAGO).date().isoformat()<="2027-05-31" else None


def energy_cost(buckets,rate):
    total=0.0
    for stamp,kwh in buckets.items():
        value=rate(stamp)
        if value is None:return None
        total+=kwh*value/100
    return total


def compare(energy,actual_supply,immediate_supply,actual_buckets,immediate_buckets,delivery_class=DEFAULT_CLASS):
    """Four equal-energy scenarios; three gains telescope without double counting."""
    def covers(b):return abs(sum(b.values())-energy)<1e-6
    def add(a,b):return a+b if a is not None and b is not None else None
    fixed=energy_cost(immediate_buckets,fixed_supply) if covers(immediate_buckets) else None
    flat=energy_cost(immediate_buckets,lambda t:cents(t,delivery_class,True)) if covers(immediate_buckets) else None
    tod_now=energy_cost(immediate_buckets,lambda t:cents(t,delivery_class)) if covers(immediate_buckets) else None
    tod_sparc=energy_cost(actual_buckets,lambda t:cents(t,delivery_class)) if covers(actual_buckets) else None
    baseline=add(fixed,flat);hourly=add(immediate_supply,flat);tod=add(immediate_supply,tod_now);system=add(actual_supply,tod_sparc)
    known=all(v is not None and math.isfinite(v) for v in (baseline,hourly,tod,system))
    gain=baseline-system if known else None
    return {"energyKWh":energy,"baselineCostUSD":baseline,"hourlyStandardCostUSD":hourly,
            "hourlyTODCostUSD":tod,"systemCostUSD":system,"savingsUSD":gain,
            "savingsPercent":100*gain/abs(baseline) if known and abs(baseline)>1e-9 else None,
            "hourlySavingsUSD":baseline-hourly if known else None,
            "deliverySavingsUSD":hourly-tod if known else None,
            "schedulingSavingsUSD":tod-system if known else None,"priced":known}


def summary(rows):
    known=[r for r in rows if r["priced"]]
    keys=["energyKWh","baselineCostUSD","hourlyStandardCostUSD","hourlyTODCostUSD","systemCostUSD","savingsUSD","hourlySavingsUSD","deliverySavingsUSD","schedulingSavingsUSD"]
    result={k:sum(r[k] for r in known) if known else None for k in keys}
    base=result["baselineCostUSD"];gain=result["savingsUSD"]
    result.update(nightCount=len(rows),pricedNightCount=len(known),unpricedNightCount=len(rows)-len(known),
                  savingsPercent=100*gain/abs(base) if base is not None and abs(base)>1e-9 else None)
    return result


def delivery_hours(first,last,delivery_class=DEFAULT_CLASS):
    cursor=int(first.timestamp())//3600*3600;end=last.timestamp();result={}
    while cursor<end:
        value=cents(datetime.fromtimestamp(cursor,timezone.utc),delivery_class)
        if value is None:return None
        result[cursor]=value;cursor+=3600
    return result

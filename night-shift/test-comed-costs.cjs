"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {spawnSync} = require("node:child_process");
const Engine = require("./dist/comed-cost-engine.js");
const close = (a, b, epsilon = 1e-7) => assert.ok(Math.abs(a - b) < epsilon, `${a} != ${b}`);
const formatter = new Intl.DateTimeFormat("en-US", {timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23"});
function local(epoch) {
  const p = Object.fromEntries(formatter.formatToParts(new Date(epoch * 1000)).map(r => [r.type, r.value]));
  return [`${p.year}-${p.month}-${p.day}`, Number(p.hour)];
}
function fixture(from = "2024-01-29", to = "2024-02-04", rows = [["2024-01-31", 25]]) {
  const data = {source: {trace: "test"}, limitations: [], rows, rates: {}, hours: []};
  const rate = {fixed_price: 10, pea: 1, transmission: 2, distribution_facilities: 3,
    il_electricity_distribution: .1, renewable_portfolio_standard: .5, carbon_free_resource_adj: -1,
    zero_emission_standard: 0, energy_efficiency: 0, environmental_cost_recovery: 0,
    energy_transition_assistance: 0, coal_to_solar: 0, capacity_rate_usd_kw_month: 4,
    customer_charge_usd_month: 10, metering_charge_usd_month: 3, low_income_discount_recovery_usd_month: 0, notes: "fixture"};
  const start = Date.parse(from + "T00:00:00Z") / 1000, end = Date.parse(to + "T23:00:00Z") / 1000;
  for (let t = start; t <= end; t += 3600) {
    const [date, h] = local(t);
    data.rates[date.slice(0, 7)] = {...rate};
    const forecast = h < 6 ? 1 : 10;
    data.hours.push([t, date, h, forecast, h < 6 ? 2 : 8, 12, 0]);
  }
  for (const h of data.hours) {
    const prior = new Date(h[1] + "T12:00:00Z"); prior.setUTCDate(prior.getUTCDate() - 1);
    const priorDate = prior.toISOString().slice(0, 10);
    const release = data.hours.find(r => r[1] === priorDate && r[2] === 17);
    h[6] = release ? release[0] : h[0] - 86400;
  }
  return data;
}

test("CSV builder assigns midnight end stamps to the prior hour and rejects incomplete/duplicate readings", () => {
  const result = spawnSync("python3", ["-c", `
import csv, importlib.util, tempfile
from pathlib import Path
from datetime import datetime, timezone
spec=importlib.util.spec_from_file_location('cost_builder',__import__('sys').argv[1])
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory() as folder:
 p=Path(folder)
 (p/mod.FILES[0]).write_text('date,home_kwh\\n2024-05-31,1\\n')
 (p/mod.FILES[3]).write_text('component,effective_month,value,unit,status,source\\n')
 with (p/mod.FILES[2]).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['month',*mod.RATE_FIELDS,'notes']);w.writeheader()
  w.writerow(dict(month='2024-05',notes='',**{key:0 for key in mod.RATE_FIELDS}))
 midnight=int(datetime(2024,6,1,5,tzinfo=timezone.utc).timestamp()*1000)
 with (p/mod.FILES[1]).open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['millisUTC','hourly_price','hourly_price_forecast'])
  for i in range(12):w.writerow([midnight-3300000+i*300000,i,1.2])
  for i in range(11):w.writerow([midnight+300000+i*300000,i,1.2])
  for i in range(12):w.writerow([midnight+3600000+300000+i*300000,i,1.2 if i<11 else 3])
  for i in range(12):w.writerow([midnight+7200000+300000+(0 if i==11 else i)*300000,i,1.2])
 data=mod.build(p);a,b,c,d=data['hours']
 assert a[1]=='2024-05-31' and a[2]==23 and a[4]==5.5 and a[5]==12,a
 assert b[4] is None and b[5]==11,b
 assert c[3] is None,c
 assert d[4] is None,d
` ,path.join(__dirname,"build-comed-cost-data.py")], {encoding:"utf8"});
  assert.equal(result.status,0,result.stderr || result.stdout);
});

test("monthly excise tiers use 2,000 kWh, preserve all higher tiers, and return incremental tax", () => {
  close(Engine.stateTax(500), 1.65);
  close(Engine.stateTax(2000), 6.6);
  close(Engine.stateTax(2100) - Engine.stateTax(1900), .330 + .319);
  close(Engine.stateTax(50001) - Engine.stateTax(50000), .00303);
  close(Engine.stateTax(20000001) - Engine.stateTax(20000000), .00202);
  assert.throws(() => Engine.stateTax(-1), RangeError);
});

test("Glenview sourced municipal tiers apply once, with collection already included", () => {
  close(Engine.glenviewTax(1000), 5.56);
  close(Engine.glenviewTax(2000), 11.12);
  close(Engine.glenviewTax(2100) - Engine.glenviewTax(1900), .556 + .366);
  close(Engine.glenviewTax(50001) - Engine.glenviewTax(50000), .00346);
  const rows = Array.from({length:31}, (_,i) => [`2024-01-${String(i+1).padStart(2,"0")}`,i===5 ? 20 : 0]);
  const result = Engine.compare(fixture("2024-01-01","2024-02-04",rows), {householdKwh:1990});
  result.strategies.forEach(s => {
    close(s.municipalTax,10*.00556+10*.00366);
    close(s.stateTax,10*.00330+10*.00319);
    assert.equal(s.municipalTaxRuleId,"glenview-electricity-use-tax");
    assert.equal(s.municipalRateCentsPerKwh,null);
    assert.equal(s.municipalRateStatus,"sourced");
  });
});

test("explicit plan dispatch shares verified statutory rules and preserves separate municipal assumptions", () => {
  const data = fixture("2024-01-29","2024-02-04",[["2024-01-30",25]]);
  const report = Engine.compare(data,{fixedMunicipalCents:.9,hourlyMunicipalCents:.4});
  assert.deepEqual(report.strategies.map(s => s.supplyPlan),["fixed","hourly","hourly"]);
  close(report.strategies[0].municipalTax,25*.009);
  close(report.strategies[1].municipalTax,25*.004);
  close(report.strategies[2].municipalTax,25*.004);
  for (const plan of ["fixed","hourly"]) {
    const treatment=report.taxTreatment[plan];
    assert.equal(treatment.state.id,"illinois-residential-excise");
    assert.equal(treatment.state.basis,"monthly household kWh");
    assert.equal(treatment.state.tiers[0].kwh,2000);
    assert.equal(treatment.state.status,"verified");
    assert.equal(treatment.state.sameRule,true);
    assert.match(treatment.state.url,/ilga\.gov/);
    assert.equal(treatment.comparisonSource,"https://hourlypricing.comed.com/faqs/");
    assert.equal(treatment.municipal.status,"user-assumption");
  }
});

test("cheaper or negative supply and rider credits do not reduce per-kWh excise tax", () => {
  const data = fixture("2024-01-29","2024-02-04",[["2024-01-30",25]]), cheap = structuredClone(data);
  for(const h of cheap.hours) h[4]=-100;
  for(const r of Object.values(cheap.rates)) {r.fixed_price=-100;r.pea=-10;r.carbon_free_resource_adj=-500;}
  const normal=Engine.compare(data), discounted=Engine.compare(cheap);
  for(let i=0;i<3;i++) {
    assert.ok(discounted.strategies[i].supply<0);
    assert.ok(discounted.strategies[i].riders<0);
    close(discounted.strategies[i].stateTax,normal.strategies[i].stateTax);
    close(discounted.strategies[i].municipalTax,normal.strategies[i].municipalTax);
    assert.ok(discounted.strategies[i].stateTax>0 && discounted.strategies[i].municipalTax>0);
  }
});

test("blank/null municipal inputs use sourced Glenview; explicit zero and legacy fallback remain deliberate overrides", () => {
  const data=fixture("2024-01-29","2024-02-04",[["2024-01-30",25]]);
  const sourced=Engine.compare(data,{fixedMunicipalCents:null,hourlyMunicipalCents:""});
  sourced.strategies.forEach(s => {close(s.municipalTax,25*.00556);assert.equal(s.municipalRateStatus,"sourced");});
  const legacy=Engine.compare(data,{municipalCents:.61,fixedMunicipalCents:0});
  close(legacy.strategies[0].municipalTax,0);
  close(legacy.strategies[1].municipalTax,25*.0061);
  close(legacy.strategies[2].municipalTax,25*.0061);
  assert.equal(legacy.taxTreatment.fixed.municipal.status,"user-assumption");
  const nullWins=Engine.compare(data,{municipalCents:.61,fixedMunicipalCents:null});
  close(nullWins.strategies[0].municipalTax,25*.00556);
  assert.throws(()=>Engine.compare(data,{hourlyMunicipalCents:-1}),RangeError);
});

test("municipal source history is flagged outside verified filings and exported per method/month", () => {
  const data=fixture("2023-12-29","2024-01-04",[["2023-12-31",25]]);
  const result=Engine.compare(data);
  assert.equal(result.taxTreatment.fixed.municipal.status,"historical-assumption");
  assert.deepEqual(result.taxTreatment.fixed.municipal.historicalAssumptionMonths,["2023-12"]);
  assert.equal(result.months[0].strategies[0].municipalRateStatus,"historical-assumption");
  assert.equal(result.months[1].strategies[0].municipalRateStatus,"sourced");
  assert.equal(result.taxTreatment.hourly.municipal.verifiedThrough,"2026-11-05");
  const csv=Engine.csv(result);
  for(const name of ["supply_plan","state_tax_rule_id","state_tax_basis","municipal_rate_cents_per_kwh","municipal_rate_status","municipal_tax_source_url"]) assert.ok(csv.includes(name));
  assert.ok(csv.includes("historical-assumption"));
  assert.ok(csv.includes("glenview-electricity-use-tax"));
});

test("three methods deliver identical energy without double-counting transmission or normalized capacity", () => {
  const data = fixture("2024-01-29", "2024-02-04", [["2024-01-30", 25]]);
  const result = Engine.compare(data, {powerKw: 10, householdKwh: 0});
  assert.equal(result.comparedDays, 1);
  for (const s of result.strategies) close(s.energyKwh, 25);
  const fixed = result.strategies[0];
  close(fixed.supply, 25 * .11);
  close(fixed.transmission, 25 * .02);
  close(fixed.delivery, 25 * .031);
  close(fixed.distributionFacilities, 25 * .03);
  close(fixed.illinoisDistribution, 25 * .001);
  close(fixed.distributionFacilities + fixed.illinoisDistribution, fixed.delivery);
  close(fixed.riders, -.125);
  close(fixed.stateTax, .0825);
  close(fixed.capacity, 0);
  close(fixed.serviceFees, 13 * 2 / 31);
  const immediate = result.strategies[1];
  close(immediate.capacity, 10 * 2 / 31);
  close(immediate.accountCapacity, immediate.capacity);
  close(fixed.total, fixed.variableTotal + fixed.serviceFees);
  assert.ok(result.timingSavings > 0);
});

test("source zero-charge dates count toward fee coverage; account costs are once per covered month", () => {
  const data = fixture("2024-01-01", "2024-02-04", Array.from({length:31}, (_,i) => [`2024-01-${String(i + 1).padStart(2, "0")}`, i === 5 ? 20 : 0]));
  const result = Engine.compare(data);
  assert.equal(result.selectedDays, 31);
  const month = result.months[0], hourly = month.strategies[1];
  assert.equal(month.coverageDays, 31);
  close(hourly.accountServiceFees, 13);
  close(hourly.accountCapacity, 10);
  close(hourly.evShare, 20 / 620);
  close(hourly.serviceFees, 13 * 20 / 620);
  close(hourly.capacity, 10 * 20 / 620);
});

test("one missing settled reading excludes the entire common day even if immediate could avoid it", () => {
  const data = fixture();
  const hour = data.hours.find(r => r[1] === "2024-02-01" && r[2] === 6);
  hour[4] = null; hour[5] = 11;
  const result = Engine.compare(data);
  assert.equal(result.comparedDays, 0); assert.equal(result.excludedDays, 1);
  assert.match(result.exclusions[0].reason, /12 five-minute/);
  result.strategies.forEach(s => close(s.energyKwh, 0));
});

test("a missing forecast excludes rather than billing guessed energy as realized savings", () => {
  const data = fixture(); data.hours.find(r => r[1] === "2024-02-01" && r[2] === 6)[3] = null;
  const result = Engine.compare(data);
  assert.equal(result.excludedDays, 1); assert.match(result.exclusions[0].reason, /forecast/);
});

test("future actual prices do not alter SPARC's charging schedule", () => {
  const data = fixture(), altered = structuredClone(data);
  altered.hours.forEach(h => {h[4] = h[2] < 6 ? 900 : -500;});
  const a = Engine.planDay(data, data.rows[0], {powerKw:10});
  const b = Engine.planDay(altered, altered.rows[0], {powerKw:10});
  assert.ok(a.valid && b.valid);
  assert.deepEqual(a.tracks[2].hours.map(h => [h.time,h.energyKwh]), b.tracks[2].hours.map(h => [h.time,h.energyKwh]));
});

test("unpublished tomorrow forecasts cannot affect charging before publication", () => {
  const data = fixture(), altered = structuredClone(data);
  altered.hours.filter(h => h[1] === "2024-02-01").forEach(h => {h[3] = h[2] < 6 ? -1000 : 1000;});
  const options = {powerKw:10, arrivalHour:12, departureHour:8};
  const a = Engine.planDay(data, data.rows[0], options), b = Engine.planDay(altered, altered.rows[0], options);
  assert.ok(a.valid && b.valid);
  const before = track => track.hours.filter(h => h.date === "2024-01-31" && h.localHour < 17).map(h => [h.time,h.energyKwh]);
  assert.deepEqual(before(a.tracks[2]), before(b.tracks[2]));
});

test("billing assigns energy and costs to the actual calendar month, with one common covered-day span", () => {
  const data = fixture(), result = Engine.compare(data, {powerKw:10});
  const january = result.months.find(m => m.month === "2024-01"), february = result.months.find(m => m.month === "2024-02");
  close(january.strategies[0].energyKwh, 25); close(february.strategies[0].energyKwh, 0);
  close(january.strategies[2].energyKwh, 0); close(february.strategies[2].energyKwh, 25);
  close(january.strategies[0].accountServiceFees, january.strategies[2].accountServiceFees);
  close(february.strategies[0].accountServiceFees, february.strategies[2].accountServiceFees);
  close(result.timingSavings, result.strategies[1].variableTotal - result.strategies[2].variableTotal);
  close(result.allocatedTimingDifference, result.strategies[1].total - result.strategies[2].total);
});

test("Chicago DST overnight windows have 11 or 13 elapsed hours without fabricated capacity", () => {
  const spring = fixture("2024-03-08", "2024-03-12", [["2024-03-09", 110]]);
  const fall = fixture("2024-11-01", "2024-11-05", [["2024-11-02", 130]]);
  const a = Engine.planDay(spring, spring.rows[0], {powerKw:10});
  const b = Engine.planDay(fall, fall.rows[0], {powerKw:10});
  assert.ok(a.valid && b.valid); close((a.end-a.start)/3600, 11); close((b.end-b.start)/3600, 13);
  assert.equal(Engine.compare(spring, {powerKw:9}).excludedDays, 1);
});

test("fractional final charging hour delivers precise energy; configurable municipal tax adds once", () => {
  const data = fixture("2024-01-29", "2024-02-04", [["2024-01-30", 25.25]]);
  const plan = Engine.planDay(data, data.rows[0], {powerKw:10});
  plan.tracks.forEach(t => close(t.hours.reduce((n,h) => n+h.energyKwh, 0), 25.25));
  const result = Engine.compare(data, {powerKw:10,municipalCents:.61});
  result.strategies.forEach(t => close(t.municipalTax, 25.25*.0061));
});

test("real default result reconciles all components, cohort counts, source energy and CSV totals", () => {
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,"dist/comed-cost-data.js"),"utf8"),context);
  const data = context.window.SPARC_COMED_COST_DATA, result = Engine.compare(data);
  assert.equal(data.rows.length,1614); assert.equal(data.source.chargingDays,1456); close(data.source.homeKwh,32667.1);
  assert.equal(result.comparedDays + result.excludedDays,result.chargingDays);
  assert.equal(data.source.taxSources.state.tiers[0].kwh,2000);
  assert.equal(data.source.taxSources.municipal.collectionAdjustmentIncluded,true);
  assert.equal(data.source.taxSources.municipal.tiers[0].centsPerKwh,.556);
  close(result.energyKwh + result.excludedKwh,data.source.homeKwh);
  for(const s of result.strategies) {
    close(s.energyKwh,result.energyKwh);
    close(s.total,Engine.fields.reduce((n,key) => n+s[key],0));
    close(s.total,result.months.reduce((n,m) => n+m.strategies.find(x => x.id===s.id).total,0));
  }
  const exported=Engine.csv(result);
  assert.ok(exported.includes("account_capacity_usd")); assert.ok(exported.includes("rate_notes"));
  assert.equal(exported.trim().split("\r\n").length,1+result.months.length*3);
});

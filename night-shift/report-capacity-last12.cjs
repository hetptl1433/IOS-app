"use strict";

// Compare historical capacity costs from the saved monthly SPARC charging replay.

const fs = require("node:fs");

const path = require("node:path");

const TARIFFS = [
  {
    "from": "2025-09",
    "to": "2025-12",
    "rate": 8.31935,
    "source": "https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf"
  },
  {
    "from": "2026-01",
    "to": "2026-05",
    "rate": 8.32925,
    "source": "https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf"
  },
  {
    "from": "2026-06",
    "to": "2027-05",
    "rate": 10.11236,
    "source": "https://www.icc.illinois.gov/downloads/public/filing/4/399143.pdf"
  }
];

function tariffFor(month) {
  const tariff=TARIFFS.find(t=>t.from<=month&&month<=t.to);
  if(!tariff)throw new Error("No verified capacity tariff for "+month);
  return tariff;
}

function calculate(report, from="2025-11", to="2026-10") {
  if(!Number.isFinite(report.options.capacityKw)||report.options.capacityKw<0)throw new Error("Invalid capacity obligation");
  const months=report.months.filter(m=>m.month>=from&&m.month<=to);
  if(months.length!==12)throw new Error("Expected 12 months; found "+months.length);
  const rows=months.map(m=>{
    const f=m.strategies.find(s=>s.id==="fixed-immediate");
    const h=m.strategies.find(s=>s.id==="hourly-immediate");
    const s=m.strategies.find(s=>s.id==="hourly-sparc");
    if(!f||!h||!s||![f,h,s].every(x=>Number.isFinite(x.total)&&Number.isFinite(x.capacity)&&Number.isFinite(x.evShare)))throw new Error("Incomplete month "+m.month);
    const filed=tariffFor(m.month);
    const accountCapacity=filed.rate*report.options.capacityKw*m.coverageFraction;
    const hourlyCapacity=accountCapacity*h.evShare;
    const evCapacity=accountCapacity*s.evShare;
    const immediateNo=h.total-h.capacity;
    const sparcNo=s.total-s.capacity;
    return {month:m.month,rate:filed.rate,source:filed.source,
      fixedKwh:f.energyKwh,hourlyKwh:h.energyKwh,sparcKwh:s.energyKwh,
      fixed:f.total,immediateNo,immediateWith:immediateNo+hourlyCapacity,
      sparcNo,sparcWith:sparcNo+evCapacity,accountCapacity,evCapacity,
      fixedSavingsNo:f.total-sparcNo,fixedSavingsWith:f.total-sparcNo-evCapacity,
      hourlySavingsWith:immediateNo+hourlyCapacity-sparcNo-evCapacity};
  });
  const keys=["fixedKwh","hourlyKwh","sparcKwh","fixed","immediateNo","immediateWith","sparcNo","sparcWith","accountCapacity","evCapacity","fixedSavingsNo","fixedSavingsWith","hourlySavingsWith"];
  const totals={};
  for(const key of keys)totals[key]=rows.reduce((sum,r)=>sum+r[key],0);
  if(Math.abs(totals.sparcWith-totals.sparcNo-totals.evCapacity)>1e-7)throw new Error("Capacity reconciliation failed");
  if(Math.abs(totals.fixedSavingsWith-(totals.fixed-totals.sparcWith))>1e-7)throw new Error("Savings reconciliation failed");
  return {from,to,capacityKw:report.options.capacityKw,rows,totals};
}

function markdown(comparison) {
  const {from,to,capacityKw,rows,totals:t}=comparison;
  const money=n=>"$"+n.toFixed(2);
  const pct=(v,den)=>(100*v/den).toFixed(2)+"%";
  return [
"# SPARC: Historical ComEd capacity comparison (12 months)",
"",
"**Period:** "+from+" to "+to+". **Method:** recomputation of capacity components in the checked-in monthly replay.",
"**Capacity assumption:** "+capacityKw+" kW billable capacity obligation (not measured PLC).",
"",
"| Scenario | 12-month modeled EV-allocated cost | Savings vs fixed |",
"|---|---:|---:|",
"| Fixed immediate (capacity embedded in supply) | "+money(t.fixed)+" | Baseline |",
"| Hourly + SPARC, excluding capacity | "+money(t.sparcNo)+" | "+money(t.fixedSavingsNo)+" ("+pct(t.fixedSavingsNo,t.fixed)+") |",
"| Hourly + SPARC, with filed capacity | "+money(t.sparcWith)+" | "+money(t.fixedSavingsWith)+" ("+pct(t.fixedSavingsWith,t.fixed)+") |",
"| Hourly immediate, with filed capacity | "+money(t.immediateWith)+" | "+money(t.fixed-t.immediateWith)+" |",
"",
"**Capacity impact:** +"+money(t.evCapacity)+" EV-allocated cost versus excluding it. The estimated **whole-household** capacity charge is "+money(t.accountCapacity)+" for 12 months.",
"**SPARC optimization vs immediate hourly (both with capacity):** "+money(t.hourlySavingsWith)+" ("+pct(t.hourlySavingsWith,t.immediateWith)+").",
"",
"## Monthly scenario costs",
"",
"| Month | Filed capacity $/kW-month | Fixed with capacity embedded | SPARC hourly without capacity | SPARC hourly with capacity | EV-allocated capacity |",
"|---|---:|---:|---:|---:|---:|",
...rows.map(r=>"| "+r.month+" | "+r.rate.toFixed(5)+" | "+money(r.fixed)+" | "+money(r.sparcNo)+" | "+money(r.sparcWith)+" | "+money(r.evCapacity)+" |"),
"| **Total** | | **"+money(t.fixed)+"** | **"+money(t.sparcNo)+"** | **"+money(t.sparcWith)+"** | **"+money(t.evCapacity)+"** |",
"",
"## Important limitations",
"",
"- **Not a fresh charging-session simulation:** Reuses the saved replay's monthly scenario costs, then removes the original hourly capacity amount and substitutes the historically filed monthly rate. Capacity does not change the existing scheduling decisions under the fixed-obligation assumption.",
"- **Not a whole-household bill:** the reported EV capacity is allocated using the simulator's EV-kWh share, while the actual capacity line is billed to the entire account.",
"- **Unequal calendar-year kWh from overnight spillovers:** fixed/immediate has "+t.fixedKwh.toFixed(1)+" kWh and SPARC has "+t.sparcKwh.toFixed(1)+" kWh within these 12 calendar months. Underlying charging needs are shared, but sessions crossing a month/year boundary shift the billed energy. Do not claim this as a perfectly energy-matched annual billing comparison.",
"- Fixed-plan electricity supply already includes capacity; **do not add hourly capacity to fixed**. For hourly plans, monthly account capacity = filed dollars/kW-month × assumed billable obligation × billing-period coverage, then allocated by EV share solely for an EV-cost comparison.",
"- Does not demonstrate that SPARC reduces next year's capacity obligation or actual coincident peak demand. The assumed obligation is unchanged between hourly scenarios.",
"- Historical rate inputs other than capacity contain approximations. The original replay omits hourly-specific HPEA and procurement costs and uses fixed-plan transmission as an hourly proxy. Some charging days have missing prices and are excluded.",
"- Monthly calendar windows approximate ComEd billing cycles. This report is an estimate, not a verified ComEd bill.",
"",
"## Official ComEd capacity tariff sources",
"",
"- November–December 2025: $8.31935 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf).",
"- January–May 2026: $8.32925 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/394960.pdf).",
"- June–October 2026: $10.11236 per kW-month, [ICC Informational Sheet No. 4](https://www.icc.illinois.gov/downloads/public/filing/4/399143.pdf).",
"",
"To reproduce: `node night-shift/run-comed-costs.cjs && node night-shift/report-capacity-last12.cjs`.",
""
].join("\n");
}

if (require.main === module) {

  const source = path.resolve(__dirname,"../results/charging-history/comed-costs.json");

  const dest = path.resolve(__dirname,"../results/charging-history/comed-capacity-last12.md");

  const output = markdown(calculate(JSON.parse(fs.readFileSync(source,"utf8"))));

  fs.writeFileSync(dest,output);

  console.log(output);

}

module.exports = {TARIFFS,tariffFor,calculate,markdown};


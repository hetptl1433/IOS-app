/* Presentation of the verified full Norway study; independent of replay filters. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcNorwayComparison = api;
  if (typeof window !== "undefined" && root.document) api.mount(root.document, root.SPARC_NORWAY_COMPARISON);
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const money = value => new Intl.NumberFormat("en-US", {style:"currency",currency:"USD"}).format(value);
  const number = value => new Intl.NumberFormat("en-US", {maximumFractionDigits:0}).format(value);
  const percent = value => `${value.toFixed(2)}%`;
  const definitions = {
    "charge-now": {label:"Immediate charging",description:"Starts as soon as the vehicle is connected."},
    "cap-2": {label:"2¢/kWh supply-price cap",description:"Charges at or below this posted supply price, starting earlier if needed to finish."},
    "cap-3": {label:"3¢/kWh supply-price cap",description:"Charges at or below this posted supply price, starting earlier if needed to finish."},
    "cap-5": {label:"5¢/kWh supply-price cap",description:"Charges at or below this posted supply price, starting earlier if needed to finish."},
    "day-ahead": {label:"SPARC day-ahead",description:"Plans charging from published day-ahead prices."},
    live: {label:"SPARC + live prices",description:"Updates the plan as five-minute prices become available."},
    hindsight: {label:"Perfect hindsight",description:"Knows the final prices in advance. Reference only."},
  };
  function timerLabel(id) {
    const hour = Number(id.slice(6,8));
    return `${hour%12 || 12} ${hour<12 ? "AM" : "PM"} timer`;
  }
  function view(report) {
    if (!report || report.dataSource !== "Norway Dataset1" || report.priceYear !== 2025) throw new Error("The verified Norway Dataset 1 results could not load.");
    const tariff="time-of-day";
    const selected = report.results.filter(row=>row.tariff===tariff);
    if (selected.length !== 31 || new Set(selected.map(row=>row.strategy)).size !== 31) throw new Error("The Norway comparison is incomplete.");
    if (!Number.isFinite(report.baselineCostUsd) || report.baselineCostUsd<=0 || selected.some(row=>
      row.commonSessions!==report.commonSessions || !Number.isFinite(row.totalCostUsd) || !Number.isFinite(row.savingPct) ||
      Math.abs(row.deliveredKwh-report.commonEnergyKwh)>report.commonSessions*1e-6 || Math.abs(row.baselineCostUsd-report.baselineCostUsd)>1e-6 ||
      Math.abs(row.savingUsd-(report.baselineCostUsd-row.totalCostUsd))>1e-6 ||
      Math.abs(row.savingPct-row.savingUsd/report.baselineCostUsd*100)>1e-8)) throw new Error("The Norway results need matching completed sessions and energy.");
    const byId = new Map(selected.map(row=>[row.strategy,row]));
    const timers = selected.filter(row=>/^timer-\d{4}$/.test(row.strategy)).sort((a,b)=>a.strategy.localeCompare(b.strategy));
    if (timers.length !== 24) throw new Error("The timer comparison is incomplete.");
    const bestTimer = timers.reduce((best,row)=>row.totalCostUsd<best.totalCostUsd ? row : best);
    const winner = selected.filter(row=>row.strategy!=="hindsight").reduce((best,row)=>row.totalCostUsd<best.totalCostUsd ? row : best);
    const ids = ["charge-now",bestTimer.strategy,"cap-2","cap-3","cap-5","day-ahead","live","hindsight"];
    return {tariff,byId,timers,bestTimer,winner,main:ids.map(id=>byId.get(id)),
      explanation:"All setups use time-of-day delivery. Savings include charging-time and delivery-rate changes, compared with immediate charging on standard delivery."};
  }
  function csv(report) {
    const fields=["strategy","label","tariff","commonSessions","totalCostUsd","savingUsd","savingPct","deliveredKwh","completionPct","startsPerSession","pausesPerSession","riskKwh"];
    const quote=value=>`"${String(value).replace(/"/g,'""')}"`;
    return [fields,...Array.from(view(report).byId.values(),row=>fields.map(key=>row[key]))].map(row=>row.map(quote).join(",")).join("\r\n");
  }
  function mount(doc, report) {
    const $ = id=>doc.getElementById(id);
    const put = (id,value)=>{$(id).textContent=value;};
    const el=(tag,cls,value)=>{const node=doc.createElement(tag);if(cls)node.className=cls;if(value!==undefined)node.textContent=value;return node;};
    function render() {
      const state=view(report);
      put("norway-rate-explanation",state.explanation);
      put("norway-session-count",number(report.commonSessions));
      put("norway-baseline-cost",money(report.baselineCostUsd));
      put("norway-winner",definitions[state.winner.strategy]?.label || timerLabel(state.winner.strategy));
      put("norway-best-percent",percent(state.winner.savingPct));
      put("norway-best-dollars",money(state.winner.savingUsd));
      put("norway-best-cost",`Charging cost: ${money(state.winner.totalCostUsd)}`);
      put("norway-best-note","lower cost through timing + the delivery rate");
      put("norway-timer-name",timerLabel(state.bestTimer.strategy));
      put("norway-timer-percent",percent(state.bestTimer.savingPct));
      put("norway-timer-note",`Total saved: ${money(state.bestTimer.savingUsd)}`);
      put("norway-study-summary",`All 31 setups use time-of-day delivery and completed the same ${number(report.commonSessions)} sessions, delivering ${number(report.commonEnergyKwh)} kWh. Savings compare with immediate charging on standard delivery.`);
      const insufficient=report.exclusions["Reported energy exceeds the available estimated-power capacity."] || 0;
      const dataExcluded=Object.values(report.exclusions).reduce((sum,count)=>sum+count,0)-insufficient;
      put("norway-exclusions",`Excluded sessions: ${number(dataExcluded)} lack required inputs or prices; ${number(insufficient)} need more energy than the estimated charger can deliver in the connection window. These sessions contribute no savings.`);
      const largest=Math.max(...Array.from(state.byId.values(),row=>row.totalCostUsd));
      function row(result, timerOnly=false) {
        const tr=el("tr"), id=result.strategy, info=definitions[id];
        tr.dataset.strategy=id;
        if(id===state.winner.strategy)tr.classList.add("is-best");
        if(id==="hindsight")tr.classList.add("is-reference");
        if(id===state.bestTimer.strategy)tr.classList.add("is-best-timer");
        const name=el("td");
        name.append(el("span","norway-strategy-label",info?.label || timerLabel(id)));
        if(!timerOnly)name.append(el("span","norway-strategy-description",info?.description || "The lowest-cost fixed timer in this comparison."));
        const badge=id===state.winner.strategy ? "Lowest cost without future prices" : id===state.bestTimer.strategy ? "Best fixed timer" : id==="hindsight" ? "Reference only" : null;
        if(badge)name.append(el("span","norway-strategy-badge",badge));
        const cost=el("td","",money(result.totalCostUsd));
        const bar=el("span","norway-cost-bar"), fill=el("span");
        bar.setAttribute("aria-hidden","true");fill.style.width=`${result.totalCostUsd/largest*100}%`;bar.append(fill);cost.append(bar);
        tr.append(name,cost,el("td",result.savingUsd<0 ? "is-negative" : "",money(result.savingUsd)),el("td",`saving-percent${result.savingPct<0 ? " is-negative" : ""}`,percent(result.savingPct)));
        return tr;
      }
      $("norway-strategy-rows").replaceChildren(...state.main.map(result=>row(result)));
      $("norway-timer-rows").replaceChildren(...state.timers.map(result=>row(result,true)));
    }
    try {
      render();
      $("norway-download").addEventListener("click",()=>{
        const url=URL.createObjectURL(new Blob([csv(report)],{type:"text/csv;charset=utf-8"}));
        const anchor=el("a");anchor.href=url;anchor.download="norway-strategies-2025.csv";doc.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      });
    } catch(error) {
      put("norway-error",error.message);$("norway-error").hidden=false;
      $("norway-download").disabled=true;
      $("norway-strategy-rows").replaceChildren();$("norway-timer-rows").replaceChildren();
      $("norway-results").hidden=true;
    }
  }
  return {view,csv,mount,timerLabel};
});

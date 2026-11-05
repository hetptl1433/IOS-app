/* Original Norway sessions billed at historical Norwegian area spot prices. */
(function (root,factory) {
  "use strict";
  const api=factory();
  if(typeof module==="object" && module.exports)module.exports=api;
  if(root)root.SparcNorwayReplay=api;
})(typeof window!=="undefined" ? window : globalThis,function(){
  "use strict";
  const SLOT=300,HOUR=3600,DAY=86400,EPS=1e-6;
  const formatter=new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Oslo",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",hourCycle:"h23"});
  const strategies=[
    {id:"charge-now",label:"Immediate charging",available:true},
    ...Array.from({length:24},(_,hour)=>({id:`timer-${String(hour).padStart(2,"0")}00`,label:`${hour%12 || 12} ${hour<12 ? "AM" : "PM"} timer`,available:true})),
    ...[25,50,100].map(ore=>({id:`cap-${ore}`,label:`${ore} øre/kWh spot-price cap`,available:true})),
    {id:"day-ahead",label:"SPARC day-ahead",available:true},
    {id:"live",label:"SPARC + live prices",available:false,reason:"This history contains hourly day-ahead spot prices, not real-time price signals. No live-price result is calculated."},
    {id:"hindsight",label:"Perfect hindsight · reference",available:true},
  ];
  const local=t=>{
    const parts=Object.fromEntries(formatter.formatToParts(new Date(t*1000)).filter(p=>p.type!=="literal").map(p=>[p.type,p.value]));
    return {date:`${parts.year}-${parts.month}-${parts.day}`,hour:Number(parts.hour)};
  };
  const addDate=(date,days)=>{const d=new Date(date+"T12:00:00Z");d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);};
  function createMarket(data){
    if(!data || data.dataSource!=="Norway Dataset1" || data.timezone!=="Europe/Oslo" || data.currency!=="NOK")throw new Error("Norway spot-price data in NOK and Europe/Oslo time is required.");
    const areas=new Map(),calendar=new Map();
    let first=Infinity,last=-Infinity;
    for(const [area,rows] of Object.entries(data.prices)){
      const hours=new Map();
      for(const row of rows){
        const [time,price,release]=row;
        if(!Number.isFinite(time) || time%HOUR || !Number.isFinite(price) || !Number.isFinite(release) || release>time)throw new Error("Invalid Norwegian historical price record.");
        if(hours.has(time))throw new Error("Duplicate Norwegian area price hour.");
        hours.set(time,{time,price,release});first=Math.min(first,time);last=Math.max(last,time);
      }
      areas.set(area,hours);
    }
    // Calendar lookup is independent of whether a price exists for that hour.
    for(let hour=first-2*DAY;hour<=last+2*DAY;hour+=HOUR){
      const wall=local(hour),key=wall.date+"|"+wall.hour;
      if(!calendar.has(key))calendar.set(key,hour);
    }
    const at=(date,hour)=>calendar.get(date+"|"+hour) ?? (hour===2 ? calendar.get(date+"|3") : null) ?? null;
    return {data,areas,calendar,at,local};
  }
  function prepare(market,row){
    if(row.issue)return {valid:false,reason:row.issue};
    if(!Number.isFinite(row.start) || !Number.isFinite(row.end) || row.end<=row.start || !Number.isFinite(row.gridKwh) || row.gridKwh<=0)return {valid:false,reason:"Missing or invalid reported energy or connection times."};
    if(!Number.isFinite(row.powerKw) || row.powerKw<=0)return {valid:false,reason:"No estimated charger power for this user."};
    const prices=market.areas.get(row.area);
    if(!prices)return {valid:false,reason:"No Norwegian price-area mapping for this session."};
    const start=Math.floor(row.start/SLOT)*SLOT,end=Math.ceil(row.end/SLOT)*SLOT;
    const slots=[];
    for(let time=start;time<end;time+=SLOT){
      const hour=Math.floor(time/HOUR)*HOUR,price=prices.get(hour);
      if(!price)return {valid:false,reason:"Missing historical Norway spot prices for this session's area or dates."};
      const availableStart=Math.max(time,row.start),availableEnd=Math.min(time+SLOT,row.end);
      slots.push({time,hour,availableStart,availableEnd,capacityKwh:row.powerKw*(availableEnd-availableStart)/HOUR,price});
    }
    return {valid:true,row,prices,slots,start,end,releases:Array.from(new Set(slots.map(slot=>slot.price.release))).sort((a,b)=>a-b),capacityKwh:row.powerKw*(row.end-row.start)/HOUR};
  }
  function knownPrice(market,prepared,hour,now){
    const actual=prepared.prices.get(hour);
    if(actual && actual.release<=now)return actual.price;
    const wall=local(hour);
    // Before tomorrow's publication, reuse the prior local day's same hour.
    // This is an explicit forecast fallback; it never reads an unreleased price.
    const prior=market.at(addDate(wall.date,-1),wall.hour),candidate=prepared.prices.get(prior);
    if(candidate && candidate.release<=now)return candidate.price;
    const current=prepared.prices.get(Math.floor(now/HOUR)*HOUR);
    return current && current.release<=now ? current.price : null;
  }
  function plan(market,prepared,index,remaining,hindsight){
    const now=Math.max(prepared.slots[index].time,prepared.row.start),ordered=[];
    for(let i=index;i<prepared.slots.length;i++){
      const slot=prepared.slots[i],price=hindsight ? slot.price.price : knownPrice(market,prepared,slot.hour,now);
      if(price===null)continue;
      ordered.push({i,price});
    }
    ordered.sort((a,b)=>a.price-b.price || a.i-b.i);
    const schedule=new Map();let left=remaining;
    for(const item of ordered){
      const amount=Math.min(left,prepared.slots[item.i].capacityKwh);
      if(amount>0)schedule.set(item.i,amount);
      left-=amount;if(left<=1e-9)break;
    }
    return schedule;
  }
  function timerStart(market,prepared,id){
    const hour=Number(id.slice(6,8)),arrival=prepared.row.start,wall=local(arrival);
    let date=wall.date,target=market.at(date,hour);
    if(target===null)return prepared.start;
    if(target>arrival){date=addDate(date,-1);target=market.at(date,hour);}
    let requested=target!==null && arrival-target<=6*HOUR ? prepared.start : market.at(addDate(date,1),hour);
    if(requested===null)requested=prepared.start;
    let capacity=0,latest=prepared.start;
    for(let i=prepared.slots.length-1;i>=0;i--){capacity+=prepared.slots[i].capacityKwh;if(capacity>=prepared.row.gridKwh-1e-9){latest=prepared.slots[i].time;break;}}
    return Math.max(prepared.start,Math.min(requested,latest));
  }
  function runPrepared(market,prepared,id){
    const strategy=strategies.find(item=>item.id===id);
    if(!strategy || !strategy.available)return {valid:false,ready:false,costNok:null,energyKwh:0,requiredKwh:prepared.row.gridKwh,slots:[],starts:0,pauses:0,reason:strategy?.reason || "Unknown strategy."};
    const result={valid:true,ready:false,costNok:0,energyKwh:0,requiredKwh:prepared.row.gridKwh,slots:[],starts:0,pauses:0};
    const timer=id.startsWith("timer-") ? timerStart(market,prepared,id) : null;
    const cap=id.startsWith("cap-") ? Number(id.slice(4))/100 : null;
    let remaining=prepared.row.gridKwh,charging=false,schedule=new Map(),version=null;
    const futureCapacity=new Float64Array(prepared.slots.length+1);
    for(let i=prepared.slots.length-1;i>=0;i--)futureCapacity[i]=futureCapacity[i+1]+prepared.slots[i].capacityKwh;
    for(let i=0;i<prepared.slots.length;i++){
      const slot=prepared.slots[i],now=Math.max(slot.time,prepared.row.start);
      let amount=0;
      if(remaining>1e-9){
        if(id==="charge-now" || timer!==null && slot.time>=timer)amount=slot.capacityKwh;
        else if(cap!==null){
          const price=knownPrice(market,prepared,slot.hour,now);
          if(remaining>futureCapacity[i+1]+1e-9 || price!==null && price<=cap)amount=slot.capacityKwh;
        }else if(id==="day-ahead" || id==="hindsight"){
          // A publication changes which future hours may be read; a new hour
          // also updates the explicit fallback forecast for unreleased hours.
          let left=0,right=prepared.releases.length;
          while(left<right){const mid=(left+right)>>>1;if(prepared.releases[mid]<=now)left=mid+1;else right=mid;}
          const nextVersion=id==="hindsight" ? 0 : `${slot.hour}|${left}`;
          if(version!==nextVersion){version=nextVersion;schedule=plan(market,prepared,i,remaining,id==="hindsight");}
          amount=schedule.get(i) || 0;
        }
        amount=Math.max(0,Math.min(amount,slot.capacityKwh,remaining));
      }
      const on=amount>1e-9;
      if(on && !charging)result.starts++;
      if(!on && charging && remaining>EPS)result.pauses++;
      result.energyKwh+=amount;result.costNok+=amount*slot.price.price;remaining-=amount;
      result.slots.push({time:slot.time,availableStart:slot.availableStart,availableEnd:slot.availableEnd,energyKwh:amount,
        priceNokKwh:slot.price.price,cumulativeCost:result.costNok,cumulativeKwh:result.energyKwh});
      charging=on && remaining>EPS;
    }
    result.ready=remaining<=EPS;
    return result;
  }
  function simulate(market,row,id){const prepared=prepare(market,row);return prepared.valid ? runPrepared(market,prepared,id) : {valid:false,ready:false,reason:prepared.reason,costNok:null,energyKwh:0,requiredKwh:row.gridKwh,slots:[],starts:0,pauses:0};}
  function compareSession(market,row){
    const prepared=prepare(market,row);
    if(!prepared.valid)return {valid:false,reason:prepared.reason,row,eligible:false,tracks:[]};
    const tracks=strategies.map(strategy=>({...strategy,result:strategy.available ? runPrepared(market,prepared,strategy.id) : null}));
    const eligible=tracks.filter(track=>track.available).every(track=>track.result.valid && track.result.ready && Math.abs(track.result.energyKwh-row.gridKwh)<=EPS);
    return {valid:true,row,eligible,tracks,reason:eligible ? null : "The estimated charger cannot complete the requested energy, or a strategy missed the target."};
  }
  function summarize(runs){
    const summary={total:runs.length,comparable:0,skipped:0,shortfall:0,energyKwh:0,baselineCostNok:0,exclusions:{},results:strategies.map(strategy=>({...strategy,costNok:strategy.available ? 0 : null,savingNok:null,savingPct:null,completedSessions:0,starts:0,pauses:0}))};
    const totals=new Map(summary.results.map(item=>[item.id,item]));
    for(const run of runs){
      if(!run.valid){summary.skipped++;summary.exclusions[run.reason]=(summary.exclusions[run.reason] || 0)+1;continue;}
      if(!run.eligible){summary.shortfall++;continue;}
      summary.comparable++;summary.energyKwh+=run.row.gridKwh;
      const baseline=run.tracks.find(track=>track.id==="charge-now").result;
      summary.baselineCostNok+=baseline.costNok;
      for(const track of run.tracks){if(!track.available)continue;const total=totals.get(track.id);total.costNok+=track.result.costNok;total.completedSessions++;total.starts+=track.result.starts;total.pauses+=track.result.pauses;}
    }
    for(const result of summary.results){if(!result.available)continue;result.savingNok=summary.baselineCostNok-result.costNok;result.savingPct=summary.baselineCostNok>0 ? result.savingNok/summary.baselineCostNok*100 : null;}
    return summary;
  }
  function exportCsv(runs){
    const fields=["session_id","user_id","location","price_area","plugin_oslo","plugout_oslo","strategy","status","reason","grid_kwh","delivered_kwh","cost_nok","baseline_nok","saving_nok","currency","cost_scope"];
    const lines=[fields];
    for(const run of runs){
      const baseline=run.tracks.find(track=>track.id==="charge-now")?.result?.costNok;
      const tracks=run.tracks.length ? run.tracks : [{id:"",available:true,result:null}];
      for(const track of tracks){const outcome=track.result;lines.push([run.row.id,run.row.userId,run.row.site,run.row.area,run.row.startLocal,run.row.endLocal || "",track.id,
        !track.available ? "unavailable" : !run.valid ? "skipped" : !run.eligible ? "shortfall" : "compared",track.reason || run.reason || "",run.row.gridKwh,
        outcome?.energyKwh ?? "",outcome?.costNok ?? "",baseline ?? "",run.eligible && track.available ? baseline-outcome.costNok : "","NOK","historical wholesale spot energy; no added grid fees, VAT, taxes, markup or subsidy"]);}
    }
    const quote=value=>`"${String(value ?? "").replace(/"/g,'""')}"`;
    return lines.map(line=>line.map(quote).join(",")).join("\r\n");
  }
  return {createMarket,strategies,prepare,knownPrice,simulate,compareSession,summarize,exportCsv,local,addDate,SLOT};
});

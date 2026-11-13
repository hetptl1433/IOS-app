"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const Replay=require("./dist/norway-local-engine.js");
const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,"dist/norway-local-data.js"),"utf8"),context);
const data=context.window.SPARC_NORWAY_LOCAL_DATA;
const market=Replay.createMarket(data);
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
const original=data.rows.find(row=>!row.issue && row.gridKwh>10 && row.powerKw && row.startLocal.startsWith("2019-08") && Replay.prepare(market,row).valid && row.gridKwh/row.powerKw<(row.end-row.start)/3600-2);

function synthetic(){
  const rows=[];
  for(let h=0;h<24*5;h++){
    const time=Date.UTC(2020,0,13)/1000+h*3600;
    const wall=Replay.local(time),hour=wall.hour;
    const release=Date.parse(`${Replay.addDate(wall.date,-1)}T12:00:00Z`)/1000;
    rows.push([time,hour<6 ? .1 : 1,release]);
  }
  const bundle={dataSource:"Norway Dataset1",currency:"NOK",timezone:"Europe/Oslo",prices:{NO1:rows}};
  const row={id:"test",userId:"user",site:"ASK",area:"NO1",start:Date.UTC(2020,0,15,19)/1000,end:Date.UTC(2020,0,16,6)/1000,
    gridKwh:8,powerKw:4,issue:null,startLocal:"2020-01-15 20:00:00",endLocal:"2020-01-16 07:00:00"};
  return {bundle,row};
}

test("source dates, Oslo time, NOK and geographic price areas are preserved",()=>{
  assert.equal(data.rows.length,35377);
  assert.equal(data.rows[0].startLocal.slice(0,4),"2018");
  for(const row of data.rows){assert.equal(row.start,row.sourceStart);assert.equal(row.end,row.sourceEnd);}
  assert.equal(data.rows.find(row=>row.site==="BOD").area,"NO4");
  assert.equal(data.rows.find(row=>row.site==="BER").area,"NO5");
  assert.equal(data.rows.find(row=>row.site==="TRO_R").area,"NO3");
  assert.equal(data.rows.find(row=>row.site==="KRO").area,"NO1");
  assert.equal(data.currency,"NOK");assert.equal(data.timezone,"Europe/Oslo");
  assert.equal(Replay.local(Date.parse("2019-06-30T22:00:00Z")/1000).date,"2019-07-01");
});

test("price normalization retains precision, negative values, and declared DST gaps",()=>{
  const midnight=Date.parse("2019-06-30T22:00:00Z")/1000;
  close(market.areas.get("NO1").get(midnight).price,.2658);
  assert.ok(Array.from(market.areas.values()).some(hours=>Array.from(hours.values()).some(row=>row.price<0)));
  for(const date of ["2019-10-27","2020-03-29","2020-10-25","2021-03-28"]){
    for(const hours of market.areas.values())assert.ok(!Array.from(hours.keys()).some(time=>Replay.local(time).date===date));
  }
  assert.throws(()=>Replay.createMarket({...data,currency:"USD"}),/NOK/);
});

test("missing prices, boundaries and power are skipped rather than invented",()=>{
  assert.equal(Replay.compareSession(market,data.rows[0]).valid,false);
  assert.match(Replay.compareSession(market,data.rows[0]).reason,/historical Norway spot prices/);
  assert.equal(Replay.compareSession(market,{...original,end:null}).valid,false);
  assert.equal(Replay.compareSession(market,{...original,powerKw:null}).valid,false);
  assert.equal(Replay.compareSession(market,{...original,area:"unknown"}).valid,false);
});

test("unreleased next-day prices cannot change SPARC's pre-publication decisions",()=>{
  const {bundle,row}=synthetic();
  row.start=Date.UTC(2020,0,15,10)/1000;
  const first=Replay.createMarket(bundle),prepared=Replay.prepare(first,row);
  const target=Date.UTC(2020,0,16,1)/1000,release=first.areas.get("NO1").get(target).release;
  const forecast=Replay.knownPrice(first,prepared,target,release-1);
  const changed=structuredClone(bundle);
  for(const price of changed.prices.NO1)if(price[2]===release)price[1]+=100;
  const second=Replay.createMarket(changed),preparedSecond=Replay.prepare(second,row);
  close(Replay.knownPrice(second,preparedSecond,target,release-1),forecast);
  close(Replay.knownPrice(second,preparedSecond,target,release),first.areas.get("NO1").get(target).price+100);
  const a=Replay.simulate(first,row,"day-ahead"),b=Replay.simulate(second,row,"day-ahead");
  assert.deepEqual(a.slots.filter(slot=>slot.time<release).map(slot=>slot.energyKwh),b.slots.filter(slot=>slot.time<release).map(slot=>slot.energyKwh));
  assert.notDeepEqual(a.slots.filter(slot=>slot.time>=release).map(slot=>slot.energyKwh),b.slots.filter(slot=>slot.time>=release).map(slot=>slot.energyKwh));
});

test("published overnight spot prices give SPARC and hindsight the same energy cost",()=>{
  const {bundle,row}=synthetic(),m=Replay.createMarket(bundle);
  const plan=Replay.simulate(m,row,"day-ahead"),best=Replay.simulate(m,row,"hindsight");
  assert.equal(plan.ready,true);assert.equal(best.ready,true);
  close(plan.costNok,best.costNok);close(plan.costNok,.8);
});

test("every available method respects exact partial connection capacity",()=>{
  const {bundle,row}=synthetic();
  for(const price of bundle.prices.NO1)price[1]=10;
  row.start=Date.UTC(2020,0,15,19,1)/1000;row.end=row.start+480;row.powerKw=3.6;row.gridKwh=.3;
  const run=Replay.compareSession(Replay.createMarket(bundle),row);
  assert.equal(run.eligible,true);
  assert.equal(run.tracks.length,31);
  for(const track of run.tracks){
    if(!track.available){assert.equal(track.id,"live");assert.equal(track.result,null);continue;}
    assert.equal(track.result.ready,true,track.id);close(track.result.energyKwh,.3);
    close(track.result.costNok,3);
    for(const slot of track.result.slots){assert.ok(slot.availableStart>=row.start);assert.ok(slot.availableEnd<=row.end);assert.ok(slot.energyKwh<=row.powerKw*(slot.availableEnd-slot.availableStart)/3600+1e-9);}
  }
});

test("sessions needing omitted clock-change prices are excluded",()=>{
  const row={...original,start:Date.parse("2020-03-28T23:30:00Z")/1000,end:Date.parse("2020-03-29T03:00:00Z")/1000};
  const run=Replay.compareSession(market,row);
  assert.equal(run.valid,false);assert.equal(run.eligible,false);
  assert.match(run.reason,/Missing historical Norway spot prices/);
});

test("negative spot prices remain negative costs and impossible targets add no savings",()=>{
  const {bundle,row}=synthetic();
  for(const price of bundle.prices.NO1)price[1]=-.5;
  const m=Replay.createMarket(bundle),run=Replay.compareSession(m,row);
  assert.equal(run.eligible,true);
  for(const track of run.tracks)if(track.available)close(track.result.costNok,-4);
  const summary=Replay.summarize([run]);
  close(summary.baselineCostNok,-4);
  assert.ok(summary.results.every(result=>result.savingPct===null));
  const impossible=Replay.compareSession(m,{...row,gridKwh:1000});
  assert.equal(impossible.eligible,false);
  const missed=Replay.summarize([impossible]);
  assert.equal(missed.shortfall,1);assert.equal(missed.comparable,0);assert.equal(missed.baselineCostNok,0);
});

test("no real-time result or fictional fees enter the Norway aggregate or CSV",()=>{
  const run=Replay.compareSession(market,original),summary=Replay.summarize([run]);
  assert.equal(summary.comparable,1);
  const immediate=run.tracks.find(track=>track.id==="charge-now").result;
  close(immediate.costNok,immediate.slots.reduce((sum,slot)=>sum+slot.energyKwh*slot.priceNokKwh,0));
  const live=summary.results.find(result=>result.id==="live");
  assert.equal(live.available,false);assert.equal(live.costNok,null);assert.equal(live.savingPct,null);
  assert.match(Replay.exportCsv([run]),/NOK/);
  assert.match(Replay.exportCsv([run]),/"unavailable"/);
  assert.ok(!Replay.exportCsv([run]).includes("USD"));
});

test("the representative original-date replay conserves energy and weighted savings",()=>{
  const chosen=new Map();
  for(const row of data.rows){
    if(chosen.has(row.area) || row.issue || !row.powerKw || row.startLocal<"2019-07-02")continue;
    const run=Replay.compareSession(market,row);if(run.eligible)chosen.set(row.area,run);
  }
  assert.equal(chosen.size,4);
  const summary=Replay.summarize(Array.from(chosen.values()));
  for(const result of summary.results){if(!result.available)continue;assert.equal(result.completedSessions,4);close(summary.baselineCostNok-result.costNok,result.savingNok);close(result.savingPct,result.savingNok/summary.baselineCostNok*100);}
});

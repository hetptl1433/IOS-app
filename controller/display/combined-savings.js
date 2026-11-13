'use strict';
(() => {
  const host=document.getElementById('combined-results');
  let previous=null,retryAfter=0,open=false;
  const known=x=>typeof x==='number'&&Number.isFinite(x);
  const fmt=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
  const money=x=>known(x)?fmt.format(x):'—';
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function short(d){const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(d||''));return m&&MONTHS[+m[2]-1]?MONTHS[+m[2]-1]+' '+(+m[3]):'';}
  function node(tag,text,cls=''){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
  function row(label,value,cls=''){const n=node('div','','combined-row'+(cls?' '+cls:''));n.append(node('span',label),node('strong',value,'num'));return n;}
  function render(report){
    const t=report.summary||{},card=document.createDocumentFragment();
    const start=short(report.start),end=short(report.end);
    const top=node('div','','combined-top');
    top.append(node('span',start&&end?start+' – '+end:'','label'));
    const btn=node('button','i','info-btn');btn.type='button';btn.setAttribute('aria-label',"How it's calculated");btn.setAttribute('aria-expanded',String(open));
    top.append(btn);card.append(top);
    const pct=known(t.savingsPercent)?t.savingsPercent:null,neg=pct!==null&&pct<0;
    const big=node('p','','combined-big'+(neg?' neg':''));
    big.append(node('span',pct!==null?Math.round(Math.abs(pct))+'%':'—','combined-amount num'));
    if(pct!==null)big.append(node('span',neg?'more':'saved','combined-unit'));
    card.append(big);
    const nights=Array.isArray(report.nights)?report.nights:[];
    const vals=nights.map(n=>known(n.savingsUSD)?n.savingsUSD:0),max=Math.max(0.01,...vals.map(Math.abs));
    const bars=node('div','','combined-bars');bars.setAttribute('aria-hidden','true');
    vals.forEach(v=>{const b=node('span','','combined-bar'+(v<0?' neg':''));b.style.height=Math.max(v===0?2:4,Math.round(Math.abs(v)/max*100))+(v===0?'px':'%');bars.append(b);});
    card.append(bars);
    const panel=node('div','','combined-info');panel.hidden=!open;
    panel.append(row('Fixed-price plan',money(t.baselineCostUSD)),row('With SPARC',money(t.systemCostUSD)),
      row('Hourly pricing',money(t.hourlySavingsUSD)),row('Time-of-Day delivery',money(t.deliverySavingsUSD)),row('Smart timing',money(t.schedulingSavingsUSD)),
      row('Saved',money(t.savingsUSD),'strong'));
    panel.append(node('p','Same charging both ways · supply + delivery · taxes and fixed fees not included.','caption'));
    if(t.unpricedNightCount>0)panel.append(node('p',t.unpricedNightCount+(t.unpricedNightCount===1?' night':' nights')+' without prices not counted.','caption'));
    card.append(panel);
    btn.addEventListener('click',()=>{open=!open;panel.hidden=!open;btn.setAttribute('aria-expanded',String(open));});
    host.replaceChildren(card);
  }
  async function load(){
    retryAfter=0;
    try{
      const response=await fetch('/api/savings?kind=simulation',{cache:'no-store'});
      if(!response.ok)throw new Error('unavailable');
      const report=await response.json();
      const encoded=JSON.stringify(report);if(encoded===previous)return;
      render(report);previous=encoded;
    }catch{previous=null;retryAfter=Date.now()+3000;host.replaceChildren(node('p','Savings unavailable','caption'));}
  }
  // Recover with the existing successful controller heartbeat, even when a browser throttles the 30-second timer.
  document.addEventListener('sparc-state',()=>{if(retryAfter&&Date.now()>=retryAfter&&!document.getElementById('view-savings').hidden)load();});
  load();setInterval(()=>{if(!document.getElementById('view-savings').hidden)load();},30000);
})();

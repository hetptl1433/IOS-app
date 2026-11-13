"""Verify money semantics, routine writes and UI wiring without a real browser."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


DISPLAY = Path(__file__).resolve().parents[1] / 'display'
NODE = shutil.which('node')


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        self.values.extend(value for name, value in attrs if name == 'id')


class SavingsDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javascript = (DISPLAY/'display.js').read_text()
        cls.prefix = cls.javascript.split('// The Pi\'s own screen.')[0]

    def evaluate(self, expression):
        if not NODE: self.skipTest('Node is needed for local JavaScript checks')
        result = subprocess.run([NODE, '-e', self.prefix + '\nconsole.log(JSON.stringify(' + expression + '));'],
                                check=True, capture_output=True, text=True, timeout=10)
        return json.loads(result.stdout)

    def test_unknown_prices_do_not_become_zero_and_negative_prices_remain_credits(self):
        self.assertEqual(self.evaluate('[null,undefined,NaN,Infinity,"0",0,-0.25].map(SavingsFormat.money)'),
                         ['—','—','—','—','—','$0.00','-$0.25'])

    def test_negative_savings_are_presented_as_extra_cost(self):
        self.assertEqual(self.evaluate('[SavingsFormat.saving(-0.25,-5),SavingsFormat.saving(0,0),SavingsFormat.saving(null)]'),
                         ['Extra cost $0.25 · 5.0%','Saved $0.00 · 0.0%','Comparison pending'])

    def test_percentages_keep_credits_zero_unknown_and_no_charging_distinct(self):
        records = [
            {'savingsUSD': -.007666, 'baselineCostUSD': 1.521833, 'sessionCount': 1},
            {'savingsUSD': .2, 'baselineCostUSD': -1, 'sessionCount': 1},
            {'savingsUSD': 0, 'baselineCostUSD': 1, 'sessionCount': 1},
            {'savingsUSD': None, 'baselineCostUSD': 1, 'sessionCount': 1},
            {'savingsUSD': 0, 'baselineCostUSD': 0, 'sessionCount': 1},
            {'savingsUSD': 0, 'baselineCostUSD': 0, 'sessionCount': 0},
            {'savingsUSD': 0, 'baselineCostUSD': 0, 'energyKWh': 0, 'sessionCount': 1},
        ]
        actual = self.evaluate(json.dumps(records) + '.map(x=>{const r=SavingsFormat.comparison(x,true);return [SavingsFormat.signedPercent(r.value),r.label,r.tone];})')
        self.assertEqual(actual, [
            ['-0.5%', 'Higher cost', 'extra'], ['+20.0%', 'Saved', 'saved'],
            ['0.0%', 'No savings', 'saved'], ['—', 'Awaiting prices', 'unknown'],
            ['—', 'Comparison unavailable', 'unknown'], ['—', 'No charging', 'empty'],
            ['—', 'No charging', 'empty'],
        ])

    def test_week_uses_provided_weighted_percentage_and_old_records_derive_from_baseline(self):
        self.assertEqual(self.evaluate('[SavingsFormat.comparison({savingsPercent:9.577674,savingsUSD:1,baselineCostUSD:2}).value,SavingsFormat.comparison({savingsUSD:1,baselineCostUSD:2}).value,SavingsFormat.comparison({savingsUSD:1}).value]'),
                         [9.577674, 50, None])

    def test_campaign_payload_keeps_revision_and_omits_server_identity(self):
        draft = {'enabled': True, 'startDate':'2026-11-05', 'nights':7, 'startMinutes':1320,
                 'departureMinutes':420, 'initialBatteryPercent':20, 'targetBatteryPercent':80,
                 'screenSleepMinutes':5, 'expectedRevision':3, 'revision':4,
                 'campaignId':'read-only', 'currentSessionId':'read-only'}
        result = self.evaluate('SavingsFormat.campaignBody(' + json.dumps(draft) + ')')
        self.assertEqual(result['expectedRevision'],3)
        self.assertNotIn('campaignId',result)
        self.assertNotIn('revision',result)
        self.assertTrue(self.evaluate('SavingsFormat.validCampaign(' + json.dumps(result) + ')'))
        for changes in ({'nights':91},{'expectedRevision':None},{'startDate':'2026-02-30'},
                        {'initialBatteryPercent':80},{'screenSleepMinutes':True}):
            with self.subTest(changes=changes):
                self.assertFalse(self.evaluate('SavingsFormat.validCampaign(' + json.dumps(result|changes) + ')'))

    def test_calendar_day_controls_do_not_drift_across_dst_or_month_end(self):
        self.assertEqual(self.evaluate('[SavingsFormat.addDays("2026-11-01",1),SavingsFormat.addDays("2026-01-31",1),SavingsFormat.addDays("2028-03-01",-1)]'),
                         ['2026-11-02','2026-02-01','2028-02-29'])

    def test_all_literal_element_references_exist_and_ids_are_unique(self):
        parser = Ids(); parser.feed((DISPLAY/'index.html').read_text())
        self.assertEqual(len(parser.values),len(set(parser.values)))
        referenced = set(re.findall(r"\$\('([^']+)'\)",self.javascript))
        self.assertFalse(referenced-set(parser.values), referenced-set(parser.values))

    @unittest.skipUnless(NODE, 'Node is needed for local JavaScript checks')
    def test_javascript_syntax(self):
        subprocess.run([NODE,'--check',str(DISPLAY/'display.js')],check=True,capture_output=True,timeout=10)

    @unittest.skipUnless(NODE, 'Node is needed for local JavaScript checks')
    def test_ui_loads_missing_prices_and_only_saves_routine_after_explicit_tap(self):
        harness = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[1],'utf8'),source=fs.readFileSync(process.argv[2],'utf8');
class Node {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.attrs={};this.listeners={};this.hidden=false;this.scrollTop=0;this._text='';this.open=false;this.classList={add(){},remove(){},toggle(){}};}
 set textContent(v){this._text=String(v);this.children=[];}
 get textContent(){return this._text+this.children.map(n=>n.textContent||'').join('');}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
 addEventListener(name,fn){this.listeners[name]=fn;}
 setAttribute(k,v){this.attrs[k]=v;}
 getAttribute(k){return this.attrs[k]||'';}
 getBoundingClientRect(){return {left:0,top:0,width:100,height:50};}
 querySelector(q){if(!this.parts)this.parts={};return this.parts[q]||(this.parts[q]=new Node());}
 querySelectorAll(q){const all=this.children.flatMap(n=>[n,...n.querySelectorAll('*')]);return q==='*'?all:q==='details[open]'?all.filter(n=>n.tagName==='details'&&n.open):[];}
 getContext(){return {setTransform(){}};}
}
const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],new Node()]));
const queryNodes={};
const document={getElementById(id){assert(nodes[id],`Missing element ${id}`);return nodes[id];},createElement(tag){return new Node(tag);},querySelectorAll(){return [];},querySelector(q){return queryNodes[q]||(queryNodes[q]=new Node());},addEventListener(){},dispatchEvent(){},documentElement:new Node('html')};
const campaign={enabled:true,startDate:'2026-11-05',nights:7,startMinutes:1320,departureMinutes:420,initialBatteryPercent:20,targetBatteryPercent:80,screenSleepMinutes:5,revision:1,phase:'waiting',nextStart:'2026-11-06T22:00:00-06:00'};
const state={ok:true,weekly:{enabled:false,days:[],mode:'fixed',departureMinutes:420,fixedStartMinutes:1320,targetPercent:80},trip:null,overnight:null,manual:null,snapshot:null,controller:{},pricing:{},campaign};
const history={week:{start:'2026-11-02',end:'2026-11-09',energyKWh:5,costUSD:null,savingsUSD:null,savingsPercent:null,unpricedSessions:1,provisional:true,days:[{date:'2026-11-05',energyKWh:5,costUSD:null,sessionCount:1}]},sessions:[{id:'old',nightDate:'2026-11-05',start:'2026-11-05T22:00:00-06:00',departure:'2026-11-06T07:00:00-06:00',status:'complete',energyKWh:5,costUSD:-0.1,savingsUSD:-0.2,savingsPercent:-20,costFinal:true,baselineFinal:true,initialBatteryPercent:20,targetBatteryPercent:80,batteryPercent:80,targetReachedByDeadline:true}]};
const requests=[];
const context={console,document,Intl,Date,Math,Number,Map,Set,URLSearchParams,structuredClone,location:{search:'',hash:'#savings'},performance:{now:()=>10000},matchMedia:()=>({matches:true}),innerWidth:1024,innerHeight:600,requestAnimationFrame(){},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},fetch:async(path,options={})=>{requests.push({path,options});let value=path.startsWith('/api/history')?history:state;if(options.method==='POST'){const body=JSON.parse(options.body);state.campaign={...campaign,...body,revision:2};value=state;}return {ok:true,json:async()=>structuredClone(value)};}};
context.CustomEvent=class { constructor(type,options){this.type=type;this.detail=options.detail;} };
context.window={devicePixelRatio:1,addEventListener(){}};
vm.runInNewContext(source,context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(nodes['savings-cost'].textContent,'—');
 assert.equal(nodes['savings-percent'].textContent,'—');
 assert.equal(nodes['savings-amount'].textContent,'Awaiting prices');
 assert(nodes['daily-savings'].textContent.includes('Awaiting prices'));
 assert(nodes['session-list'].textContent.includes('-$0.10'));
 assert(nodes['session-list'].textContent.includes('Extra cost $0.20'));
 assert(nodes['session-list'].textContent.includes('-20.0% higher cost'));
 const makeDay=(date,values={})=>({date,sessionCount:0,energyKWh:0,costUSD:0,savingsUSD:0,baselineCostUSD:0,...values});
 history.week={...history.week,energyKWh:25,costUSD:3.2,savingsUSD:.344796264,baselineCostUSD:3.6,savingsPercent:9.577674,days:[
   makeDay('2026-11-02'),makeDay('2026-11-03',{sessionCount:1,energyKWh:5,costUSD:null,savingsUSD:null,baselineCostUSD:1}),
   makeDay('2026-11-04',{sessionCount:1,energyKWh:5,savingsUSD:.51746276,baselineCostUSD:2}),
   makeDay('2026-11-05',{sessionCount:1,energyKWh:5,savingsUSD:-.007666,baselineCostUSD:1.521833}),
   makeDay('2026-11-06',{sessionCount:1,energyKWh:5,baselineCostUSD:1}),
   makeDay('2026-11-07',{sessionCount:1,energyKWh:5}),makeDay('2026-11-08')
 ]};
 history.sessions.push({id:'derived',nightDate:'2026-11-05',start:'2026-11-06T04:00:00Z',departure:'2026-11-06T13:00:00Z',energyKWh:5,costUSD:1.529499,savingsUSD:-.007666,baselineCostUSD:1.521833,status:'complete'});
 await nodes['history-refresh'].listeners.click();
 assert.equal(nodes['savings-percent'].textContent,'+9.6%');
 const daily=nodes['daily-savings'].children;
 assert.equal(daily.length,7);
 assert(daily[0].textContent.includes('No charging'));
 assert(daily[1].textContent.includes('Awaiting prices'));
 assert(daily[2].textContent.includes('WedNov 4+25.9%Saved'));
 assert(daily[3].textContent.includes('ThuNov 5-0.5%Higher cost'));
 assert(daily[3].className.includes('extra'));
 assert(daily[3].attrs['aria-label'].includes('Thu Nov 5 starting night, -0.5%, Higher cost'));
 assert(daily[4].textContent.includes('0.0%No savings'));
 assert(daily[5].textContent.includes('Comparison unavailable'));
 daily[3].listeners.click();
 assert(nodes['day-detail'].textContent.includes('Thu Nov 5 starting night'));
 assert(nodes['day-detail'].textContent.includes('-0.5% higher cost'));
 assert(nodes['session-list'].children[1].textContent.includes('Nov 5'));
 assert(nodes['session-list'].children[1].textContent.includes('-0.5% higher cost'));
 assert.equal(nodes['campaign-target'].textContent,'80%');
 nodes['campaign-target-more'].listeners.click({detail:0});
 assert.equal(nodes['campaign-target'].textContent,'85%');
 assert.equal(requests.filter(x=>x.options.method==='POST').length,0);
 await nodes['campaign-save'].listeners.click({currentTarget:nodes['campaign-save']});
 const post=requests.find(x=>x.options.method==='POST');
 assert.equal(post.path,'/api/campaign');
 const body=JSON.parse(post.options.body);
 assert.equal(body.targetBatteryPercent,85);assert.equal(body.expectedRevision,1);
 assert.equal(body.startMinutes,1320);assert.equal(body.departureMinutes,420);
 assert(!('campaignId' in body));
 assert.equal(nodes['campaign-target'].textContent,'85%');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        subprocess.run([NODE,'-e',harness,str(DISPLAY/'index.html'),str(DISPLAY/'display.js')],
                       check=True,capture_output=True,text=True,timeout=10)


if __name__=='__main__': unittest.main()

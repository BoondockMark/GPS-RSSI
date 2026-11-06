const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function node(){return {children:[],attributes:{},textContent:'',setAttribute(k,v){this.attributes[k]=v;},
  appendChild(c){this.children.push(c);},replaceChildren(){this.children=[];}};}
const context={document:{createElement:node,createElementNS:node}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/gps_rssi/assets/visualization.js'),'utf8'),context);
const visuals=context.RssiVisuals;
const plain=value=>JSON.parse(JSON.stringify(value));
const walk=item=>[item,...item.children.flatMap(walk)];

test('shared scale distinguishes zero, missing, out-of-range and maximum signal',()=>{
  assert.equal(visuals.color(0),'#440154');assert.equal(visuals.color(100),'#fde725');
  assert.equal(visuals.color(null),'#94a3b8');assert.equal(visuals.valid(false),false);
  assert.equal(visuals.valid(NaN),false);assert.equal(visuals.valid(-1),false);
});
test('statistics retain zero and bin 100 without treating missing samples as zero',()=>{
  const s=visuals.statistics([0,10,100,null,NaN,-1].map(rssi=>({rssi})));
  assert.equal(s.count,3);assert.equal(s.median,10);assert.equal(s.p10,2);
  assert.equal(s.min,0);assert.equal(s.max,100);
  assert.deepEqual(plain(s.bins),[1,1,0,0,0,0,0,0,0,1]);
  assert.equal(visuals.statistics([]).median,null);
});
test('large timelines remain bounded and retain observed weak and strong extremes',()=>{
  const samples=Array.from({length:10000},(_,i)=>({key:String(i),rssi:i===123?0:i===456?100:50,
    time:new Date(1700000000000+i*1000).toISOString()}));
  const chosen=visuals.timelinePoints(samples);
  assert.ok(chosen.length<=602);assert.ok(chosen.some(p=>p.key==='123'));assert.ok(chosen.some(p=>p.key==='456'));
  assert.ok(chosen.every(p=>samples.includes(p)));
});
test('chart panels separate receiver sessions rather than averaging their scales',()=>{
  const container=node();
  visuals.render(container,[{direction:'uplink',seriesKey:'a',seriesLabel:'Radio A',rssi:0,time:'2026-11-06T00:00:00Z'},
    {direction:'uplink',seriesKey:'b',seriesLabel:'Radio B',rssi:100,time:'2026-11-06T00:00:02Z'}]);
  assert.equal(container.children.length,2);
  assert.match(container.children[0].children[1].textContent,/Median 0.0/);
  assert.match(container.children[1].children[1].textContent,/Median 100.0/);
});
test('keyboard and pointer selection identify actual measurements',()=>{
  const container=node(),selected=[];
  visuals.render(container,[{key:'sample-0',direction:'downlink',rssi:0,time:'2026-11-06T00:00:00Z'}],{onSelect:k=>selected.push(k)});
  const dot=walk(container).find(n=>n.attributes.role==='button');
  dot.onclick();let prevented=false;
  dot.onkeydown({key:'Enter',preventDefault(){prevented=true;}});
  assert.deepEqual(selected,['sample-0','sample-0']);assert.equal(prevented,true);
});
test('empty signal history explains missing measurements without a false zero',()=>{
  const container=node();visuals.render(container,[]);
  assert.match(container.children[0].textContent,/No qualified receive/);
  assert.equal(walk(container).some(n=>n.attributes.role==='button'),false);
});

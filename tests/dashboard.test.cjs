// Optional frontend behavior checks: node tests/dashboard.test.cjs
// The actual dashboard runs in a DOM/Leaflet harness, without external services.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/dashboard.js'),'utf8');
const point=id=>({id,session:'session-one',station:'mobile',source:'btech_bluetooth',
  lat:33.96,lon:-117.95,rssi:50,squelch_open:true,time:'2026-11-06T00:00:00Z',channel:1});
const batch=(ids,extra={})=>({points:ids.map(point),excluded:{},processed:ids.length,
  cursor:ids.at(-1)||0,generation:'first',reset:false,more:false,latest:null,link_event:null,...extra});

async function harness(batches,{leaflet=true,maxPoints=2}={}){
  const elements=new Map();
  function node(){return {style:{},children:[],textContent:'',checked:false,
    get innerHTML(){return this.textContent.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');},
    appendChild(child){this.children.push(child);},replaceChildren(){this.children=[];}};}
  const element=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  const calls=[],timers=[],pans=[],fits=[];
  const map={setView(){return this;},fitBounds(bounds){fits.push(bounds);},panTo(p){pans.push(p);},removeLayer(){}};
  const control={addTo(){return this;},addOverlay(){},removeLayer(){}};
  const config={refresh:2,max_points:maxPoints,max_age:10,max_accuracy:100,source:'live',area:{}};
  const context={console,document:{getElementById:element,createElement:node},URLSearchParams,AbortSignal,
    setTimeout(fn,delay){timers.push({fn,delay});},setInterval(){},
    async fetch(url){
      calls.push(url);
      if(url==='/api/config')return {ok:true,json:async()=>config};
      const value=batches.shift();
      if(value instanceof Error)throw value;
      if(!value)throw Error('No test snapshot queued');
      return {ok:true,json:async()=>value};
    }};
  if(leaflet)context.L={map:()=>map,tileLayer:()=>({addTo(){}}),
    control:{layers:()=>control},latLngBounds:p=>p,
    geoJSON:()=>({addTo(){return this;},getBounds(){return 'area';}}),
    layerGroup:()=>({nodes:new Set(),addTo(){return this;},removeLayer(n){this.nodes.delete(n);}}),
    circleMarker:()=>({bindPopup(){return this;},addTo(group){group.nodes.add(this);return this;}})};
  vm.createContext(context);vm.runInContext(source,context);
  async function flush(){for(let i=0;i<15;i++)await Promise.resolve();}
  await flush();
  return {element,calls,timers,pans,fits,context,
    state:expression=>vm.runInContext(expression,context),
    async tick(){assert.ok(timers.length,'poll should schedule another update');await timers.shift().fn();await flush();}};
}

test('polls incrementally, deduplicates and limits displayed points without recentering',async()=>{
  const h=await harness([batch([1],{reset:true}),batch([1,2,3],{cursor:3})]);
  assert.equal(h.state('points.length'),1);
  await h.tick();
  assert.equal(h.state('points.length'),2);
  assert.equal(h.state('points[0].id'),2);
  assert.match(h.calls[2],/after=1&generation=first/);
  assert.equal(h.element('rows').children.length,2);
  assert.equal(h.pans.length,0);
  assert.deepEqual(h.fits,['area']);
});

test('keeps displayed data after API errors and resumes polling',async()=>{
  const h=await harness([batch([1],{reset:true}),Error('server unavailable'),batch([2],{cursor:2})]);
  await h.tick();
  assert.equal(h.state('points.length'),1);
  assert.match(h.element('error').textContent,/server unavailable/);
  await h.tick();
  assert.equal(h.state('points.length'),2);
  assert.equal(h.element('error').textContent,'');
});

test('pause skips requests; follow-newest pans only when selected',async()=>{
  const h=await harness([batch([1],{reset:true}),batch([2],{cursor:2})]);
  h.element('pause').onclick();
  const requests=h.calls.length;
  await h.tick();assert.equal(h.calls.length,requests);
  h.element('follow').checked=true;h.element('pause').onclick();
  await h.tick();assert.equal(h.pans.length,1);
});

test('database reset clears old points and exclusion totals',async()=>{
  const h=await harness([batch([9],{reset:true,excluded:{no_gps_lock:2}}),
    batch([1],{reset:true,generation:'replacement'})]);
  await h.tick();
  assert.equal(h.state('points.length'),1);
  assert.equal(h.state('points[0].id'),1);
  assert.equal(h.state('Object.keys(exclusions).length'),0);
});

test('missing Leaflet still permits live measurement table updates',async()=>{
  const h=await harness([batch([1],{reset:true})],{leaflet:false});
  assert.equal(h.element('rows').children.length,1);
  assert.match(h.element('error').textContent,/Map library unavailable/);
  assert.ok(h.timers.length);
});

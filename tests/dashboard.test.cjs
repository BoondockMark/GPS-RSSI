// Optional frontend behavior checks: node tests/dashboard.test.cjs
// The actual dashboard runs in a DOM/Leaflet harness, without external services.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/dashboard.js'),'utf8');
const visuals=fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/visualization.js'),'utf8');
const point=id=>({id,session:'session-one',station:'mobile',source:'benlink_bluetooth',
  lat:33.96,lon:-117.95,rssi:50,squelch_open:true,time:'2026-11-06T00:00:00Z',channel:1});
const batch=(ids,extra={})=>({points:ids.map(point),excluded:{},processed:ids.length,
  cursor:ids.at(-1)||0,generation:'first',reset:false,more:false,latest:null,link_event:null,
  telemetry:ids.map(id=>({id,session:'session-one',station:'mobile',direction:'downlink',received_at:'2026-11-06T00:00:00Z',
    rssi_value:50,rssi_unit:'benshi_relative_0_100',is_in_rx:true,is_in_tx:false})),...extra});

async function harness(batches,{leaflet=true,maxPoints=2,enableControls=false,operations=[],confirmed=true}={}){
  const elements=new Map();
  function node(){return {style:{},children:[],textContent:'',checked:false,attributes:{},
    setAttribute(name,value){this.attributes[name]=value;},getAttribute(name){return this.attributes[name];},
    get innerHTML(){return this.textContent.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');},
    appendChild(child){this.children.push(child);},replaceChildren(){this.children=[];}};}
  const element=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  const calls=[],requests=[],timers=[],pans=[],fits=[],confirmations=[],resizes=[];
  const keyHandlers={},windowHandlers={},classes=new Set();
  let center=[33.96,-117.95],zoom=13;
  const map={on(){},setView(c,z){center=c;zoom=z;return this;},getCenter(){return center;},getZoom(){return zoom;},
    invalidateSize(options){resizes.push(options);},fitBounds(bounds){fits.push(bounds);},
    panTo(p){center=p;pans.push(p);},removeLayer(){}};
  const control={getContainer:()=>node(),addTo(){return this;},addOverlay(){},removeLayer(){}};
  const config={refresh:2,max_points:maxPoints,max_age:10,max_accuracy:100,source:'live',area:{},
    controls:{enabled:enableControls,token:'test-control-token',operation:{state:'idle'}}};
  const context={console,document:{getElementById:element,createElement:node,createElementNS:node,
    body:{classList:{toggle(name,enabled){if(enabled)classes.add(name);else classes.delete(name);}}},
    addEventListener(name,fn){keyHandlers[name]=fn;}},
    window:{addEventListener(name,fn){windowHandlers[name]=fn;}},
    requestAnimationFrame(fn){fn();},URLSearchParams,AbortSignal,
    setTimeout(fn,delay){timers.push({fn,delay});},setInterval(){},
    confirm(message){confirmations.push(message);return confirmed;},
    async fetch(url,options={}){
      calls.push(url);requests.push({url,options});
      if(url==='/api/config')return {ok:true,json:async()=>config};
      if(url==='/api/control')return {ok:true,json:async()=>({id:'op-one',action:JSON.parse(options.body).action,state:'pending'})};
      if(url.startsWith('/api/control?id=')){
        const operation=operations.shift();
        if(!operation)throw Error('No operation status queued');
        return {ok:true,json:async()=>operation};
      }
      const value=batches.shift();
      if(value instanceof Error)throw value;
      if(!value)throw Error('No test snapshot queued');
      return {ok:true,json:async()=>value};
    }};
  if(leaflet)context.L={map:()=>map,tileLayer:()=>({on(){},addTo(){}}),
    control:{layers:()=>control},latLngBounds:p=>p,
    geoJSON:()=>({addTo(){return this;},getBounds(){return 'area';}}),
    layerGroup:()=>({nodes:new Set(),addTo(){return this;},removeLayer(n){this.nodes.delete(n);}}),
    circleMarker:()=>({bindPopup(){return this;},addTo(group){group.nodes.add(this);return this;}})};
  vm.createContext(context);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/basemap.js'),'utf8').replace('__BASEMAP_SOURCES__',fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/basemaps.json'),'utf8')),context);vm.runInContext(visuals,context);vm.runInContext(source,context);
  async function flush(){for(let i=0;i<15;i++)await Promise.resolve();}
  await flush();
  return {element,calls,requests,timers,pans,fits,context,confirmations,resizes,classes,keyHandlers,windowHandlers,
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

test('fixed-site receive history works without GPS and retains zero while excluding TX',async()=>{
  const sample={id:1,session:'fixed-session',station:'fixed',direction:'uplink',received_at:'2026-11-06T00:00:00Z',
    rssi_value:0,rssi_unit:'benshi_relative_0_100',is_in_rx:true,is_in_tx:false};
  const h=await harness([batch([],{telemetry:[sample,{...sample,id:2,rssi_value:100,is_in_tx:true}]})]);
  assert.equal(h.state('points.length'),0);assert.equal(h.state('telemetry.length'),2);
  const panels=h.element('signal-charts').children;
  assert.equal(panels.length,1);assert.match(panels[0].children[1].textContent,/1 receive samples.*Median 0.0/);
  h.context.selectMeasurement('1',true);
  assert.match(h.element('measurement-selection').textContent,/Relative RSSI 0.0.*No plotted GPS/);
  assert.equal(h.pans.length,0);
});

test('telemetry is deduplicated, bounded and cleared on database reset',async()=>{
  const h=await harness([batch([1,2],{reset:true}),batch([2,3]),batch([4],{reset:true,generation:'new'})]);
  await h.tick();assert.equal(h.state('telemetry.length'),2);assert.equal(h.state('telemetry[0].id'),2);
  await h.tick();assert.equal(h.state('telemetry.length'),1);assert.equal(h.state('telemetry[0].id'),4);
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

test('read-only viewer hides app controls',async()=>{
  const h=await harness([batch([1],{reset:true})]);
  assert.equal(h.element('app-controls').hidden,true);
  assert.match(h.element('controls-note').textContent,/gps-rssi start/);
});

test('reset requests confirmation, sends capability, clears data, and resumes from a fresh cursor',async()=>{
  const h=await harness([batch([9],{reset:true,excluded:{no_gps_lock:2}}),batch([1],{reset:true,generation:'new'})],
    {enableControls:true,operations:[{id:'op-one',action:'reset',state:'completed',message:'Reset complete',backup:'data/backups/old.sqlite'}]});
  await h.element('reset-db').onclick();
  assert.match(h.confirmations[0],/ALL samples, sessions, and events/);
  const post=h.requests.find(r=>r.options.method==='POST');
  assert.equal(post.options.headers['X-GPS-RSSI-Control'],'test-control-token');
  assert.equal(JSON.parse(post.options.body).confirm,true);
  assert.equal(h.state('points.length'),0);
  assert.equal(h.state('cursor'),null);
  assert.equal(h.element('rows').children.length,0);
  assert.match(h.element('action-status').textContent,/data\/backups\/old.sqlite/);
  await h.tick();
  assert.equal(h.state('points[0].id'),1);
});

test('confirmed exit stops polling and leaves a final stopped state',async()=>{
  const h=await harness([batch([1],{reset:true})],{enableControls:true,
    operations:[{id:'op-one',action:'exit',state:'completed',message:'Recording stopped and database saved'}]});
  await h.element('exit-app').onclick();
  assert.equal(h.state('exited'),true);
  assert.equal(h.element('reset-db').disabled,true);
  assert.equal(h.element('telemetry').textContent,'Recording stopped.');
  const calls=h.calls.length;
  await h.tick();
  assert.equal(h.calls.length,calls);
  assert.equal(h.timers.length,0);
});

test('canceling confirmation sends no mutation request',async()=>{
  const h=await harness([batch([1],{reset:true})],{enableControls:true,confirmed:false});
  await h.element('reset-db').onclick();
  assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);
  assert.equal(h.state('points.length'),1);
});

test('failed reset retains points and allows subsequent updates',async()=>{
  const h=await harness([batch([1],{reset:true}),batch([2],{cursor:2})],{enableControls:true,
    operations:[{id:'op-one',action:'reset',state:'failed',message:'Backup failed; original data retained'}]});
  await h.element('reset-db').onclick();
  assert.equal(h.state('points.length'),1);
  assert.match(h.element('action-status').textContent,/original data retained/);
  assert.equal(h.element('reset-db').disabled,false);
  await h.tick();assert.equal(h.state('points.length'),2);
});

test('snapshot arriving after a reset cannot restore stale points',async()=>{
  let release;
  const stale=new Promise(resolve=>{release=resolve;});
  const h=await harness([batch([1],{reset:true}),stale],{enableControls:true,
    operations:[{id:'op-one',action:'reset',state:'completed',message:'Reset complete'}]});
  const inFlight=h.tick();
  await h.element('reset-db').onclick();
  release(batch([99],{cursor:99}));
  await inFlight;
  assert.equal(h.state('points.length'),0);
  assert.equal(h.state('cursor'),null);
});

test('maximize and restore resize the map while retaining its view and ongoing updates',async()=>{
  const h=await harness([batch([1],{reset:true}),batch([2],{cursor:2})]);
  h.state('map.setView([33.975,-117.94],16)');
  const button=h.element('maximize-map');
  button.onclick();
  assert.equal(h.classes.has('map-maximized'),true);
  assert.equal(button.textContent,'Restore map');
  assert.equal(button.getAttribute('aria-pressed'),'true');
  assert.equal(h.resizes.length,1);
  assert.equal(h.state('map.getZoom()'),16);
  assert.equal(h.state('JSON.stringify(map.getCenter())'),'[33.975,-117.94]');
  await h.tick();assert.equal(h.state('points.length'),2);
  button.onclick();
  assert.equal(h.classes.has('map-maximized'),false);
  assert.equal(button.textContent,'Maximize map');
  assert.equal(button.getAttribute('aria-pressed'),'false');
  assert.equal(h.resizes.length,2);
  assert.deepEqual(h.fits,['area']);
});

test('Escape restores the normal dashboard and window resizing refreshes Leaflet',async()=>{
  const h=await harness([batch([1],{reset:true})]);
  h.element('maximize-map').onclick();
  let prevented=false;
  h.keyHandlers.keydown({key:'Escape',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(h.classes.has('map-maximized'),false);
  h.windowHandlers.resize();
  assert.equal(h.resizes.length,3);
});

test('maximize works in the fallback viewer without Leaflet',async()=>{
  const h=await harness([batch([1],{reset:true})],{leaflet:false});
  h.element('maximize-map').onclick();
  assert.equal(h.classes.has('map-maximized'),true);
  assert.equal(h.element('rows').children.length,1);
  h.element('maximize-map').onclick();
  assert.equal(h.classes.has('map-maximized'),false);
});

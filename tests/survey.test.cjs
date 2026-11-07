const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assets=path.join(__dirname,'../src/gps_rssi/assets');

function harness({leaflet=true,large=false}={}){
  const observations=[
    {station:'mobile-a',session:'session-a',rssi:0,lat:33.96,lon:-117.95,time:'2026-11-06T17:00:00Z'},
    {station:'mobile-a',session:'session-a',rssi:100,time:'2026-11-06T17:01:00Z'},
    {station:'mobile-b',session:'session-b',rssi:50,lat:33.97,lon:-117.96,time:'2026-11-06T17:02:00Z',source:'replay'},
    {station:'fixed',session:'session-fixed',rssi:40,direction:'uplink',time:'2026-11-06T17:03:00Z'},
  ].map((r,i)=>({key:String(i),id:i,original_sample:i+1,label:'<script>unsafe</script>',rssi_unit:'benshi_relative_0_100',
    source:'benlink_bluetooth',lat:null,lon:null,location_reason:i===3?'fixed_station_no_mobile_location':i===1?'no_gps_lock':'',
    channel:1,squelch_open:false,direction:'downlink',...r}));
  if(large)observations.splice(0,4,...Array.from({length:800},(_,i)=>({...observations[1],id:i,original_sample:i+1})));
  const sessions=[...new Map(observations.map(r=>[r.session,{id:r.session,station:r.station,started_at:r.time}])).values()];
  const data={observations,summary:{sessions,station_count:sessions.length,session_count:sessions.length,source:'all',selected_samples:observations.length}};
  function node(){return {children:[],style:{},attributes:{},value:'',textContent:'',
    appendChild(n){this.children.push(n);},replaceChildren(){this.children=[];},setAttribute(k,v){this.attributes[k]=v;}};}
  const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  el('survey-data').textContent=JSON.stringify(data);
  const markers=[],layers=[],fits=[],pans=[];
  const map={on(){},setView(){return this;},removeLayer(g){g.visible=false;},fitBounds(bounds){fits.push(bounds);},panTo(p){pans.push(p);}};
  const L={control:{layers:()=>({addTo(){return this;},getContainer:node})},map:()=>map,tileLayer:()=>({addTo(){return this;},on(){return this;}}),latLngBounds:p=>p,
    layerGroup:()=>{const g={visible:true,addTo(){return this;}};layers.push(g);return g;},
    circleMarker:(latlon,style)=>{const m={latlon,style,handlers:{},bindPopup(n){this.popup=n;return this;},
      addTo(g){this.layer=g;return this;},on(k,v){this.handlers[k]=v;return this;},openPopup(){this.open=true;}};markers.push(m);return m;}};
  const context={document:{getElementById:el,createElement:node,createElementNS:node},...(leaflet?{L}:{})};
  vm.createContext(context);
  for(const file of ['basemap.js','visualization.js','survey.js'])vm.runInContext(fs.readFileSync(path.join(assets,file),'utf8').replace('__BASEMAP_SOURCES__',fs.readFileSync(path.join(assets,'basemaps.json'),'utf8')),context);
  return {el,markers,fits,pans,observations,context,get activeMarkers(){return markers.filter(m=>m.layer.visible);},
    filter(id,value){el(id).value=value;el(id).onchange();}};
}
const walk=n=>[n,...n.children.flatMap(walk)];
const plain=v=>JSON.parse(JSON.stringify(v));

test('survey retains separate receivers, zero readings, and fixed history without fabricated coordinates',()=>{
  const h=harness();assert.equal(h.el('station-count').textContent,'3');assert.equal(h.el('receive-count').textContent,'4');
  assert.equal(h.el('mapped-count').textContent,'2');assert.equal(h.el('unlocated-count').textContent,'2');
  assert.equal(h.el('signal-charts').children.length,3);assert.equal(h.el('comparison-rows').children.length,3);
  assert.equal(h.activeMarkers[0].style.fillColor,'#440154');assert.equal(h.activeMarkers[0].style.dashArray,'3 3');
  assert.notEqual(h.activeMarkers[0].style.color,h.activeMarkers[1].style.color);
  assert.match(h.el('exclusions').textContent,/fixed_station_no_mobile_location/);
});
test('transceiver, source, session, and UTC filters update charts, table, map and counts together',()=>{
  const h=harness();h.filter('station-filter','mobile-a');assert.equal(h.el('receive-count').textContent,'2');
  assert.equal(h.activeMarkers.length,1);assert.equal(h.el('signal-charts').children.length,1);
  assert.equal(h.el('observation-rows').children.length,2);assert.equal(h.fits.length,1,'filters retain map view');
  h.filter('time-from','2026-11-06T17:01:00');assert.equal(h.el('receive-count').textContent,'1');assert.equal(h.activeMarkers.length,0);
  h.el('reset-filters').onclick();h.filter('source-filter','replay');assert.equal(h.el('receive-count').textContent,'1');
  assert.match(h.activeMarkers[0].popup.textContent,/REPLAY/);
  h.el('reset-filters').onclick();h.filter('session-filter','session-fixed');assert.equal(h.el('receive-count').textContent,'1');
  assert.equal(h.el('mapped-count').textContent,'0');assert.equal(h.el('signal-charts').children.length,1);
});
test('UTC time bounds are inclusive and invalid ranges produce an understandable empty view',()=>{
  const h=harness();h.filter('time-from','2026-11-06T17:01:00');h.filter('time-to','2026-11-06T17:02:00');
  assert.equal(h.el('receive-count').textContent,'2');
  h.filter('time-to','2026-11-06T17:00:00');assert.equal(h.el('receive-count').textContent,'0');
  assert.match(h.el('comparison-rows').children[0].children[0].textContent,/No receive observations/);
});
test('comparison row focus and reset select the expected recording and map extent',()=>{
  const h=harness();h.el('comparison-rows').children[0].onclick();
  assert.equal(h.el('session-filter').value,'session-a');assert.equal(h.el('receive-count').textContent,'2');
  assert.equal(h.fits.length,2);h.el('reset-filters').onclick();assert.equal(h.el('receive-count').textContent,'4');
});
test('selecting a real timeline observation links its provenance and map marker',()=>{
  const h=harness();const dot=walk(h.el('signal-charts')).find(n=>n.attributes.role==='button');dot.onclick();
  assert.deepEqual(plain(h.pans),[[33.96,-117.95]]);assert.equal(h.activeMarkers[0].open,true);
  assert.match(h.el('measurement-selection').textContent,/original sample 1.*RSSI 0/);
  h.filter('station-filter','mobile-b');assert.match(h.el('measurement-selection').textContent,/outside the current filters/);
});
test('missing map library retains charts and filters, and stored labels only become text',()=>{
  const h=harness({leaflet:false});assert.equal(h.markers.length,0);assert.match(h.el('map-status').textContent,/Charts and tables remain/);
  h.filter('station-filter','mobile-a');assert.equal(h.el('receive-count').textContent,'2');
  assert.match(h.el('comparison-rows').children[0].children[1].textContent,/<script>unsafe<\/script>/);
  assert.equal(h.el('comparison-rows').children[0].children[1].innerHTML,undefined);
});
test('large surveys bound the observation table while statistics retain every reading',()=>{
  const h=harness({large:true});assert.equal(h.el('receive-count').textContent,'800');
  assert.equal(h.el('observation-rows').children.length,500);
  assert.match(h.el('table-note').textContent,/500 of 800/);
  assert.match(h.el('signal-charts').children[0].children[1].textContent,/800 receive samples/);
});

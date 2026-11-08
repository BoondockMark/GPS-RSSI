const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const assets=path.join(__dirname,'../src/gps_rssi/assets');
const source=fs.readFileSync(path.join(assets,'basemap.js'),'utf8').replace('__BASEMAP_SOURCES__',fs.readFileSync(path.join(assets,'basemaps.json'),'utf8'));
function harness(location){
  const note={textContent:''},calls=[],removed=[];
  const active=new Set(),events={};let choices;
  const map={removeLayer(layer){removed.push(layer);active.delete(layer);},hasLayer:layer=>active.has(layer),on(name,fn){events[name]=fn;}};
  const ctx={location,URLSearchParams,document:{getElementById:()=>note},L:{layerGroup:()=>({addTo(){active.add(this);return this;}}),control:{layers(layers){choices=layers;return {addTo(){return this;},getContainer:()=>({setAttribute(){}})};}},tileLayer(url,options){
    const layer={url,options,events:{},on(name,fn){this.events[name]=fn;return this;},addTo(){active.add(this);return this;}};
    calls.push(layer);return layer;
  }}};vm.createContext(ctx);vm.runInContext(source,ctx);
  return {context:ctx,helper:vm.runInContext('RssiBasemap',ctx),note,calls,removed,map,get choices(){return choices;},choose(name){for(const layer of active)active.delete(layer);const layer=choices[name];active.add(layer);events.baselayerchange({layer});}};
}
test('sandboxed surveys use their read-only cache capability',()=>{
  const h=harness({protocol:'http:',hostname:'127.0.0.1',pathname:'/files/job/map.html',search:'?tile_token=abc'});
  h.helper.addTo(h.map);
  assert.equal(h.calls[0].url,'/tiles/osm/{z}/{x}/{y}.png?tile_token=abc');
  assert.equal(h.calls[0].options.referrerPolicy,'origin');assert.equal(h.calls[0].options.keepBuffer,0);
  assert.match(h.note.textContent,/cached on this computer/);
});
test('live maps use the local cache; ordinary web exports use browser caching',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.helper.addTo(h.map,{cached:true});assert.equal(h.calls[0].url,'/tiles/osm/{z}/{x}/{y}.png');
  const web=harness({protocol:'https:',hostname:'example.com',pathname:'/map.html',search:''});
  web.helper.addTo(web.map);assert.match(web.calls[0].url,/^https:\/\/tile.openstreetmap.org/);
});
test('training maps can start with no active tile background',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.helper.addTo(h.map,{background:'none'});
  assert.equal(h.calls.filter(layer=>h.map.hasLayer(layer)).length,0);
  assert.match(h.note.textContent,/No background selected/);
});
test('file exports and sandboxed maps without a capability do not request tiles',()=>{
  for(const location of [{protocol:'file:'},{protocol:'http:',hostname:'localhost',pathname:'/files/job/map.html',search:''}]){
    const h=harness(location);h.helper.addTo(h.map);assert.equal(h.calls.length,0);assert.match(h.note.textContent,/app’s Activity/);
  }
});
test('a failed background is removed once and is not retried by redraws',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.helper.addTo(h.map,{cached:true});h.calls[0].events.tileerror();h.calls[0].events.tileerror();
  assert.equal(h.removed.length,1);assert.match(h.note.textContent,/could not load/);
  h.helper.addTo(h.map,{cached:true});assert.equal(h.calls.length,4);
});

test('map backgrounds switch independently and recover after a provider failure',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.helper.addTo(h.map,{cached:true});
  assert.deepEqual(Object.keys(h.choices),['Street','Topographic','Shaded relief','Satellite','No background']);
  assert.equal(h.calls.filter(l=>h.map.hasLayer(l)).length,1);
  h.choose('Shaded relief');assert.match(h.note.textContent,/Regional terrain/);
  assert.equal(h.choices['Shaded relief'].options.maxNativeZoom,13);
  h.choices.Street.events.tileerror();assert.equal(h.removed.length,0);
  h.choices['Shaded relief'].events.tileerror();assert.equal(h.removed.length,1);
  h.choose('Satellite');assert.match(h.note.textContent,/Satellite tiles/);
  assert.equal(h.choices.Satellite.url,'/tiles/satellite/{z}/{x}/{y}.png');
  h.choose('No background');assert.match(h.note.textContent,/No background selected/);
});

test('an imported regional map uses only local URLs and escapes its attribution',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  // The map configuration is inserted by the local server, not taken from a URL.
  h.context.RssiOfflineConfig={offline:true,map:{name:'Regional topo',minzoom:'0',maxzoom:'14',attribution:'<img src=x onerror=alert(1)>'}};
  h.helper.addTo(h.map,{cached:true});
  assert.equal(h.choices['Offline map'].url,'/tiles/local/{z}/{x}/{y}.png');
  assert.equal(h.choices['Offline map'].options.maxNativeZoom,14);
  assert.match(h.choices['Offline map'].options.attribution,/&lt;img/);
  assert.equal(h.map.hasLayer(h.choices['Offline map']),true);
  assert.match(h.note.textContent,/Offline mode/);
});


test('offline tile misses retain available coverage while the driver moves',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.context.RssiOfflineConfig={offline:true};h.helper.addTo(h.map,{cached:true});
  h.choices.Street.events.tileerror();
  assert.equal(h.map.hasLayer(h.choices.Street),true);
  assert.equal(h.removed.length,0);assert.match(h.note.textContent,/gaps stay blank/);
});

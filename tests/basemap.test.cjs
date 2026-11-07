const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/gps_rssi/assets/basemap.js'),'utf8');
function harness(location){
  const note={textContent:''},calls=[],removed=[];
  const map={removeLayer(layer){removed.push(layer);}};
  const ctx={location,URLSearchParams,document:{getElementById:()=>note},L:{tileLayer(url,options){
    const layer={url,options,events:{},on(name,fn){this.events[name]=fn;return this;},addTo(){return this;}};
    calls.push(layer);return layer;
  }}};vm.createContext(ctx);vm.runInContext(source,ctx);
  return {helper:vm.runInContext('RssiBasemap',ctx),note,calls,removed,map};
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
test('file exports and sandboxed maps without a capability do not request tiles',()=>{
  for(const location of [{protocol:'file:'},{protocol:'http:',hostname:'localhost',pathname:'/files/job/map.html',search:''}]){
    const h=harness(location);h.helper.addTo(h.map);assert.equal(h.calls.length,0);assert.match(h.note.textContent,/app’s Activity/);
  }
});
test('a failed background is removed once and is not retried by redraws',()=>{
  const h=harness({protocol:'http:',hostname:'localhost',pathname:'/',search:''});
  h.helper.addTo(h.map,{cached:true});h.calls[0].events.tileerror();h.calls[0].events.tileerror();
  assert.equal(h.removed.length,1);assert.match(h.note.textContent,/could not load/);
  h.helper.addTo(h.map,{cached:true});assert.equal(h.calls.length,1);
});

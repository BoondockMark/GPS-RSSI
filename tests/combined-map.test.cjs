const test=require('node:test');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const python=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');

function harness({leaflet=true,source='live',associated=true}={}){
  const rows=[
    {direction:'uplink',status:associated?'associated':'unconfirmed',lat:33.96,lon:-117.95,rssi:0},
    {direction:'downlink',status:'unconfirmed',lat:33.97,lon:-117.95,rssi:50},
    {direction:'uplink',status:'unmatched',lat:null,lon:null,rssi:40},
  ].map((r,i)=>({receiver_label:'<script>unsafe</script>',receiver_station:'station',receiver_session:'session',
    receiver_sample:i,corrected_time:'2026-11-06T17:00:00Z',gps_gap:1,rssi_unit:'benshi_relative_0_100',
    reason:'',source,squelch_open:false,...r}));
  const html=execFileSync(python,['-c',
    'import json,sys; from gps_rssi.combined_mapping import render_combined_map; d=json.loads(sys.argv[1]); print(render_combined_map(d["rows"],d["summary"]))',
    JSON.stringify({rows,summary:{source,counts:{associated:associated?1:0,unconfirmed:associated?1:2,unmatched:1}}})],
    {cwd:root,encoding:'utf8',timeout:10000,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  const sourceCode=html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
  const payload=html.match(/<script id="combined-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
  const elements=new Map([['combined-data',{textContent:payload}],['summary',{}],['error',{}]]);
  const groups=[],markers=[],fits=[];
  let overlays;
  const map={setView(){return this;},fitBounds(bounds){fits.push(bounds);}};
  const L={map:()=>map,tileLayer:()=>({addTo(){}}),
    layerGroup:()=>{const g={visible:false,addTo(){this.visible=true;return this;}};groups.push(g);return g;},
    circleMarker:(latlon,style)=>{const m={latlon,style,bindPopup(p){this.popup=p;return this;},addTo(g){this.group=g;return this;}};markers.push(m);return m;},
    control:{layers:(_,layers)=>{overlays=layers;return {addTo(){}};}},latLngBounds:p=>p};
  vm.runInNewContext(sourceCode,{document:{getElementById:id=>elements.get(id),createElement:()=>({style:{}})},...(leaflet?{L}:{})});
  return {html,groups,markers,fits,overlays,elements};
}

test('combined map separates directions and hides unconfirmed layers initially',()=>{
  const h=harness();
  assert.equal(h.groups.length,2);
  assert.equal(h.groups[0].visible,true);
  assert.equal(h.groups[1].visible,false);
  assert.equal(h.markers.length,2);
  assert.equal(h.markers[0].style.fillColor,'hsl(0,75%,42%)');
  assert.equal(h.markers[0].style.dashArray,'3 3');
  assert.deepEqual(JSON.parse(JSON.stringify(h.fits)),[[[33.96,-117.95]]]);
  assert.ok(h.overlays['Mobile → fixed | associated']);
  assert.ok(h.overlays['Fixed → mobile | unconfirmed']);
  assert.match(h.markers[0].popup.textContent,/<script>unsafe<\/script>/);
  assert.equal(h.markers[0].popup.innerHTML,undefined);
});

test('missing map library preserves static escaped results and summary',()=>{
  const h=harness({leaflet:false});
  assert.equal(h.markers.length,0);
  assert.match(h.html,/&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(h.elements.get('summary').textContent,/unmatched: 1/);
});

test('unconfirmed-only results stay hidden and do not set map bounds',()=>{
  const h=harness({associated:false});
  assert.equal(h.groups.some(g=>g.visible),false);
  assert.equal(h.fits.length,0);
  assert.match(h.elements.get('error').textContent,/No associated map points/);
});

test('replay surveys are labeled explicitly in summary and popups',()=>{
  const h=harness({source:'replay'});
  assert.match(h.elements.get('summary').textContent,/REPLAY/);
  assert.match(h.markers[0].popup.textContent,/REPLAY/);
});

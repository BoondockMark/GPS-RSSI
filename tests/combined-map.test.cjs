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
  const scripts=[...html.matchAll(/<script>\s*([\s\S]*?)<\/script>/g)].map(m=>m[1]);
  const payload=html.match(/<script id="combined-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
  function node(){return {style:{},children:[],attributes:{},value:'',textContent:'',
    setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},
    insertBefore(c){this.children.unshift(c);},appendChild(c){this.children.push(c);},replaceChildren(){this.children=[];}};}
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};
  element('combined-data').textContent=payload;
  const tableRows=rows.map((_,i)=>{const n=node();n.setAttribute('data-row',String(i));return n;});
  const groups=[],markers=[],fits=[],pans=[];
  const map={setView(){return this;},fitBounds(bounds){fits.push(bounds);},panTo(p){pans.push(p);},removeLayer(g){g.visible=false;}};
  const L={map:()=>map,tileLayer:()=>({addTo(){}}),
    layerGroup:()=>{const g={visible:false,addTo(){this.visible=true;return this;}};groups.push(g);return g;},
    circleMarker:(latlon,style)=>{const m={latlon,style,handlers:{},bindPopup(p){this.popup=p;return this;},
      on(name,fn){this.handlers[name]=fn;return this;},openPopup(){this.open=true;},addTo(g){this.group=g;return this;}};markers.push(m);return m;},
    latLngBounds:p=>p};
  const context={document:{getElementById:element,createElement:node,createElementNS:node,querySelectorAll:()=>tableRows},...(leaflet?{L}:{})};
  vm.createContext(context);for(const code of scripts)vm.runInContext(code,context);
  return {html,groups,markers,fits,pans,elements,element,tableRows,context,
    get activeMarkers(){return markers.filter(m=>m.group.visible);},
    filter(id,value){element(id).value=value;element(id).onchange();}};
}
const plain=value=>JSON.parse(JSON.stringify(value));
const walk=node=>[node,...node.children.flatMap(walk)];

test('combined map starts with associated measurements and retains zero strength',()=>{
  const h=harness();
  assert.equal(h.activeMarkers.length,1);
  assert.equal(h.activeMarkers[0].style.fillColor,'#440154');
  assert.equal(h.activeMarkers[0].style.dashArray,'3 3');
  assert.deepEqual(plain(h.fits),[[[33.96,-117.95]]]);
  assert.match(h.activeMarkers[0].popup.textContent,/<script>unsafe<\/script>/);
  assert.equal(h.activeMarkers[0].popup.innerHTML,undefined);
  assert.equal(h.element('visible-count').textContent,'1');
  assert.equal(h.tableRows[0].hidden,false);assert.equal(h.tableRows[1].hidden,true);
});
test('direction and match-quality filters update map, counts, charts and table together',()=>{
  const h=harness();h.filter('quality-filter','located');
  assert.equal(h.activeMarkers.length,2);assert.equal(h.element('unconfirmed-count').textContent,'1');
  assert.equal(h.element('signal-charts').children.length,2);
  h.filter('direction-filter','downlink');
  assert.equal(h.activeMarkers.length,1);assert.equal(h.element('visible-count').textContent,'1');
  assert.equal(h.tableRows[0].hidden,true);assert.equal(h.tableRows[1].hidden,false);
  assert.equal(h.element('signal-charts').children.length,1);
  assert.equal(h.fits.length,1,'filters must not reset the current map view');
  h.filter('direction-filter','both');h.filter('quality-filter','all');
  assert.equal(h.element('visible-count').textContent,'3');assert.equal(h.element('mapped-count').textContent,'2');
  assert.equal(h.element('unmatched-count').textContent,'1');assert.equal(h.tableRows[2].hidden,false);
});
test('timeline selection pans to a measured location and reports its match quality',()=>{
  const h=harness();const dot=walk(h.element('signal-charts')).find(n=>n.attributes.role==='button');
  dot.onclick();assert.deepEqual(plain(h.pans),[[33.96,-117.95]]);
  assert.match(h.element('measurement-selection').textContent,/Sample 0.*RSSI 0.*associated/);
  assert.equal(h.activeMarkers[0].open,true);
});
test('missing map library preserves charts, filters and escaped results',()=>{
  const h=harness({leaflet:false});assert.equal(h.markers.length,0);
  assert.match(h.html,/&lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(h.element('error').textContent,/Charts and the results table still work/);
  h.filter('quality-filter','all');assert.equal(h.element('visible-count').textContent,'3');
  assert.equal(h.element('signal-charts').children.length,2);
});
test('unconfirmed-only surveys start empty and can be inspected explicitly',()=>{
  const h=harness({associated:false});assert.equal(h.activeMarkers.length,0);assert.equal(h.fits.length,0);
  assert.match(h.element('error').textContent,/No mapped locations/);
  h.filter('quality-filter','located');assert.equal(h.activeMarkers.length,2);
});
test('replay surveys stay labeled in summary and popups',()=>{
  const h=harness({source:'replay'});assert.match(h.element('summary').textContent,/REPLAY/);
  assert.match(h.activeMarkers[0].popup.textContent,/REPLAY/);
});


test('review removes selected observations from all views and export, and undo restores them',()=>{
  const h=harness();h.element('review-mode').checked=true;
  h.activeMarkers[0].handlers.click();
  assert.equal(h.element('review-remove').disabled,false);
  h.element('review-remove').onclick();
  assert.equal(h.activeMarkers.length,0);
  assert.equal(h.element('visible-count').textContent,'0');
  assert.equal(h.tableRows[0].hidden,true);
  let csv=vm.runInContext('review.csv()',h.context);
  assert.equal(csv.trim().split('\r\n').length,3,'hidden unconfirmed and unmatched rows retained');
  assert.match(csv,/transmitter_samples/);
  h.element('review-undo').onclick();
  assert.equal(h.activeMarkers.length,1);
  assert.equal(vm.runInContext('review.csv()',h.context).trim().split('\r\n').length,4);
});
test('filters clear hidden selections and table review works without Leaflet',()=>{
  const h=harness({leaflet:false});
  h.element('review-select').onclick();
  h.filter('direction-filter','downlink');
  assert.equal(h.element('review-remove').disabled,true);
  h.filter('quality-filter','all');
  h.tableRows[1].reviewCheckbox.onchange();
  h.element('review-remove').onclick();
  assert.match(h.element('review-status').textContent,/1 removed/);
  h.element('review-undo').onclick();
  assert.equal(h.tableRows[1].hidden,false);
});

test('cleaned CSV escapes stored text and preserves zero, booleans and array metadata',()=>{
  const h=harness();
  vm.runInContext(`data.rows[0].receiver_label='=SUM(1,2)';
    data.rows[0].reason='a,"quoted"\\nreason';data.rows[0].transmitter_samples=[1,2]`,h.context);
  const csv=vm.runInContext('review.csv()',h.context);
  assert.ok(csv.includes(`"'=SUM(1,2)"`));
  assert.ok(csv.includes('a,""quoted""'));
  assert.ok(csv.includes('"[1,2]"'));
  assert.ok(csv.includes('"0"'));
  assert.ok(csv.includes('"False"'));
});

'use strict';
const data=JSON.parse(document.getElementById('survey-data').textContent);
const el=id=>document.getElementById(id);
const observations=data.observations.map((row,index)=>({...row,key:String(index)}));
const stationColors=['#075985','#c2410c','#047857','#a21caf','#9f1239','#4338ca','#854d0e','#0e7490'];
const stations=[...new Set(data.summary.sessions.map(s=>s.station))].sort();
const stationColor=station=>stationColors[stations.indexOf(station)%stationColors.length];
let map=null,layer=null,markers=new Map(),selected=null,tileFailed=false;
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;}
function option(select,value,label){const n=node('option',label);n.value=value;select.appendChild(n);}
for(const station of stations)option(el('station-filter'),station,station);
for(const session of data.summary.sessions)option(el('session-filter'),session.id,`${session.station} · ${session.id.slice(0,8)} · ${session.started_at}`);
el('station-filter').value='';el('session-filter').value='';el('source-filter').value='all';
el('survey-summary').textContent=`${data.summary.source==='replay'?'REPLAY — synthetic survey':data.summary.source==='all'?'Live and replay survey':'Live survey'} · ${data.summary.station_count} transceiver names · ${data.summary.session_count} sessions · ${data.summary.selected_samples} total selected samples. Each session retains its own identity and setup notes.`;
if(typeof L!=='undefined'){
  map=L.map('map',{preferCanvas:true}).setView([33.96,-117.95],13);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map)
    .on?.('tileerror',()=>{tileFailed=true;el('map-status').textContent='Street tiles unavailable. Measured points and charts remain available.';});
}
function visibleRows(){
  const from=el('time-from').value,to=el('time-to').value;
  const start=from?Date.parse(from+'Z'):-Infinity,end=to?Date.parse(to+'Z'):Infinity;
  return observations.filter(row=>(!el('station-filter').value||row.station===el('station-filter').value)&&
    (!el('session-filter').value||row.session===el('session-filter').value)&&
    (el('source-filter').value==='all'||(row.source==='replay')===(el('source-filter').value==='replay'))&&
    ((!from&&!to)||(Date.parse(row.time)>=start&&Date.parse(row.time)<=end)));
}
function signals(rows){return rows.filter(r=>RssiVisuals.valid(r.rssi)&&r.rssi_unit==='benshi_relative_0_100')
  .map(r=>({...r,seriesKey:r.session,seriesLabel:`${r.station} · ${r.label} · ${r.session.slice(0,8)} · ${r.source==='replay'?'REPLAY':'LIVE'}`}));}
function selectRow(key,pan=true){
  selected=String(key);const row=observations[Number(key)];if(!row)return;
  el('measurement-selection').textContent=`${row.station} · ${row.label} · session ${row.session} · original sample ${row.original_sample} · ${row.time} · RSSI ${row.rssi??'unavailable'} · ${row.source==='replay'?'REPLAY':'LIVE'} · ${row.location_reason||'Qualified mobile GPS'}`;
  if(pan&&map&&row.lat!==null&&row.lon!==null){map.panTo([row.lat,row.lon]);markers.get(selected)?.openPopup();}
  RssiVisuals.render(el('signal-charts'),signals(visibleRows()),{onSelect:selectRow,selected});
}
function fitMeasurements(){const located=visibleRows().filter(r=>r.lat!==null&&r.lon!==null);
  if(map&&located.length)map.fitBounds(L.latLngBounds(located.map(r=>[r.lat,r.lon])),{padding:[25,25],maxZoom:16});}
function renderComparison(rows){
  const body=el('comparison-rows');body.replaceChildren();const sessions=new Map();
  for(const row of rows){if(!sessions.has(row.session))sessions.set(row.session,[]);sessions.get(row.session).push(row);}
  const caption=value=>value===null?'—':value.toFixed(1);
  for(const entries of sessions.values()){
    const r=entries[0],stats=RssiVisuals.statistics(signals(entries)),tr=node('tr');
    const values=[r.station,`${r.session.slice(0,8)} · ${r.label}`,r.source==='replay'?'REPLAY':'LIVE',
      r.direction==='downlink'?'Fixed → mobile':'Mobile → fixed',entries.length,entries.filter(p=>p.lat!==null).length,
      caption(stats.median),caption(stats.p10),`${caption(stats.min)}–${caption(stats.max)}`];
    values.forEach(value=>tr.appendChild(node('td',value)));tr.children[0].style.borderLeft=`5px solid ${stationColor(r.station)}`;
    tr.setAttribute('tabindex','0');tr.setAttribute('role','button');tr.setAttribute('aria-label',`Focus ${r.station}, session ${r.session}`);
    tr.onclick=()=>{el('station-filter').value=r.station;el('session-filter').value=r.session;renderView();fitMeasurements();};
    tr.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();tr.onclick();}};body.appendChild(tr);
  }
  if(!rows.length){const tr=node('tr'),td=node('td','No receive observations match these filters.');td.setAttribute('colspan','9');tr.appendChild(td);body.appendChild(tr);}
}
function renderView(){
  const rows=visibleRows(),located=rows.filter(r=>r.lat!==null&&r.lon!==null);
  if(map){if(layer)map.removeLayer(layer);layer=L.layerGroup().addTo(map);}markers.clear();
  if(map)for(const r of located){
    const popup=node('div');popup.style.whiteSpace='pre-line';
    popup.textContent=`${r.station} · ${r.label} · ${r.source==='replay'?'REPLAY':'LIVE'}\nSession ${r.session}\nOriginal sample ${r.original_sample} · ${r.time}\nRelative RSSI ${r.rssi} /100 · Channel ${r.channel}`;
    const marker=L.circleMarker([r.lat,r.lon],{radius:r.source==='replay'?7:5,color:stationColor(r.station),weight:2,
      fillColor:RssiVisuals.color(r.rssi),fillOpacity:.85,dashArray:r.squelch_open?null:'3 3'}).bindPopup(popup).addTo(layer);
    marker.on?.('click',()=>selectRow(r.key,false));markers.set(r.key,marker);
  }
  el('station-count').textContent=String(new Set(rows.map(r=>r.station)).size);
  el('receive-count').textContent=String(rows.length);el('mapped-count').textContent=String(located.length);
  el('unlocated-count').textContent=String(rows.length-located.length);
  el('map-status').textContent=!map?'Map unavailable. Charts and tables remain available.':tileFailed?
    'Street tiles unavailable. Measured points and charts remain available.':!located.length?
    'No qualified mobile GPS points in this view. Inspect GPS exclusions or change filters.':'Measured locations only. Street tiles require Internet access.';
  if(selected!==null&&!rows.some(r=>r.key===selected)){selected=null;el('measurement-selection').textContent='The selected observation is outside the current filters.';}
  renderComparison(rows);RssiVisuals.render(el('signal-charts'),signals(rows),{onSelect:selectRow,selected});
  const body=el('observation-rows');body.replaceChildren();
  for(const r of rows.slice(-500)){
    const tr=node('tr');for(const value of [r.station,r.session,r.original_sample,r.time,r.source,r.rssi??'unavailable',r.lat??'',r.lon??'',r.location_reason||'Qualified GPS'])tr.appendChild(node('td',value));
    body.appendChild(tr);
  }
  const reasons=new Map();for(const r of rows)if(r.location_reason)reasons.set(r.location_reason,(reasons.get(r.location_reason)||0)+1);
  el('exclusions').textContent='Receive observations without map locations: '+([...reasons].map(([reason,count])=>`${reason}: ${count}`).join('; ')||'none');
  el('table-note').textContent=`Showing the last ${Math.min(rows.length,500)} of ${rows.length} filtered receive observations. Statistics use all filtered observations. Full selected data is in measurements.csv.`;
}
for(const id of ['station-filter','session-filter','source-filter','time-from','time-to'])el(id).onchange=()=>{
  if(id==='station-filter')el('session-filter').value='';renderView();};
el('fit-measurements').onclick=fitMeasurements;
el('reset-filters').onclick=()=>{for(const id of ['station-filter','session-filter','time-from','time-to'])el(id).value='';el('source-filter').value='all';renderView();fitMeasurements();};
renderView();fitMeasurements();

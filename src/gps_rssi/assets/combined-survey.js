'use strict';
const data=JSON.parse(document.getElementById('combined-data').textContent);
const el=id=>document.getElementById(id);
let map=null,groups=[],markers=new Map(),selected=null;
el('summary').textContent=(data.summary.source==='replay'?'REPLAY — synthetic input | ':'LIVE input | ')+
 (Object.entries(data.summary.counts).map(([k,v])=>`${k}: ${v}`).join(' | ')||'No receive observations.');
const review=SurveyReview.create(data.rows,{fields:data.fields,filename:'reviewed-measurements.csv',onChange:renderView});
el('direction-filter').value='both';el('quality-filter').value='associated';
if(typeof L!=='undefined'){
  map=L.map('map',{preferCanvas:true}).setView([33.96,-117.95],13);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
}
function visibleRows(){
  return data.rows.map((row,index)=>({...row,key:String(index)})).filter(row=>
    !review.removed.has(row.key)&&
    (el('direction-filter').value==='both'||row.direction===el('direction-filter').value)&&
    (el('quality-filter').value==='all'||row.status==='associated'||
      (el('quality-filter').value==='located'&&row.status==='unconfirmed')));
}
function signals(rows){
  return rows.filter(r=>r.rssi_unit==='benshi_relative_0_100'&&RssiVisuals.valid(r.rssi))
    .map(r=>({...r,time:r.corrected_time,seriesKey:r.receiver_session,
      seriesLabel:r.receiver_station+' · '+r.receiver_label+' · session '+r.receiver_session.slice(0,8)}));
}
function selectRow(key,pan=true){
  selected=String(key);
  const row=data.rows[Number(key)];
  if(!row)return;
  el('measurement-selection').textContent=`${row.direction==='uplink'?'Mobile → fixed':'Fixed → mobile'} | ${row.receiver_label} | Sample ${row.receiver_sample} | ${row.corrected_time||row.original_time} | RSSI ${row.rssi??'unavailable'} | ${row.status}${row.reason?' | '+row.reason:''} | ${row.gps_gap===null?'No GPS match':'GPS gap '+row.gps_gap.toFixed(2)+'s'}`;
  if(pan&&map&&row.lat!==null&&row.lon!==null){map.panTo([row.lat,row.lon]);markers.get(selected)?.openPopup();}
  RssiVisuals.render(el('signal-charts'),signals(visibleRows()),{onSelect:selectRow,selected});
}
function fitMeasurements(){
  const locations=visibleRows().filter(r=>r.lat!==null&&r.lon!==null).map(r=>[r.lat,r.lon]);
  if(map&&locations.length)map.fitBounds(L.latLngBounds(locations),{padding:[25,25],maxZoom:16});
}
function renderView(){
  const rows=visibleRows();review.setVisible(rows.map(r=>r.key));
  const located=rows.filter(r=>r.lat!==null&&r.lon!==null);
  for(const group of groups)map?.removeLayer(group);
  groups=[];markers.clear();
  const layers=new Map();
  if(map)for(const row of located){
    const label=row.direction+'|'+row.status;
    if(!layers.has(label)){const layer=L.layerGroup().addTo(map);layers.set(label,layer);groups.push(layer);}
    const popup=document.createElement('div');popup.style.whiteSpace='pre-line';
    popup.textContent=`${row.direction==='uplink'?'Mobile → fixed':'Fixed → mobile'} | ${row.status} | ${row.source==='replay'?'REPLAY':'LIVE'}\n${row.receiver_station} | ${row.receiver_label}\nSample ${row.receiver_sample} | ${row.corrected_time}\nRelative RSSI ${row.rssi} | GPS gap ${row.gps_gap.toFixed(2)}s\n${row.reason}`;
    const marker=L.circleMarker([row.lat,row.lon],{radius:review.selected.has(row.key)?9:6,weight:review.selected.has(row.key)?4:2,color:review.selected.has(row.key)?'#e11d48':row.status==='associated'?'#243746':'#7e22ce',
      fillColor:RssiVisuals.color(row.rssi),fillOpacity:.8,dashArray:row.squelch_open?null:'3 3'})
      .bindPopup(popup).addTo(layers.get(label));
    marker.on?.('click',()=>{review.mapClick(row.key);selectRow(row.key,false);});markers.set(row.key,marker);
  }
  el('visible-count').textContent=String(rows.length);el('mapped-count').textContent=String(located.length);
  el('unconfirmed-count').textContent=String(rows.filter(r=>r.status==='unconfirmed').length);
  el('unmatched-count').textContent=String(rows.filter(r=>r.status==='unmatched').length);
  const shown=new Set(rows.map(r=>r.key));
  for(const tr of document.querySelectorAll('tr[data-row]'))tr.hidden=!shown.has(tr.getAttribute('data-row'));
  if(selected!==null&&!shown.has(selected)){selected=null;el('measurement-selection').textContent='The selected observation is outside the current filters.';}
  RssiVisuals.render(el('signal-charts'),signals(rows),{onSelect:selectRow,selected});
  el('error').textContent=!map?'Map library unavailable. Charts and the results table still work.':
    !located.length?'No mapped locations in this view. Inspect match-quality filters and the results table.':'';
}
el('direction-filter').onchange=renderView;el('quality-filter').onchange=renderView;
el('fit-measurements').onclick=fitMeasurements;
renderView();fitMeasurements();

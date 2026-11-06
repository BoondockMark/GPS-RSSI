'use strict';
const el=id=>document.getElementById(id);
let config,map,areaLayer,control,paused=false,cursor=null,generation=null,latest=null;
let points=[],markers=new Map(),groups=new Map(),exclusions={},processed=0;
function popup(p){
  const node=document.createElement('div');node.style.whiteSpace='pre-line';
  node.textContent=`${p.station} | ${p.source}\n${p.time}\nSession ${p.session}\nRelative RSSI ${p.rssi.toFixed(1)} / 100\nChannel ${p.channel} | Squelch ${p.squelch_open?'open':'closed'}`;
  return node;
}
function renderTable(){
  el('rows').replaceChildren();
  for(const p of points.slice(-500).reverse()){
    const row=document.createElement('tr');
    for(const v of [p.id,p.station,p.source,p.time,p.lat,p.lon,p.rssi,p.squelch_open?'open':'closed']){
      const cell=document.createElement('td');cell.textContent=String(v);row.appendChild(cell);
    }el('rows').appendChild(row);
  }
}
function clearPoints(){
  for(const entry of groups.values()){map?.removeLayer(entry.layer);control?.removeLayer(entry.layer);}
  points=[];markers.clear();groups.clear();exclusions={};processed=0;latest=null;
}
function addPoints(incoming){
  for(const p of incoming){
    if(markers.has(p.id))continue;
    let group=null,marker=null;
    if(map){
      const key=p.session;
      if(!groups.has(key)){
        const label=document.createElement('span');label.textContent=`${p.source==='replay'?'REPLAY':'LIVE'} | ${p.station} | ${p.session}`;
        const layer=L.layerGroup().addTo(map);groups.set(key,{layer,count:0});control.addOverlay(layer,label.innerHTML);
      }
      group=groups.get(key);group.count++;
      marker=L.circleMarker([p.lat,p.lon],{radius:p.source==='replay'?7:5,
        color:p.source==='replay'?'#7e22ce':'#243746',weight:p.source==='replay'?3:1,
        fillColor:`hsl(${p.rssi*1.2},75%,42%)`,fillOpacity:.8,dashArray:p.squelch_open?null:'3 3'});
      marker.bindPopup(popup(p)).addTo(group.layer);
    }
    points.push(p);markers.set(p.id,{marker,group});
  }
  while(points.length>config.max_points){
    const old=points.shift(),entry=markers.get(old.id);markers.delete(old.id);
    if(entry.group){
      entry.group.layer.removeLayer(entry.marker);entry.group.count--;
      if(!entry.group.count){map.removeLayer(entry.group.layer);control.removeLayer(entry.group.layer);groups.delete(old.session);}
    }
  }
  if(map&&incoming.length&&el('follow').checked){const p=incoming[incoming.length-1];map.panTo([p.lat,p.lon]);}
}
function updateAge(){
  if(!latest){el('telemetry').textContent='No telemetry in the loaded samples.';el('radio').textContent='';return;}
  const age=Math.max(0,(Date.now()-Date.parse(latest.received_at))/1000);
  el('telemetry').textContent=`${age<=15?'Recent telemetry':'No recent telemetry'} | last sample ${Number.isFinite(age)?Math.round(age)+'s ago':latest.received_at}`;
  el('radio').textContent=`RSSI ${latest.rssi_value.toFixed(1)} (${latest.rssi_unit}) | ${latest.is_in_tx?'TX':latest.is_in_rx?'RX':'idle'} | GPS lock ${Boolean(latest.gps_locked)}`;
}
async function readJSON(url){
  const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(10000)});
  if(!response.ok)throw new Error(`Dashboard request failed (${response.status}). Retrying…`);
  return response.json();
}
async function poll(){
  let soon=false;
  try{
    if(!paused){
      const query=new URLSearchParams();
      if(cursor!==null){query.set('after',cursor);query.set('generation',generation);}
      const data=await readJSON('/api/snapshot?'+query);
      if(data.reset)clearPoints();
      cursor=data.cursor;generation=data.generation;processed+=data.processed;
      for(const [reason,count] of Object.entries(data.excluded))exclusions[reason]=(exclusions[reason]||0)+count;
      addPoints(data.points);if(data.latest)latest=data.latest;
      if(data.link_event){
        let detail={};try{detail=JSON.parse(data.link_event.detail_json)||{};}catch(error){/* older log */}
        const message=detail.error?` | ${detail.stage||''} ${detail.error}`:'';
        el('link').textContent=`Last recorded link event: ${data.link_event.kind} at ${data.link_event.received_at}${message}`;
      }
      else el('link').textContent='No link events in selected sessions.';
      const replay=points.filter(p=>p.source==='replay').length;
      el('summary').textContent=`Showing ${points.length} points (${points.length-replay} live, ${replay} replay), keeping at most ${config.max_points}. Processed ${processed} samples since initial window. Source: ${config.source}.`;
      el('excluded').textContent='Excluded from loaded samples: '+(Object.entries(exclusions).map(([k,v])=>`${k}: ${v}`).join('; ')||'none');
      el('error').textContent=map?'':'Map library unavailable; the measurement table still updates.';
      renderTable();soon=data.more;
    }
  }catch(error){el('error').textContent=error.message+' The last displayed data is retained.';}
  finally{updateAge();setTimeout(poll,soon?50:config.refresh*1000);}
}
async function start(){
  try{
    config=await readJSON('/api/config');
    if(typeof L!=='undefined'){
      map=L.map('map').setView([33.96,-117.95],13);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
        maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map);
      control=L.control.layers(null,null,{collapsed:false}).addTo(map);
      if(config.area){
        areaLayer=L.geoJSON(config.area,{style:f=>({color:f.properties.kind==='city'?'#6d28d9':'#0369a1',
          weight:2,fillOpacity:.035,dashArray:f.properties.kind==='buffer'?'6 5':null}),
          onEachFeature:(f,layer)=>{const text=document.createElement('span');text.textContent=f.properties.name;layer.bindPopup(text);}
        }).addTo(map);
        control.addOverlay(areaLayer,'City boundary + 0.5-mile planning buffer');map.fitBounds(areaLayer.getBounds());
      }
    }
    el('area-note').textContent=config.area?
      'La Habra Heights + 0.5-mile outward buffer (804.672 m). Generalized Census 2022 boundary; planning outline, not a surveyed or legal boundary.':
      'No planning overlay selected. Initial view is the La Habra Heights region.';
    el('quality').textContent=`GPS limits: fix/sample time difference ≤ ${config.max_age}s; reported accuracy ≤ ${config.max_accuracy}m. Initial window: newest ${config.max_points} selected samples. Older measurements remain in SQLite.`;
    el('pause').onclick=()=>{paused=!paused;el('pause').textContent=paused?'Resume updates':'Pause updates';};
    el('fit-area').disabled=!areaLayer;
    el('fit-area').onclick=()=>{if(areaLayer)map.fitBounds(areaLayer.getBounds());};
    el('fit-points').onclick=()=>{if(map&&points.length)map.fitBounds(L.latLngBounds(points.map(p=>[p.lat,p.lon])),{maxZoom:16,padding:[25,25]});};
    setInterval(updateAge,1000);poll();
  }catch(error){el('error').textContent=error.message+' Reload this page after the server is available.';}
}
start();

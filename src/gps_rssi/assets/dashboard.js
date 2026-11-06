'use strict';
const el=id=>document.getElementById(id);
let config,map,areaLayer,control,paused=false,cursor=null,generation=null,latest=null;
let points=[],markers=new Map(),groups=new Map(),exclusions={},processed=0;
let actionBusy=false,exited=false,snapshotEpoch=0;
let mapMaximized=false;
function resizeMap(view=null){
  if(!map)return;
  const center=view?view.center:map.getCenter(),zoom=view?view.zoom:map.getZoom();
  requestAnimationFrame(()=>{map.invalidateSize({pan:false});map.setView(center,zoom,{animate:false});});
}
function maximizeMap(expanded){
  const view=map?{center:map.getCenter(),zoom:map.getZoom()}:null;
  mapMaximized=expanded;
  document.body.classList.toggle('map-maximized',expanded);
  const button=el('maximize-map');
  button.textContent=expanded?'Restore map':'Maximize map';
  button.setAttribute('aria-pressed',String(expanded));
  button.title=expanded?'Restore the normal dashboard (Esc)':'Expand the map to fill this window';
  resizeMap(view);
}
function setupMapLayout(){
  el('maximize-map').onclick=()=>maximizeMap(!mapMaximized);
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&mapMaximized){event.preventDefault();maximizeMap(false);}
  });
  window.addEventListener('resize',()=>resizeMap());
  // The bottom bar can wrap or change height as controls become available.
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>resizeMap()).observe(el('map-toolbar'));
}
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
  if(exited)return;
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
  if(exited)return;
  const epoch=snapshotEpoch;
  let soon=false;
  try{
    if(!paused&&!actionBusy){
      const query=new URLSearchParams();
      if(cursor!==null){query.set('after',cursor);query.set('generation',generation);}
      const data=await readJSON('/api/snapshot?'+query);
      if(epoch!==snapshotEpoch||actionBusy||exited)return;
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
  }catch(error){if(epoch===snapshotEpoch&&!exited)el('error').textContent=error.message+' The last displayed data is retained.';}
  finally{updateAge();if(!exited)setTimeout(poll,soon?50:config.refresh*1000);}
}
function controlButtons(disabled){
  el('reset-db').disabled=disabled;el('exit-app').disabled=disabled;el('pause').disabled=disabled;
}
async function monitorOperation(operation){
  for(let attempts=0;attempts<240;attempts++){
    const status=await readJSON('/api/control?id='+encodeURIComponent(operation.id));
    el('action-status').textContent=status.message||status.state;
    if(status.state==='failed')throw new Error(status.message);
    if(status.state==='completed'){
      if(status.action==='exit'){
        exited=true;el('telemetry').textContent='Recording stopped.';el('radio').textContent='';
        el('link').textContent='The app is closing. Close this tab and relaunch when ready.';el('error').textContent='';
      }else{
        snapshotEpoch++;cursor=null;generation=null;clearPoints();renderTable();
        el('summary').textContent='Fresh recording session; waiting for new measurements.';
        el('link').textContent='Recording is restarting.';
        el('excluded').textContent='Excluded from loaded samples: none';el('error').textContent='';updateAge();
        if(status.backup)el('action-status').textContent+=' Backup: '+status.backup;
      }
      return;
    }
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error('The operation is still pending. Check the console or reload to see its status.');
}
async function performControl(action,existing=null){
  if(actionBusy||exited)return;
  if(!existing&&!confirm(action==='reset'?
    'Reset ALL samples, sessions, and events in this database (including other stations and replay data)?\nDatabase: '+
    (config.controls.database||'active recording database')+'\nA timestamped backup will be saved first. Recording will pause and restart. Settings will be kept.':
    'Stop recording, save the database, disconnect the radio, and exit the app?'))return;
  actionBusy=true;snapshotEpoch++;controlButtons(true);
  el('action-status').textContent=action==='reset'?'Requesting database reset…':'Requesting clean exit…';
  try{
    let operation=existing;
    if(!operation){
      const response=await fetch('/api/control',{method:'POST',
        headers:{'Content-Type':'application/json','X-GPS-RSSI-Control':config.controls.token},
        body:JSON.stringify({action,confirm:true}),signal:AbortSignal.timeout(10000)});
      operation=await response.json();
      if(!response.ok)throw new Error(operation.error||'Control request failed');
    }
    await monitorOperation(operation);
  }catch(error){el('action-status').textContent=error.message+' Check the console before retrying if the server is unavailable.';}
  finally{actionBusy=false;if(!exited)controlButtons(false);}
}
async function start(){
  try{
    config=await readJSON('/api/config');
    const enabled=Boolean(config.controls?.enabled);
    el('app-controls').hidden=!enabled;
    el('controls-note').textContent=enabled?
      'Reset backs up and clears this database, then resumes recording. Exit stops the logger and dashboard.':
      'Reset and Exit controls are available when launched with gps-rssi start.';
    el('reset-db').onclick=()=>performControl('reset');
    el('exit-app').onclick=()=>performControl('exit');
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
    const operation=config.controls?.operation;
    if(enabled&&operation&&(['pending','running'].includes(operation.state)||
      (operation.state==='completed'&&operation.action==='exit')))performControl(operation.action,operation);
  }catch(error){el('error').textContent=error.message+' Reload this page after the server is available.';}
}
setupMapLayout();
start();

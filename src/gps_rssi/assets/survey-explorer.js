'use strict';
globalThis.SurveyExplorer=(()=>{
  const el=id=>document.getElementById(id);
  const node=(tag,text,attrs={})=>{
    const n=['svg','line','circle','text','polygon','polyline'].includes(tag)?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
    if(text!==null&&text!==undefined)n.textContent=text;
    for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));
    return n;
  };
  function chart(container,points,{title,value=p=>p.rssi,maxDistance,selected,onSelect,line=false,limits=[0,100],antenna=false,units='relative signal'}={}){
    container.replaceChildren();
    if(!points.length){container.appendChild(node('p','No located measurements in this view.'));return;}
    const sampled=antenna?points:points.length>600?RssiVisuals.timelinePoints(points,600):points;
    const end=Math.max(1,maxDistance??points.reduce((n,p)=>Math.max(n,p.distance),0)),[low,high]=limits;
    const x=p=>48+564*p.distance/end,y=v=>147-116*(v-low)/(high-low||1);
    const svg=node('svg',null,{viewBox:'0 0 640 190',role:'group','aria-label':title});
    if(antenna&&sampled.every(p=>Number.isFinite(p.fresnelLower)&&Number.isFinite(p.fresnelUpper))){
      const edge=[...sampled.map(p=>`${x(p)},${y(p.fresnelUpper)}`),...sampled.slice().reverse().map(p=>`${x(p)},${y(p.fresnelLower)}`)];
      svg.appendChild(node('polygon',null,{points:edge.join(' '),fill:'#f59e0b','fill-opacity':.18,stroke:'#b45309','stroke-width':1,'aria-label':'60% first Fresnel zone envelope'}));
    }
    for(const level of [low,(low+high)/2,high]){
      svg.appendChild(node('line',null,{x1:48,x2:612,y1:y(level),y2:y(level),stroke:'#cbd5e1'}));
      svg.appendChild(node('text',level.toFixed(0),{x:3,y:y(level)+4,'font-size':12,fill:'#475569'}));
    }
    if(antenna){
      // Preserve every queried ridge without thousands of individual SVG lines.
      svg.appendChild(node('polyline',null,{points:sampled.map(p=>`${x(p)},${y(value(p))}`).join(' '),fill:'none',stroke:'#64748b','stroke-width':2,'aria-label':'Curvature-adjusted ground profile'}));
      svg.appendChild(node('line',null,{x1:x(sampled[0]),x2:x(sampled.at(-1)),y1:y(sampled[0].line),y2:y(sampled.at(-1).line),stroke:'#dc2626','stroke-width':2,'stroke-dasharray':'5 3'}));
    }
    let previous=null;
    for(const p of antenna?[]:sampled){
      const v=value(p);
      if(!Number.isFinite(v)){previous=null;continue;}
      if(line&&previous&&previous.part===p.part){
        svg.appendChild(node('line',null,{x1:x(previous),x2:x(p),y1:y(value(previous)),y2:y(v),stroke:antenna?'#64748b':RssiVisuals.color(Math.min(previous.rssi??50,p.rssi??50)),'stroke-width':2}));
      }
      const chosen=selected!==null&&selected!==undefined&&p.key===selected;
      const dot=node('circle',null,{cx:x(p),cy:y(v),r:antenna?1.5:chosen?6:3.5,fill:antenna?'#64748b':RssiVisuals.color(p.rssi),
        stroke:chosen?'#e11d48':'#172b3a','stroke-width':chosen?2:.5,
        ...(onSelect?{tabindex:0,role:'button','aria-label':`${(p.distance/1000).toFixed(3)} km, ${v.toFixed(1)} ${units}; ${p.time||''}; select measurement`}:{})});
      if(onSelect){dot.onclick=()=>onSelect(p.key);dot.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(p.key);}};}
      svg.appendChild(dot);previous=p;
    }
    svg.appendChild(node('text','0 km',{x:48,y:174,'font-size':12}));
    svg.appendChild(node('text',`${(end/1000).toFixed(3)} km`,{x:612,y:174,'text-anchor':'end','font-size':12}));
    const fig=node('figure',null,{class:'profile-chart'});fig.appendChild(svg);
    fig.appendChild(node('figcaption',`${title} · ${sampled.length} of ${points.length} points shown.`));container.appendChild(fig);
  }
  function elevationLimits(points,antenna=false){
    const values=points.flatMap(p=>antenna?[p.terrain,p.line,p.fresnelLower,p.fresnelUpper]:[p.elevation]).filter(Number.isFinite);
    if(!values.length)return [0,100];
    const min=Math.min(...values),max=Math.max(...values),pad=Math.max(5,(max-min)*.1);
    return [min-pad,max+pad];
  }
  function provenance(samples){
    const times=samples.map(p=>p.fetched_at).filter(Number.isFinite);
    if(!times.length)return '';
    const start=new Date(Math.min(...times)*1000).toISOString().slice(0,10),end=new Date(Math.max(...times)*1000).toISOString().slice(0,10);
    return `${samples.some(p=>p.local)?'Terrain imported / elevations retrieved':'Elevation queries retrieved'} ${start}${end===start?'':' to '+end} UTC; terrain acquisition dates may differ.`;
  }
  function create({rows,map,onSelect,summary={}}){
    let visible=rows,selected=null,routeLayer=null,terrainLayer=null,viewRoutes=[],comparison=null;
    let matchCache=null;
    const routeElevations=new Map(),pathResults=new Map();let task=null;
    const config=JSON.parse(el('terrain-config').textContent),endpoint=config.terrainEndpoint;
    const demo=summary.demo?.kind==='gps-rssi-training-v1'&&RouteAnalysis.located(summary.demo.station)&&rows.length>0&&rows.every(r=>r.source==='replay');
    const terrainSource=demo?'Synthetic training terrain':'USGS EPQS / 3DEP';
    function sourceLabel(samples){
      if(demo)return terrainSource;
      const local=samples.filter(p=>p.local).length;
      return local===samples.length&&local?'Local terrain GeoTIFF':local?'Local terrain GeoTIFF + USGS / saved points':terrainSource;
    }
    let demoMode='ridge';
    const label=r=>`${r.station} · ${r.session.slice(0,8)} · ${r.label||'No setup label'} · ${r.source==='replay'?'REPLAY':'LIVE'}`;
    const sessions=[...new Map(rows.map(r=>[r.session,r])).values()];
    for(const id of ['compare-a','compare-b','terrain-session'])for(const row of sessions){
      const option=node('option',label(row));option.value=row.session;el(id).appendChild(option);
    }
    el('compare-a').value=sessions[0]?.session||'';el('compare-b').value=sessions[1]?.session||sessions[0]?.session||'';
    el('terrain-session').value=sessions.find(r=>RouteAnalysis.located(r))?.session||sessions[0]?.session||'';
    el('terrain-availability').textContent=demo?'Offline synthetic terrain is active. No USGS requests or elevation-cache writes occur.':endpoint?'Elevations load only when requested below. Imported local terrain is used first; outside its coverage, saved or online USGS points apply according to Offline mode.':'Open this survey from the app’s Activity to load elevations. Route and comparison views work without the elevation service.';
    function buttons(){
      el('load-route-elevation').disabled=(!endpoint&&!demo)||!!task;
      el('load-path-elevation').disabled=(!endpoint&&!demo)||!!task||!RouteAnalysis.located(rows.find(r=>r.key===selected)||{});
      el('cancel-elevation').disabled=!task;
    }
    function cancel(message){if(task){const old=task;task=null;old.controller.abort();if(message)el('terrain-status').textContent=message;}buttons();}
    async function run(kind,job){
      cancel();const current={kind,controller:new AbortController()};task=current;buttons();
      el('terrain-status').textContent=demo?'Evaluating synthetic terrain…':'Loading ground elevations…';
      try{await job(current.controller.signal,()=>task===current);}
      catch(error){if(task===current)el('terrain-status').textContent=error.name==='AbortError'?'Elevation request canceled.':error.message;}
      finally{if(task===current){task=null;buttons();}}
    }
    async function elevations(points,signal){
      if(demo){
        if(signal.aborted)throw new DOMException('Canceled','AbortError');
        return TrainingTerrain.samples(points,demoMode,summary.demo.station);
      }
      const samples=[];
      for(let i=0;i<points.length;i+=16){
        const response=await fetch(endpoint,{method:'POST',credentials:'omit',headers:{'Content-Type':'text/plain'},
          body:JSON.stringify({points:points.slice(i,i+16).map(p=>({lat:p.lat,lon:p.lon})),refresh:el('refresh-elevation').checked}),signal});
        const result=await response.json();
        if(!response.ok)throw Error(result.error||'Elevation request failed.');
        if(!Array.isArray(result.samples)||result.samples.length!==Math.min(16,points.length-i))throw Error('Elevation service returned an incomplete batch.');
        samples.push(...result.samples);
        el('terrain-status').textContent=`Loaded ${samples.length} of ${points.length} elevation samples…`;
      }
      return samples;
    }
    function sourceDetails(prefix,samples){
      const body=el(prefix+'-source-rows');body.replaceChildren();
      if(!samples.length){el(prefix+'-source-note').textContent='No elevation queries for this view.';return;}
      const groups=new Map(),known=samples.map(p=>p.metadata?.resolution_m).filter(v=>Number.isFinite(v)&&v>0);
      const legacy=samples.filter(p=>p.legacy_metadata).length;
      const range=values=>{const valid=values.filter(v=>Number.isFinite(v)&&v>0);if(!valid.length)return 'unknown';const a=Math.min(...valid),b=Math.max(...valid);return a.toFixed(2)+(Math.abs(b-a)>.005?'–'+b.toFixed(2):'');};
      for(const p of samples){
        const m=p.metadata||{},key=JSON.stringify([m.source_name,m.raster_id,m.resolution_raw,m.resolution_units,m.acquisition_date,m.resolution_basis,p.legacy_metadata]);
        if(!groups.has(key))groups.set(key,{metadata:m,points:[]});groups.get(key).points.push(p);
      }
      for(const {metadata:m,points} of [...groups.values()].slice(0,50)){
        const row=node('tr',null),dates=[...new Set(points.filter(p=>Number.isFinite(p.fetched_at)).map(p=>new Date(p.fetched_at*1000).toISOString()))].sort();
        for(const text of [`${m.source_name||m.source||terrainSource} / ${m.raster_id??'raster unknown'} · ${m.resolution_basis||'source details unavailable'}`,
          demo?'not applicable':m.resolution_raw==null?'unknown':`${m.resolution_raw} (${m.resolution_units||'native units not reported'})`,
          demo?'not applicable':`${range(points.map(p=>p.metadata?.cell_size_x_m))} × ${range(points.map(p=>p.metadata?.cell_size_y_m))}`,
          m.acquisition_date??(demo?'not applicable':'unknown'),dates.length?dates[0]+(dates.length>1?' to '+dates.at(-1):''):'not applicable',points.length])row.appendChild(node('td',text));
        body.appendChild(row);
      }
      el(prefix+'-source-note').textContent=demo?'Synthetic analytic fixture: no geographic DEM resolution or acquisition date applies.':`${samples.length} queried locations · verified finer cell dimension: ${range(known)} m · ${samples.length-known.length} locations have unverified cell size. ${legacy?legacy+' legacy cache entries lack metadata; use the refresh checkbox to query it. ':''}${samples.some(p=>p.stale)?'Some cached elevations are older than 30 days. ':''}Cell size is horizontal resolution, not vertical accuracy. Acquisition dates are reported by the service; timestamps are local import times or query/cache retrieval times. ${groups.size>50?'Showing 50 of '+groups.size+' source groups.':''}`;
    }
    async function adaptivePath(station,row,signal){
      const total=RouteAnalysis.distance(station,row),maxSpacing=Number(el('path-spacing').value),maxSamples=Number(el('path-budget').value);
      const initial=RouteAnalysis.path(station,row,Math.min(17,Math.max(3,Math.ceil(total/maxSpacing)+1)));
      const first=await elevations(initial,signal);
      let nodes=initial.map((point,i)=>({...first[i],point,fraction:i/(initial.length-1)}));
      let plan=RouteAnalysis.samplingPlan(total,nodes,{maxSpacing,maxSamples});
      while(plan.next.length&&nodes.every(n=>Number.isFinite(n.elevation))){
        if(signal.aborted)throw new DOMException('Canceled','AbortError');
        const requested=plan.next.map(n=>({...n,point:RouteAnalysis.path(nodes[n.left].point,nodes[n.right].point,3)[1]}));
        const samples=await elevations(requested.map(n=>n.point),signal);
        nodes.push(...requested.map((n,i)=>({...samples[i],point:n.point,fraction:n.fraction})));
        nodes.sort((a,b)=>a.fraction-b.fraction);
        plan=RouteAnalysis.samplingPlan(total,nodes,{maxSpacing,maxSamples});
        el('terrain-status').textContent=`Refining path: ${nodes.length} of up to ${maxSamples} queries · largest current gap ${plan.maxGap.toFixed(2)} m.`;
      }
      const missing=nodes.some(n=>!Number.isFinite(n.elevation));
      const note=`${nodes.length} ground samples · actual spacing ${plan.minGap.toFixed(2)}–${plan.maxGap.toFixed(2)} m · target spacing ${plan.targetMin.toFixed(2)}–${plan.targetMax.toFixed(2)} m. ${missing?'Refinement stopped because elevations are missing.':plan.capped?'SAMPLING LIMITED: query budget reached before the spacing targets.':plan.finerThanFloor?'SAMPLING LIMITED: some source pixels are finer than the 0.5 m request target floor.':'Spacing targets met at queried sources.'} ${demo?'Analytic fixture: no native geographic grid exists.':plan.unknownResolution?plan.unknownResolution+' source resolutions are unverified; desired spacing was used there.':''}`;
      return {points:nodes.map(n=>n.point),samples:nodes,sampling:{...plan,next:undefined,missing,note,limited:missing||plan.incomplete}};
    }
    function paintRoutes(target,group,routes){
      for(const route of routes)for(const [a,b] of route.segments){
        if(!RssiVisuals.valid(a.rssi)||!RssiVisuals.valid(b.rssi))continue;
        L.polyline([[a.lat,a.lon],[b.lat,b.lon]],{color:RssiVisuals.color(Math.min(a.rssi,b.rssi)),weight:4,opacity:.8}).addTo(group).bringToBack().on('click',()=>onSelect(b.key));
      }
    }
    function paintPoints(group,points,focus=selected){
      for(const p of points){
        const marker=L.circleMarker([p.lat,p.lon],{radius:p.key===focus?8:5,color:p.key===focus?'#e11d48':'#172b3a',weight:2,fillColor:RssiVisuals.color(p.rssi),fillOpacity:.9}).addTo(group);
        const popup=node('div',`${label(p)} · ${p.time} · RSSI ${p.rssi??'unavailable'}`);
        marker.bindPopup(popup).on('click',()=>onSelect(p.key));
      }
    }
    function drawRoutes(){
      if(!map)return;
      if(routeLayer)map.removeLayer(routeLayer);routeLayer=L.layerGroup().addTo(map);
      if(el('show-routes').checked)paintRoutes(map,routeLayer,viewRoutes);
    }
    function distanceCharts(){
      const container=el('route-charts');container.replaceChildren();
      for(const route of viewRoutes.filter(r=>r.points.length)){
        const panel=node('section',null);panel.appendChild(node('h3',label(route.points[0])));
        const div=node('div',null);chart(div,route.points,{title:'Relative signal /100 vs observed route distance (gaps excluded)',selected,onSelect,line:true});panel.appendChild(div);container.appendChild(panel);
      }
      if(!container.children.length)container.appendChild(node('p','No qualified route locations in this view.'));
    }
    function compare(){
      if(!comparison)return;
      const routes=['compare-a','compare-b'].map(id=>viewRoutes.find(r=>r.session===el(id).value));
      el('compare-note').textContent=el('compare-a').value===el('compare-b').value?'Both panes show the same session. Choose a different recording for a comparison.':'Distance axes start independently. Use the table below for pairs matched by proximity.';
      const matches=locationMatches();
      const pair=matches.pairs.find(p=>p.a.key===selected||p.b.key===selected);
      const maximum=Math.max(1,...routes.map(r=>r?.distance||0));
      routes.forEach((route,i)=>{
        const id=i?'compare-b':'compare-a',pane=comparison[i];
        el(id+'-note').textContent=route?.points.length?`${route.points.length} located measurements · ${(route.distance/1000).toFixed(3)} km of connected segments`:'No located measurements under the current survey filters.';
        const focus=pair?(i?pair.b.key:pair.a.key):selected;
        chart(el(id+'-chart'),route?.points||[],{title:'Relative signal /100 vs observed route distance',maxDistance:maximum,selected:focus,onSelect,line:true});
        if(pane.map){if(pane.layer)pane.map.removeLayer(pane.layer);pane.layer=L.layerGroup().addTo(pane.map);
          if(route){paintRoutes(pane.map,pane.layer,[route]);paintPoints(pane.layer,route.points,focus);}}
      });
      matchedTable(matches);
    }
    function locationMatches(){
      const a=el('compare-a').value,b=el('compare-b').value,radius=Number(el('match-radius').value),sameFrequency=el('match-frequency').checked;
      if(matchCache&&matchCache.visible===visible&&matchCache.a===a&&matchCache.b===b&&matchCache.radius===radius&&matchCache.sameFrequency===sameFrequency)return matchCache.result;
      let result;
      try{result=RouteAnalysis.matchLocations(visible.filter(r=>r.session===a),visible.filter(r=>r.session===b),{radius,sameFrequency});}
      catch(error){result={pairs:[],error:error.message};}
      matchCache={visible,a,b,radius,sameFrequency,result};return result;
    }
    function matchedTable(result){
      const body=el('matched-rows');body.replaceChildren();
      el('match-summary').textContent=result.error||`${result.pairs.length} one-to-one pairs within ${el('match-radius').value} m · ${result.eligibleA} eligible A readings, ${result.eligibleB} eligible B readings. ${el('match-frequency').checked?'Unknown frequencies and frequency mismatches are excluded.':'Frequency constraint is off: pairs may have different or unknown frequencies.'}${result.truncated?' Matching is limited to the first 5,000 eligible readings per session; use survey filters to narrow the selection.':''}`;
      const frequency=p=>Number.isFinite(p.frequency_hz)&&p.frequency_hz>0?(p.frequency_hz/1e6).toFixed(6):'unknown';
      for(const [index,pair] of result.pairs.slice(0,200).entries()){
        const tr=node('tr',null,{'data-pair-a':pair.a.key,'data-pair-b':pair.b.key});
        if(pair.a.key===selected||pair.b.key===selected)tr.className='pair-selected';
        const cell=node('td',null),button=node('button',`Inspect pair ${index+1}`,{type:'button'});
        button.onclick=()=>{onSelect(pair.a.key);comparison[0].map?.panTo([pair.a.lat,pair.a.lon],{animate:false});};cell.appendChild(button);tr.appendChild(cell);
        for(const value of [`${pair.a.original_sample} / ${pair.a.time}`,`${pair.b.original_sample} / ${pair.b.time}`,pair.separation.toFixed(1),pair.a.rssi.toFixed(1),pair.b.rssi.toFixed(1),`${pair.delta>0?'+':''}${pair.delta.toFixed(1)}`,`${frequency(pair.a)} / ${frequency(pair.b)}`])tr.appendChild(node('td',value));
        body.appendChild(tr);
      }
    }
    function showComparison(){
      el('comparison-panes').hidden=false;el('show-comparison').hidden=true;
      el('location-comparison').hidden=false;
      comparison=[0,1].map(i=>({map:typeof L==='undefined'?null:L.map(i?'compare-b-map':'compare-a-map',{preferCanvas:true,zoomAnimation:false,fadeAnimation:false}).setView(map?map.getCenter():[33.96,-117.95],map?map.getZoom():13),layer:null}));
      if(comparison[0].map){
        let syncing=false;
        comparison.forEach((pane,i)=>{
          RssiBasemap.addTo(pane.map,{background:demo?'none':'osm'});
          pane.map.on('moveend',()=>{if(syncing)return;syncing=true;try{comparison[1-i].map.setView(pane.map.getCenter(),pane.map.getZoom(),{animate:false});}finally{syncing=false;}});
        });
      }
      compare();
    }
    function terrainRoute(){
      const route=viewRoutes.find(r=>r.session===el('terrain-session').value),container=el('terrain-route-charts');container.replaceChildren();
      const saved=routeElevations.get(el('terrain-session').value);
      if(!route||!saved){el('terrain-route-note').textContent='Choose a route and load its elevations; at most 96 located measurements are sampled.';sourceDetails('route',[]);return;}
      const byKey=new Map(saved.map(p=>[p.key,p])),points=route.points.filter(p=>byKey.has(p.key)).map(p=>({...p,...byKey.get(p.key),distance:p.distance,part:p.part}));
      const signal=node('div',null),ground=node('div',null);
      chart(signal,route.points,{title:'Relative signal /100 vs observed route distance',maxDistance:route.distance,selected,onSelect,line:true});
      chart(ground,points,{title:`${demo?'Synthetic':'Terrain'} ground elevation (m) vs the same observed distance`,value:p=>p.elevation,maxDistance:route.distance,selected,onSelect,line:true,limits:elevationLimits(points),units:'metres'});
      container.appendChild(signal);container.appendChild(ground);
      const known=points.filter(p=>Number.isFinite(p.elevation)).length;
      sourceDetails('route',points);
      el('terrain-route-note').textContent=`${sourceLabel(points)} · ${known} of ${points.length} sampled locations have elevation data. ${demo?'Fictitious ground for UI testing; not geographic terrain. No service cache is used.':'Ground elevations are model samples, not GPS altitude. Missing elevations break the profile. Imported terrain remains available until removed; point caches are retained for 30 days. '+provenance(points)}`;
    }
    function drawTerrain(){
      if(!map)return;
      if(terrainLayer)map.removeLayer(terrainLayer);terrainLayer=L.layerGroup().addTo(map);
      for(const row of visible){
        const result=pathResults.get(row.key);if(!result||!RouteAnalysis.located(row))continue;
        L.circleMarker([row.lat,row.lon],{radius:12,color:result.status==='obstructed'?'#dc2626':result.fresnelStatus==='intruded'?'#b45309':result.sampling?.limited?'#64748b':result.fresnelStatus==='sampled-clear'?'#15803d':'#64748b',weight:3,fill:false}).addTo(terrainLayer).on('click',()=>onSelect(row.key));
      }
      const result=pathResults.get(selected);
      if(result?.samples.length)L.polyline(result.samples.map(p=>[p.lat,p.lon]),{color:'#dc2626',weight:2,dashArray:'6 5'}).addTo(terrainLayer);
    }
    function pathProfile(){
      const result=pathResults.get(selected),container=el('path-profile');container.replaceChildren();
      sourceDetails('path',result?.sourceSamples||[]);
      el('path-sampling-note').textContent=result?.sampling?.note||'No path sampling has run.';
      if(!result)return;
      if(result.status==='unknown'){container.appendChild(node('p','Missing ground elevations prevent a terrain cross-section for this path.'));el('terrain-status').textContent=result.message;return;}
      chart(container,result.samples,{title:'Path distance (km): ground (gray), antenna line (red dashed), 60% Fresnel envelope (amber when frequency known), metres',value:p=>p.terrain,maxDistance:result.total,antenna:true,line:true,limits:elevationLimits(result.samples,true)});
      el('terrain-status').textContent=result.message;
    }
    function select(key){
      const changed=selected!==key;
      if(task?.kind==='path'&&selected!==key)cancel('Selected measurement changed; the previous path request was canceled.');
      selected=key;
      if(changed&&!pathResults.has(key)&&!task)el('terrain-status').textContent='No path inspection for this measurement. Enter the station settings and inspect the selected path.';
      const row=rows.find(r=>r.key===key);
      frequencyNote();
      el('terrain-selected').textContent=row&&RouteAnalysis.located(row)?`${label(row)} · sample ${row.original_sample} · ${row.time} · ${row.lat.toFixed(6)}, ${row.lon.toFixed(6)} · RSSI ${row.rssi??'unavailable'} /100`:'Select a located measurement on the map or a chart.';
      buttons();distanceCharts();compare();terrainRoute();pathProfile();drawTerrain();
      if(changed&&comparison?.[0].map&&row&&RouteAnalysis.located(row))comparison[0].map.panTo([row.lat,row.lon],{animate:false});
    }
    el('show-routes').onchange=drawRoutes;
    el('show-comparison').onclick=showComparison;
    el('compare-a').onchange=compare;el('compare-b').onchange=compare;
    el('match-radius').onchange=compare;el('match-frequency').onchange=compare;
    el('terrain-session').onchange=()=>{cancel('Route changed.');terrainRoute();};
    el('cancel-elevation').onclick=()=>cancel('Elevation request canceled.');
    el('load-route-elevation').onclick=()=>run('route',async(signal,current)=>{
      const route=viewRoutes.find(r=>r.session===el('terrain-session').value);
      if(!route?.points.length)throw Error('Choose a session with located measurements under the current filters.');
      const sampled=RouteAnalysis.sample(route.points),samples=await elevations(sampled,signal);
      if(!current())return;
      routeElevations.set(route.session,sampled.map((p,i)=>({...p,...samples[i],lat:p.lat,lon:p.lon,key:p.key,elevation:samples[i].elevation??null})));
      terrainRoute();el('terrain-status').textContent=demo?'Synthetic route elevations loaded. Select a reading or try a preset.':'Route ground elevations loaded. Select a measurement to inspect its radio path.';
    });
    function clearPaths(){cancel();pathResults.clear();el('terrain-status').textContent='Path settings changed; previous path results have been cleared.';el('path-profile').replaceChildren();el('path-sampling-note').textContent='No path sampling has run.';sourceDetails('path',[]);drawTerrain();}
    function pathFrequency(){
      if(el('path-frequency-source').value==='manual')return el('path-frequency').value.trim()===''?null:Number(el('path-frequency').value)*1e6;
      return rows.find(r=>r.key===selected)?.frequency_hz??null;
    }
    function frequencyNote(){
      const frequency=pathFrequency();
      el('path-frequency-note').textContent=Number.isFinite(frequency)&&frequency>0?`${el('path-frequency-source').value==='manual'?'Entered':'Recorded receive'} frequency: ${(frequency/1e6).toFixed(6)} MHz. The model checks a 60% first Fresnel radius.`:el('path-frequency-source').value==='manual'?'Enter a valid frequency, or select recorded frequency to run a line-only check when the recording has none.':'No valid frequency: line-of-sight inspection remains available, but Fresnel clearance will be unknown. Enter a frequency to include it.';
    }
    el('path-frequency-source').onchange=()=>{const manual=el('path-frequency-source').value==='manual';el('path-frequency').disabled=!manual;el('path-frequency').required=manual;clearPaths();frequencyNote();};
    el('path-frequency').addEventListener('input',()=>{clearPaths();frequencyNote();});
    for(const id of ['fixed-lat','fixed-lon','fixed-height','mobile-height','path-spacing'])el(id).addEventListener('input',clearPaths);
    el('path-budget').onchange=clearPaths;el('refresh-elevation').onchange=clearPaths;
    // Reports intentionally lack allow-forms in their sandbox. Validate locally
    // and use the read-only elevation capability instead of submitting the form.
    el('path-form').onsubmit=event=>event.preventDefault();
    el('path-form').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();el('load-path-elevation').click();}});
    el('load-path-elevation').onclick=()=>{
      if(!el('path-form').reportValidity())return;
      run('path',async(signal,current)=>{
        const row=rows.find(r=>r.key===selected);
        if(!row||!RouteAnalysis.located(row))throw Error('Select a located receive measurement first.');
        const read=id=>el(id).value.trim()===''?NaN:Number(el(id).value);
        const station={lat:read('fixed-lat'),lon:read('fixed-lon')},fixedHeight=read('fixed-height'),mobileHeight=read('mobile-height');
        const frequencyHz=pathFrequency(),frequencySource=el('path-frequency-source').value;
        if(!RouteAnalysis.located(station)||!Number.isFinite(fixedHeight)||fixedHeight<0||fixedHeight>500||!Number.isFinite(mobileHeight)||mobileHeight<0||mobileHeight>100)throw Error('Enter valid station coordinates and both antenna heights above ground.');
        const length=RouteAnalysis.distance(station,row);
        if(length<1||length>200000)throw Error('Choose a path between 1 metre and 200 km long.');
        const {points,samples,sampling}=await adaptivePath(station,row,signal);
        if(!current())return;
        const result=RouteAnalysis.clearance(points,samples.map(p=>p.elevation??null),fixedHeight,mobileHeight,{frequencyHz});
        result.sampling=sampling;result.sourceSamples=samples;
        result.message=result.status==='unknown'?'Unknown path clearance: one or more ground elevations are unavailable. No clear-path conclusion is possible.':
          `${result.status==='obstructed'?'Sampled terrain crosses the estimated antenna line.':'No terrain obstruction was found on the antenna line at sampled locations.'}\nMinimum sampled line clearance: ${result.worst.clearance.toFixed(1)} m at ${(result.worst.distance/1000).toFixed(3)} km · maximum ground sample spacing: ${result.spacing.toFixed(2)} m · path: ${(result.total/1000).toFixed(3)} km.\n${result.fresnelStatus==='unknown'?'Fresnel clearance unknown: no valid frequency.':`${result.fresnelStatus==='intruded'?'Sampled terrain intrudes into the 60% first Fresnel envelope.':'60% first Fresnel envelope clears sampled terrain.'} Minimum sampled envelope margin: ${result.fresnelWorst.fresnelClearance.toFixed(1)} m; ${frequencySource==='manual'?'entered':'recorded receive'} frequency ${(result.frequencyHz/1e6).toFixed(6)} MHz.`}\n${sourceLabel(samples)}; Earth curvature included. Buildings, vegetation and refraction are omitted. These sampled checks do not establish coverage.`;
        if(sampling.limited)result.message+='\nSAMPLING LIMITED — read the spacing status before interpreting a clear sampled profile.';
        result.message+='\n'+(demo?'SYNTHETIC TRAINING — not actual elevation or measured coverage.':provenance(samples));
        pathResults.set(row.key,result);pathProfile();drawTerrain();
      });
    };
    function update(filtered,key){
      cancel('View filters changed; the previous elevation request was canceled.');visible=filtered;viewRoutes=RouteAnalysis.routes(filtered);
      drawRoutes();select(key);
    }
    if(demo){
      el('refresh-elevation').disabled=true;
      el('training-panel').hidden=false;el('survey-title').textContent='Synthetic survey playground';
      el('survey-summary').textContent='SYNTHETIC TRAINING · '+el('survey-summary').textContent;
      el('terrain-intro').textContent='These ground elevations are fictitious local fixtures, evaluated entirely in your browser. Use the training presets or edit antenna settings to investigate the interface. No coordinates are sent to USGS and nothing is written to the service elevation cache.';
      el('load-route-elevation').textContent='Load synthetic route elevations';el('load-path-elevation').textContent='Inspect synthetic path';
      el('terrain-status').textContent='Synthetic ridge terrain is selected. Choose a training preset to run a check.';
      const cases=[['Sampled clear','flat',60,false,'green: both sampled checks clear'],['Fresnel intrusion','flat',5,false,'amber: line clear, Fresnel envelope intruded'],['Blocked line','ridge',60,false,'red: ridge crosses the antenna line'],['Missing elevation','missing',60,false,'gray: unknown terrain clearance'],['Missing frequency','flat',60,true,'gray: Fresnel clearance unknown']];
      for(const [name,mode,height,missingFrequency,expected] of cases){
        const button=node('button',name,{type:'button'});
        button.onclick=()=>{
          const candidates=visible.filter(r=>RouteAnalysis.located(r)&&(missingFrequency?!(Number.isFinite(r.frequency_hz)&&r.frequency_hz>0):Number.isFinite(r.frequency_hz)&&r.frequency_hz>0));
          if(!candidates.length){el('training-case-note').textContent='No suitable reading under these filters. Reset filters to include both replay runs.';return;}
          const row=candidates.reduce((a,b)=>RouteAnalysis.distance(summary.demo.station,a)>RouteAnalysis.distance(summary.demo.station,b)?a:b);
          demoMode=mode;clearPaths();routeElevations.clear();terrainRoute();
          el('fixed-lat').value=summary.demo.station.lat;el('fixed-lon').value=summary.demo.station.lon;
          el('fixed-height').value=height;el('mobile-height').value=height;
          el('path-spacing').value='10';el('path-budget').value='1025';
          el('path-frequency-source').value='recorded';el('path-frequency').value='';el('path-frequency').disabled=true;el('path-frequency').required=false;
          onSelect(row.key);frequencyNote();
          el('training-case-note').textContent=`${name} · ${mode} synthetic ground · both antenna heights ${height} m · expected ${expected}. Inputs remain editable.`;
          el('load-path-elevation').click();
        };
        el('training-cases').appendChild(button);
      }
    }
    buttons();
    return {update,select,isDemo:demo};
  }
  return {create};
})();

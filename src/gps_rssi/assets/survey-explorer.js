'use strict';
globalThis.SurveyExplorer=(()=>{
  const el=id=>document.getElementById(id);
  const node=(tag,text,attrs={})=>{
    const n=['svg','line','circle','text'].includes(tag)?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
    if(text!==null&&text!==undefined)n.textContent=text;
    for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));
    return n;
  };
  function chart(container,points,{title,value=p=>p.rssi,maxDistance,selected,onSelect,line=false,limits=[0,100],antenna=false,units='relative signal'}={}){
    container.replaceChildren();
    if(!points.length){container.appendChild(node('p','No located measurements in this view.'));return;}
    const sampled=points.length>600?RssiVisuals.timelinePoints(points,600):points;
    const end=Math.max(1,maxDistance??points.reduce((n,p)=>Math.max(n,p.distance),0)),[low,high]=limits;
    const x=p=>48+564*p.distance/end,y=v=>147-116*(v-low)/(high-low||1);
    const svg=node('svg',null,{viewBox:'0 0 640 190',role:'group','aria-label':title});
    for(const level of [low,(low+high)/2,high]){
      svg.appendChild(node('line',null,{x1:48,x2:612,y1:y(level),y2:y(level),stroke:'#cbd5e1'}));
      svg.appendChild(node('text',level.toFixed(0),{x:3,y:y(level)+4,'font-size':12,fill:'#475569'}));
    }
    let previous=null;
    for(const p of sampled){
      const v=value(p);
      if(!Number.isFinite(v)){previous=null;continue;}
      if(line&&previous&&previous.part===p.part){
        svg.appendChild(node('line',null,{x1:x(previous),x2:x(p),y1:y(value(previous)),y2:y(v),stroke:antenna?'#64748b':RssiVisuals.color(Math.min(previous.rssi??50,p.rssi??50)),'stroke-width':2}));
      }
      if(antenna&&previous){
        svg.appendChild(node('line',null,{x1:x(previous),x2:x(p),y1:y(previous.line),y2:y(p.line),stroke:'#dc2626','stroke-width':2,'stroke-dasharray':'5 3'}));
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
    const values=points.flatMap(p=>antenna?[p.terrain,p.line]:[p.elevation]).filter(Number.isFinite);
    if(!values.length)return [0,100];
    const min=Math.min(...values),max=Math.max(...values),pad=Math.max(5,(max-min)*.1);
    return [min-pad,max+pad];
  }
  function provenance(samples){
    const times=samples.map(p=>p.fetched_at).filter(Number.isFinite);
    if(!times.length)return '';
    const start=new Date(Math.min(...times)*1000).toISOString().slice(0,10),end=new Date(Math.max(...times)*1000).toISOString().slice(0,10);
    return `Elevation queries retrieved ${start}${end===start?'':' to '+end} UTC; terrain acquisition dates may differ.`;
  }
  function create({rows,map,onSelect}){
    let visible=rows,selected=null,routeLayer=null,terrainLayer=null,viewRoutes=[],comparison=null;
    const routeElevations=new Map(),pathResults=new Map();let task=null;
    const config=JSON.parse(el('terrain-config').textContent),endpoint=config.terrainEndpoint;
    const label=r=>`${r.station} · ${r.session.slice(0,8)} · ${r.label||'No setup label'} · ${r.source==='replay'?'REPLAY':'LIVE'}`;
    const sessions=[...new Map(rows.map(r=>[r.session,r])).values()];
    for(const id of ['compare-a','compare-b','terrain-session'])for(const row of sessions){
      const option=node('option',label(row));option.value=row.session;el(id).appendChild(option);
    }
    el('compare-a').value=sessions[0]?.session||'';el('compare-b').value=sessions[1]?.session||sessions[0]?.session||'';
    el('terrain-session').value=sessions.find(r=>RouteAnalysis.located(r))?.session||sessions[0]?.session||'';
    el('terrain-availability').textContent=endpoint?'Elevations load only when requested below. USGS data availability and resolution vary by location.':'Open this survey from the app’s Activity to load elevations. Route and comparison views work without the elevation service.';
    function buttons(){
      el('load-route-elevation').disabled=!endpoint||!!task;
      el('load-path-elevation').disabled=!endpoint||!!task||!RouteAnalysis.located(rows.find(r=>r.key===selected)||{});
      el('cancel-elevation').disabled=!task;
    }
    function cancel(message){if(task){const old=task;task=null;old.controller.abort();if(message)el('terrain-status').textContent=message;}buttons();}
    async function run(kind,job){
      cancel();const current={kind,controller:new AbortController()};task=current;buttons();
      el('terrain-status').textContent='Loading ground elevations from USGS…';
      try{await job(current.controller.signal,()=>task===current);}
      catch(error){if(task===current)el('terrain-status').textContent=error.name==='AbortError'?'Elevation request canceled.':error.message;}
      finally{if(task===current){task=null;buttons();}}
    }
    async function elevations(points,signal){
      const samples=[];
      for(let i=0;i<points.length;i+=16){
        const response=await fetch(endpoint,{method:'POST',credentials:'omit',headers:{'Content-Type':'text/plain'},
          body:JSON.stringify({points:points.slice(i,i+16).map(p=>({lat:p.lat,lon:p.lon}))}),signal});
        const result=await response.json();
        if(!response.ok)throw Error(result.error||'Elevation request failed.');
        if(!Array.isArray(result.samples)||result.samples.length!==Math.min(16,points.length-i))throw Error('Elevation service returned an incomplete batch.');
        samples.push(...result.samples);
        el('terrain-status').textContent=`Loaded ${samples.length} of ${points.length} elevation samples…`;
      }
      return samples;
    }
    function paintRoutes(target,group,routes){
      for(const route of routes)for(const [a,b] of route.segments){
        if(!RssiVisuals.valid(a.rssi)||!RssiVisuals.valid(b.rssi))continue;
        L.polyline([[a.lat,a.lon],[b.lat,b.lon]],{color:RssiVisuals.color(Math.min(a.rssi,b.rssi)),weight:4,opacity:.8}).addTo(group).bringToBack().on('click',()=>onSelect(b.key));
      }
    }
    function paintPoints(group,points){
      for(const p of points){
        const marker=L.circleMarker([p.lat,p.lon],{radius:p.key===selected?8:5,color:p.key===selected?'#e11d48':'#172b3a',weight:2,fillColor:RssiVisuals.color(p.rssi),fillOpacity:.9}).addTo(group);
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
      el('compare-note').textContent=el('compare-a').value===el('compare-b').value?'Both panes show the same session. Choose a different recording for a comparison.':'Distance axes share a scale; the two recordings are not automatically matched by location.';
      const maximum=Math.max(1,...routes.map(r=>r?.distance||0));
      routes.forEach((route,i)=>{
        const id=i?'compare-b':'compare-a',pane=comparison[i];
        el(id+'-note').textContent=route?.points.length?`${route.points.length} located measurements · ${(route.distance/1000).toFixed(3)} km of connected segments`:'No located measurements under the current survey filters.';
        chart(el(id+'-chart'),route?.points||[],{title:'Relative signal /100 vs observed route distance',maxDistance:maximum,selected,onSelect,line:true});
        if(pane.map){if(pane.layer)pane.map.removeLayer(pane.layer);pane.layer=L.layerGroup().addTo(pane.map);
          if(route){paintRoutes(pane.map,pane.layer,[route]);paintPoints(pane.layer,route.points);}}
      });
    }
    function showComparison(){
      el('comparison-panes').hidden=false;el('show-comparison').hidden=true;
      comparison=[0,1].map(i=>({map:typeof L==='undefined'?null:L.map(i?'compare-b-map':'compare-a-map',{preferCanvas:true,zoomAnimation:false,fadeAnimation:false}).setView(map?map.getCenter():[33.96,-117.95],map?map.getZoom():13),layer:null}));
      if(comparison[0].map){
        let syncing=false;
        comparison.forEach((pane,i)=>{
          RssiBasemap.addTo(pane.map);
          pane.map.on('moveend',()=>{if(syncing)return;syncing=true;try{comparison[1-i].map.setView(pane.map.getCenter(),pane.map.getZoom(),{animate:false});}finally{syncing=false;}});
        });
      }
      compare();
    }
    function terrainRoute(){
      const route=viewRoutes.find(r=>r.session===el('terrain-session').value),container=el('terrain-route-charts');container.replaceChildren();
      const saved=routeElevations.get(el('terrain-session').value);
      if(!route||!saved){el('terrain-route-note').textContent='Choose a route and load its elevations; at most 96 located measurements are sampled.';return;}
      const byKey=new Map(saved.map(p=>[p.key,p])),points=route.points.filter(p=>byKey.has(p.key)).map(p=>({...p,...byKey.get(p.key),distance:p.distance,part:p.part}));
      const signal=node('div',null),ground=node('div',null);
      chart(signal,route.points,{title:'Relative signal /100 vs observed route distance',maxDistance:route.distance,selected,onSelect,line:true});
      chart(ground,points,{title:'USGS ground elevation (m) vs the same observed distance',value:p=>p.elevation,maxDistance:route.distance,selected,onSelect,line:true,limits:elevationLimits(points),units:'metres'});
      container.appendChild(signal);container.appendChild(ground);
      const known=points.filter(p=>Number.isFinite(p.elevation)).length;
      el('terrain-route-note').textContent=`USGS EPQS / 3DEP · ${known} of ${points.length} sampled locations have elevation data. Ground elevations are model samples, not GPS altitude. Missing elevations break the profile. Cached queries are retained for 30 days. ${provenance(points)}`;
    }
    function drawTerrain(){
      if(!map)return;
      if(terrainLayer)map.removeLayer(terrainLayer);terrainLayer=L.layerGroup().addTo(map);
      for(const row of visible){
        const result=pathResults.get(row.key);if(!result||!RouteAnalysis.located(row))continue;
        L.circleMarker([row.lat,row.lon],{radius:12,color:result.status==='obstructed'?'#dc2626':result.status==='sampled-clear'?'#15803d':'#64748b',weight:3,fill:false}).addTo(terrainLayer).on('click',()=>onSelect(row.key));
      }
      const result=pathResults.get(selected);
      if(result?.samples.length)L.polyline(result.samples.map(p=>[p.lat,p.lon]),{color:'#dc2626',weight:2,dashArray:'6 5'}).addTo(terrainLayer);
    }
    function pathProfile(){
      const result=pathResults.get(selected),container=el('path-profile');container.replaceChildren();
      if(!result)return;
      if(result.status==='unknown'){container.appendChild(node('p','Missing ground elevations prevent a terrain cross-section for this path.'));el('terrain-status').textContent=result.message;return;}
      chart(container,result.samples,{title:'Path distance (km): curvature-adjusted ground (solid) and antenna line (red dashed), metres',value:p=>p.terrain,maxDistance:result.total,antenna:true,line:true,limits:elevationLimits(result.samples,true)});
      el('terrain-status').textContent=result.message;
    }
    function select(key){
      const changed=selected!==key;
      if(task?.kind==='path'&&selected!==key)cancel('Selected measurement changed; the previous path request was canceled.');
      selected=key;
      if(changed&&!pathResults.has(key)&&!task)el('terrain-status').textContent='No path inspection for this measurement. Enter the station settings and inspect the selected path.';
      const row=rows.find(r=>r.key===key);
      el('terrain-selected').textContent=row&&RouteAnalysis.located(row)?`${label(row)} · sample ${row.original_sample} · ${row.time} · ${row.lat.toFixed(6)}, ${row.lon.toFixed(6)} · RSSI ${row.rssi??'unavailable'} /100`:'Select a located measurement on the map or a chart.';
      buttons();distanceCharts();compare();terrainRoute();pathProfile();drawTerrain();
    }
    el('show-routes').onchange=drawRoutes;
    el('show-comparison').onclick=showComparison;
    el('compare-a').onchange=compare;el('compare-b').onchange=compare;
    el('terrain-session').onchange=()=>{cancel('Route changed.');terrainRoute();};
    el('cancel-elevation').onclick=()=>cancel('Elevation request canceled.');
    el('load-route-elevation').onclick=()=>run('route',async(signal,current)=>{
      const route=viewRoutes.find(r=>r.session===el('terrain-session').value);
      if(!route?.points.length)throw Error('Choose a session with located measurements under the current filters.');
      const sampled=RouteAnalysis.sample(route.points),samples=await elevations(sampled,signal);
      if(!current())return;
      routeElevations.set(route.session,sampled.map((p,i)=>({...p,elevation:samples[i].elevation??null,fetched_at:samples[i].fetched_at})));
      terrainRoute();el('terrain-status').textContent='Route ground elevations loaded. Select a measurement to inspect its radio path.';
    });
    function clearPaths(){cancel();pathResults.clear();el('terrain-status').textContent='Station settings changed; previous path results have been cleared.';el('path-profile').replaceChildren();drawTerrain();}
    for(const id of ['fixed-lat','fixed-lon','fixed-height','mobile-height'])el(id).addEventListener('input',clearPaths);
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
        if(!RouteAnalysis.located(station)||!Number.isFinite(fixedHeight)||fixedHeight<0||fixedHeight>500||!Number.isFinite(mobileHeight)||mobileHeight<0||mobileHeight>100)throw Error('Enter valid station coordinates and both antenna heights above ground.');
        const length=RouteAnalysis.distance(station,row);
        if(length<1||length>200000)throw Error('Choose a path between 1 metre and 200 km long.');
        const points=RouteAnalysis.path(station,row),samples=await elevations(points,signal);
        if(!current())return;
        const result=RouteAnalysis.clearance(points,samples.map(p=>p.elevation??null),fixedHeight,mobileHeight);
        result.message=result.status==='unknown'?'Unknown path clearance: one or more ground elevations are unavailable. No clear-path conclusion is possible.':
          `${result.status==='obstructed'?'Sampled terrain crosses the estimated antenna line.':'No terrain obstruction was found at the sampled locations; this does not establish a clear radio path.'}\nMinimum sampled clearance: ${result.worst.clearance.toFixed(1)} m · ground sample spacing: ${result.spacing.toFixed(1)} m · path: ${(result.total/1000).toFixed(3)} km.\nUSGS EPQS / 3DEP; Earth curvature included. Buildings, vegetation, refraction and Fresnel clearance are not included.`;
        result.message+='\n'+provenance(samples);
        pathResults.set(row.key,result);pathProfile();drawTerrain();
      });
    };
    function update(filtered,key){
      cancel('View filters changed; the previous elevation request was canceled.');visible=filtered;viewRoutes=RouteAnalysis.routes(filtered);
      drawRoutes();select(key);
    }
    buttons();
    return {update,select};
  }
  return {create};
})();

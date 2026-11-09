/* Read-only Leaflet rendering; managed operation controls remain in Field sweeps. */
globalThis.UnifiedLiveMap=(()=>{
  function create({container,cards,statusModel,onField}){
    let map=null,layer=null,stamp='',data=null,lastFit=false,fitting=false,scope=null;
    const entries=new Map(),fixMarkers=new Map();
    const el=(tag,text)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;return element;};
    function ensureMap(offline){
      const next=JSON.stringify(offline);
      if(map&&next===stamp)return;
      const center=map?.getCenter(),zoom=map?.getZoom();if(map)map.remove();
      globalThis.RssiOfflineConfig=offline;stamp=next;
      if(typeof L==='undefined'){container.textContent='Map library unavailable. Radio status and saved data remain available.';return;}
      map=L.map(container,{preferCanvas:true,zoomAnimation:false,fadeAnimation:false}).setView(center||[33.96,-117.95],zoom||12);
      RssiBasemap.addTo(map,{cached:true});layer=L.layerGroup().addTo(map);
      map.on('dragstart',()=>{document.getElementById('survey-map-auto-fit').checked=false;});
      map.on('zoomstart',()=>{if(!fitting)document.getElementById('survey-map-auto-fit').checked=false;});
    }
    function fit(){if(!map||!data)return;const positions=data.radios.flatMap(row=>row.records.filter(LiveSurveyModel.located).map(row=>[row.lat,row.lon]));if(!positions.length)return;fitting=true;try{map.fitBounds(positions,{padding:[25,25],maxZoom:16,animate:false});lastFit=true;}finally{fitting=false;}}
    function card(row,color){
      let item=entries.get(row.profile.id);if(item)return item;
      const root=el('article');root.className='survey-radio-card';root.dataset.surveyRadio=row.profile.id;root.style.setProperty('--radio-color',color);
      const heading=el('h3',row.profile.name),mode=el('p'),phase=el('strong'),detail=el('p'),rssi=el('p'),gps=el('p'),recording=el('p'),error=el('p'),action=el('button','Open Field controls');action.type='button';action.className='secondary';action.onclick=()=>onField(item.row,data.session_id);
      phase.className='survey-radio-phase';detail.className='fine';error.className='form-error';gps.className='survey-radio-gps';rssi.className='survey-radio-rssi';root.append(heading,mode,phase,detail,rssi,gps,recording,error,action);cards.append(root);
      item={root,heading,mode,phase,detail,rssi,gps,recording,error,row};entries.set(row.profile.id,item);return item;
    }
    function tick(options){
      if(!data)return;
      for(const row of data.radios){const item=entries.get(row.profile.id);if(!item)continue;
        const currentRow={...row,job:options.jobs?.find(job=>job.id===row.job?.id)||row.job};
        const state=LiveSurveyModel.presentation(currentRow,{...options,statusModel});
        const fix=fixMarkers.get(row.profile.id);if(fix){fix.marker.setStyle({fillColor:state.fixCurrent?fix.color:'#fff',dashArray:state.fixCurrent?null:'3 3'});fix.marker.setTooltipContent(el('span',row.profile.name+' · '+(state.fixCurrent?'Current GPS fix':'Last located fix · historical')+' · '+row.latest_fix.position_time));}
        item.phase.textContent=state.connection.label;item.root.dataset.connection=state.connection.code;item.detail.textContent=state.connection.detail;
        item.rssi.textContent=state.rssi===null?'Receive RSSI · —':'Receive RSSI · '+state.rssi+' /100';
        const sampled=state.seconds===null?'No saved sample time':(state.current?'Latest sample · ':'Last saved sample · ')+state.seconds+'s ago';
        item.gps.textContent=state.gps+(state.gpsSeconds!==null?' · fix '+state.gpsSeconds+'s old':'');
        item.recording.textContent=sampled+' · '+row.records.filter(p=>p.rssi!==null).length+' mapped receive points in view'+(row.truncated?' · recent window only':'')+(row.route_breaks_limited?' · link history limited; route lines omitted':'');
        item.error.hidden=!(row.error||row.warning);item.error.textContent=row.error||row.warning||'';
      }
    }
    function update(next,options){
      if(scope!==next.session_id){lastFit=false;scope=next.session_id;}
      data=next;ensureMap(next.offline);
      // Assign a separate hue to every displayed radio, consistent across scope changes.
      const ids=[...new Set([...(options.profileIds||[]),...next.radios.map(row=>row.profile.id)])].sort();
      const colors=LiveSurveyModel.colors(ids);
      if(layer)layer.clearLayers();fixMarkers.clear();
      for(const row of next.radios){const color=colors[row.profile.id],item=card(row,color);item.row=row;item.root.style.setProperty('--radio-color',color);item.heading.textContent=row.profile.name;
        const modes=row.sweep_types?.length?row.sweep_types:[row.job?.result?.sweep_type||'unclassified'];item.mode.textContent=row.profile.role.toUpperCase()+' · '+modes.join(' / ').toUpperCase()+' sweep';
        if(!layer)continue;
        for(const segment of LiveSurveyModel.segments(row.records))L.polyline(segment.map(p=>[p.lat,p.lon]),{color,weight:2,dashArray:'5 6',opacity:.65}).addTo(layer);
        for(const point of row.records.filter(p=>p.rssi!==null&&LiveSurveyModel.located(p))){const label=el('span',row.profile.name+' · '+point.sweep_type+' · RSSI '+point.rssi+'/100 · '+point.time);L.circleMarker([point.lat,point.lon],{radius:4,color,fillColor:color,weight:1,fillOpacity:.25+.75*point.rssi/100}).bindTooltip(label).addTo(layer);}
        const last=row.latest_fix;if(LiveSurveyModel.located(last)){const marker=L.circleMarker([last.lat,last.lon],{radius:9,color,fillColor:'#fff',weight:3,fillOpacity:1,dashArray:'3 3'}).bindTooltip(el('span',row.profile.name+' · Last located fix · historical')).addTo(layer);fixMarkers.set(row.profile.id,{marker,color});}
      }
      for(const [id,item] of entries)if(!next.radios.some(row=>row.profile.id===id)){item.root.remove();entries.delete(id);}
      tick(options);map?.invalidateSize();if(!lastFit||document.getElementById('survey-map-auto-fit').checked)fit();
    }
    return {update,tick,fit,resize:()=>map?.invalidateSize()};
  }
  return {create};
})();

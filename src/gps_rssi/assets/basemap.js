'use strict';
const RssiBasemap=(()=>{
  const sources=__BASEMAP_SOURCES__;
  const maps=new WeakMap();
  function status(message){const note=document.getElementById('basemap-status');if(note)note.textContent=message;}
  function addTo(map,{cached=false,background='osm'}={}){
    if(maps.has(map))return maps.get(map);
    const here=typeof location==='undefined'?null:location;
    if(here?.protocol==='file:'){
      status('Background maps need a web address. Open this survey from the app’s Activity to use cached maps. Measurements, charts and tables remain available here.');
      return null;
    }
    const workspace=here&&['127.0.0.1','localhost'].includes(here.hostname)&&/^\/files\/[^/]+\/[^/]+$/.test(here.pathname);
    const token=workspace?new URLSearchParams(here.search).get('tile_token'):null;
    if(workspace&&!token){
      status('Open this survey from the app’s Activity to use cached maps. Measurements, charts and tables remain available here.');
      return null;
    }
    const local=cached||workspace;
    const offline=globalThis.RssiOfflineConfig;
    const available={...sources};
    if(local&&offline?.map){
      const m=offline.map,escape=text=>String(text||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      available.local={label:'Offline map',maxNativeZoom:Number(m.maxzoom),minZoom:Number(m.minzoom),
        attribution:escape(m.attribution||m.name||'Imported raster MBTiles'),note:`${m.name||'Imported raster map'} · stored locally; zooms ${m.minzoom}–${m.maxzoom}.`};
    }
    const layers={},details=new Map();
    function selected(layer){
      const source=details.get(layer);
      if(!source){status('No background selected. Measurements remain visible.');return;}
      status(`${source.label} tiles ${offline?.offline?'use saved data only (Offline mode).':local?'are cached on this computer as you view them.':'use your browser’s cache.'} ${source.note||''}`.trim());
    }
    for(const [id,source] of Object.entries(available)){
      const url=local?`/tiles/${id}/{z}/{x}/{y}.png`+(token?'?tile_token='+encodeURIComponent(token):''):source.url;
      const layer=L.tileLayer(url,{maxZoom:19,maxNativeZoom:source.maxNativeZoom,
        minZoom:source.minZoom||0,referrerPolicy:'origin',keepBuffer:0,attribution:source.attribution});
      layer.on('tileerror',()=>{
        if(!map.hasLayer(layer))return;
        if(offline?.offline||id==='local'){
          status(`${source.label}: some tiles are missing. Available saved tiles remain visible; gaps stay blank and are queued. ${id==='local'?'Import a map covering this area and zoom.':'Fill missing data after reconnecting.'}`);
          return;
        }
        map.removeLayer(layer);
        status(`${source.label} tiles could not load. Choose another map background. Measurements, charts and tables remain available.`);
      });
      layers[source.label]=layer;details.set(layer,source);
    }
    layers['No background']=L.layerGroup();
    const control=L.control.layers(layers,null,{position:'bottomleft',collapsed:false}).addTo(map);
    control.getContainer().setAttribute('aria-label','Map background');
    map.on('baselayerchange',event=>selected(event.layer));
    const initial=background==='none'?layers['No background']:offline?.offline&&layers['Offline map']?layers['Offline map']:layers.Street;
    initial.addTo(map);selected(initial);
    maps.set(map,initial);
    return initial;
  }
  return {addTo};
})();

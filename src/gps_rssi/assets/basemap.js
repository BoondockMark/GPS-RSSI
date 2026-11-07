'use strict';
const RssiBasemap=(()=>{
  const sources=__BASEMAP_SOURCES__;
  const maps=new WeakMap();
  function status(message){const note=document.getElementById('basemap-status');if(note)note.textContent=message;}
  function addTo(map,{cached=false}={}){
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
    const layers={},details=new Map();
    function selected(layer){
      const source=details.get(layer);
      if(!source){status('No background selected. Measurements remain visible.');return;}
      status(`${source.label} tiles ${local?'are cached on this computer as you view them.':'use your browser’s cache.'} ${source.note||''}`.trim());
    }
    for(const [id,source] of Object.entries(sources)){
      const url=local?`/tiles/${id}/{z}/{x}/{y}.png`+(token?'?tile_token='+encodeURIComponent(token):''):source.url;
      const layer=L.tileLayer(url,{maxZoom:19,maxNativeZoom:source.maxNativeZoom,
        referrerPolicy:'origin',keepBuffer:0,attribution:source.attribution});
      layer.on('tileerror',()=>{
        if(!map.hasLayer(layer))return;
        map.removeLayer(layer);
        status(`${source.label} tiles could not load. Choose another map background. Measurements, charts and tables remain available.`);
      });
      layers[source.label]=layer;details.set(layer,source);
    }
    layers['No background']=L.layerGroup();
    const control=L.control.layers(layers,null,{position:'bottomleft',collapsed:false}).addTo(map);
    control.getContainer().setAttribute('aria-label','Map background');
    map.on('baselayerchange',event=>selected(event.layer));
    layers.Street.addTo(map);selected(layers.Street);
    maps.set(map,layers.Street);
    return layers.Street;
  }
  return {addTo};
})();

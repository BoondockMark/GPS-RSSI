'use strict';
const RssiBasemap=(()=>{
  let unavailable=false;
  function status(message){const note=document.getElementById('basemap-status');if(note)note.textContent=message;}
  function addTo(map,{cached=false}={}){
    if(unavailable)return null;
    const here=typeof location==='undefined'?null:location;
    if(here?.protocol==='file:'){
      status('Street maps need a web address. Open this survey from the app’s Activity to use cached street maps. Measurements, charts and tables remain available here.');
      return null;
    }
    const workspace=here&&['127.0.0.1','localhost'].includes(here.hostname)&&/^\/files\/[^/]+\/[^/]+$/.test(here.pathname);
    const token=workspace?new URLSearchParams(here.search).get('tile_token'):null;
    if(workspace&&!token){
      status('Open this survey from the app’s Activity to use cached street maps. Measurements, charts and tables remain available here.');
      return null;
    }
    const local=cached||workspace;
    const layer=L.tileLayer(local?'/tiles/osm/{z}/{x}/{y}.png'+(token?'?tile_token='+encodeURIComponent(token):''):'https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,referrerPolicy:'origin',keepBuffer:0,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    });
    layer.on?.('tileerror',()=>{
      if(unavailable)return;
      unavailable=true;map.removeLayer(layer);
      status('Street tiles could not load. The background has been hidden; measurements, charts and tables remain available. Reopen the map to try again.');
    });
    layer.addTo(map);
    status(local?'Street tiles are cached on this computer as you view them.':'Street tiles use your browser’s cache. Open surveys from the app’s Activity to use its persistent tile cache.');
    return layer;
  }
  return {addTo};
})();

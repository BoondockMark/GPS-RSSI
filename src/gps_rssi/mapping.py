"""Standalone HTML presentation of qualified measurement points."""

import json
from importlib.resources import files

from .basemaps import basemap_script

from . import review


def render_map(points, excluded, *, max_age, max_accuracy):
    # Neutralize HTML script termination in station names or other stored text.
    payload = json.dumps({"points": points, "excluded": excluded,
                          "max_age": max_age, "max_accuracy": max_accuracy}, allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    visuals = files("gps_rssi").joinpath("assets/visualization.js").read_text(encoding="utf-8")
    return TEMPLATE.replace("__REVIEW_CONTROLS__", review.CONTROLS).replace("__REVIEW_JS__", review.script()).replace("__BASEMAP_JS__", basemap_script()).replace("__VISUAL_JS__", visuals).replace("__GPS_RSSI_DATA__", payload)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GPS-RSSI mobile reception map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
 integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>
body{font:16px system-ui,sans-serif;margin:0;color:#152431;background:#f6f8fa}
header,main{padding:1rem 1.5rem}h1{margin:0 0 .5rem;font-size:1.6rem}
#map{height:65vh;min-height:300px;background:#e3e9ed}p{max-width:1000px}
.scale{display:inline-block;width:160px;height:12px;background:linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)}
table{border-collapse:collapse;width:100%;font-size:.85rem}td,th{padding:.5rem;text-align:left;border-bottom:1px solid #ccd5dd}
.table-wrap{overflow:auto}#warning{font-weight:bold}summary{cursor:pointer}
</style></head><body>
<header><h1>Mobile reception measurements</h1>
<p>Fixed-site-to-mobile direction. Measured points only; unmeasured areas have no coverage rating.
Signal strength is relative (16 levels scaled to 0–100), not dBm or a voice-quality score.
The transmitting station is not identified by this logger.</p>
<p><span class="scale"></span> 0 weaker → 100 stronger.
Replay points have larger purple outlines; closed-squelch points have dashed outlines.</p>
<p id="summary"></p><p id="warning"></p></header>
__REVIEW_CONTROLS__
<p id="basemap-status" role="status" aria-live="polite"></p>
<div id="map" role="region" aria-label="Interactive reception map"></div>
<main><p id="filters"></p><p id="exclusions"></p>
<p>Measurements are embedded in this file. The interactive map needs Internet access for
Leaflet and OpenStreetMap tiles. The table below also works without those services.</p>
<details><summary>Show plotted measurements</summary><div class="table-wrap"><table>
<thead><tr><th>Select</th><th>ID</th><th>Station</th><th>Session</th><th>Source</th><th>UTC sample time</th><th>GPS time</th>
<th>Latitude</th><th>Longitude</th><th>Relative RSSI</th><th>Squelch</th><th>Channel ID</th><th>Frequency (Hz)</th><th>RX frequency (Hz)</th><th>TX frequency (Hz)</th></tr></thead>
<tbody id="rows"></tbody></table></div></details></main>
<script id="measurement-data" type="application/json">__GPS_RSSI_DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
 integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>__BASEMAP_JS__</script>
<script>__VISUAL_JS__</script>
<script>__REVIEW_JS__</script>
<script>
const data=JSON.parse(document.getElementById('measurement-data').textContent);
const originalPoints=data.points;
const review=SurveyReview.create(originalPoints,{fields:['id','station','session','source','time','position_time','lat','lon','rssi','squelch_open','channel','frequency_hz','rx_frequency_hz','tx_frequency_hz'],filename:'reviewed-mobile-measurements.csv',onChange:renderView});
let map=null;
function renderView(){
const points=originalPoints.filter((p,i)=>!review.removed.has(String(i)));
const view=map?{center:map.getCenter(),zoom:map.getZoom()}:null;
if(map){map.remove();map=null;}
const live=points.filter(p=>p.source!=='replay').length;
document.getElementById('summary').textContent=`Plotted ${points.length} points: ${live} live, ${points.length-live} replay.`;
document.getElementById('filters').textContent=`GPS filters: absolute fix/sample time difference ≤ ${data.max_age}s; reported accuracy ≤ ${data.max_accuracy}m.`;
document.getElementById('exclusions').textContent='Excluded from selected samples: '+
  (Object.entries(data.excluded).map(([k,v])=>`${k}: ${v}`).join('; ') || 'none');
const body=document.getElementById('rows');
if(!body.children.length)for(const p of originalPoints){
  const row=document.createElement('tr');row.setAttribute('data-row',String(originalPoints.indexOf(p)));
  for(const value of [p.id,p.station,p.session,p.source,p.time,p.position_time,p.lat,p.lon,p.rssi,p.squelch_open?'open':'closed',p.channel,p.frequency_hz??'',p.rx_frequency_hz??'',p.tx_frequency_hz??'']){
    const cell=document.createElement('td');cell.textContent=String(value);row.appendChild(cell);
  }body.appendChild(row);
}
for(const row of body.children)row.hidden=review.removed.has(row.getAttribute('data-row'));
review.setVisible(originalPoints.map((p,i)=>String(i)).filter(key=>!review.removed.has(key)));
document.getElementById('map').style.display='';document.getElementById('warning').textContent='';
if(!points.length){
  document.getElementById('warning').textContent='No eligible mobile receive points. Check report, source selection, GPS timestamps, and accuracy.';
  document.getElementById('map').style.display='none';
}else if(typeof L==='undefined'){
  document.getElementById('warning').textContent='Map library could not load. Connect to the Internet or use the measurement table below.';
}else{
  map=L.map('map');
  RssiBasemap.addTo(map);
  const layers={};
  for(const p of points){
    const replay=p.source==='replay';
    const label=`${replay?'REPLAY':'LIVE'} | ${p.station} | ${p.session}`;
    // Leaflet treats layer labels as HTML: escape database-supplied text.
    const node=document.createElement('span');node.textContent=label;
    const key=node.innerHTML;
    if(!layers[key])layers[key]=L.layerGroup().addTo(map);
    const marker=L.circleMarker([p.lat,p.lon],{
      radius:review.selected.has(String(originalPoints.indexOf(p)))?9:replay?7:5,color:review.selected.has(String(originalPoints.indexOf(p)))?'#e11d48':replay?'#7e22ce':'#243746',weight:review.selected.has(String(originalPoints.indexOf(p)))?4:replay?3:1,
      dashArray:p.squelch_open?null:'3 3',fillOpacity:.8,
      fillColor:RssiVisuals.color(p.rssi)
    });
    const popup=document.createElement('div');
    popup.style.whiteSpace='pre-line';
    popup.textContent=`${label}\\nSample ${p.id} | ${p.time}\\nGPS ${p.position_time}\\nRelative RSSI ${p.rssi.toFixed(1)} / 100\\nChannel ID ${p.channel} | Frequency ${p.frequency_hz??'unknown'} Hz | Squelch ${p.squelch_open?'open':'closed'}`;
    marker.bindPopup(popup).addTo(layers[key]);
    marker.on('click',()=>review.mapClick(String(originalPoints.indexOf(p))));
  }
  L.control.layers(null,layers,{collapsed:false}).addTo(map);
  if(view)map.setView(view.center,view.zoom);
  else map.fitBounds(L.latLngBounds(points.map(p=>[p.lat,p.lon])),{padding:[25,25],maxZoom:16});
}
}
renderView();
</script></body></html>
"""

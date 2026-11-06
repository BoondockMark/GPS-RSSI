"""Standalone HTML presentation of qualified measurement points."""

import json


def render_map(points, excluded, *, max_age, max_accuracy):
    # Neutralize HTML script termination in station names or other stored text.
    payload = json.dumps({"points": points, "excluded": excluded,
                          "max_age": max_age, "max_accuracy": max_accuracy}, allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return TEMPLATE.replace("__GPS_RSSI_DATA__", payload)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GPS-RSSI mobile reception map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
 integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>
body{font:16px system-ui,sans-serif;margin:0;color:#152431;background:#f6f8fa}
header,main{padding:1rem 1.5rem}h1{margin:0 0 .5rem;font-size:1.6rem}
#map{height:65vh;min-height:300px;background:#e3e9ed}p{max-width:1000px}
.scale{display:inline-block;width:160px;height:12px;background:linear-gradient(90deg,#dc2626,#d4af16,#16a34a)}
table{border-collapse:collapse;width:100%;font-size:.85rem}td,th{padding:.5rem;text-align:left;border-bottom:1px solid #ccd5dd}
.table-wrap{overflow:auto}#warning{font-weight:bold}summary{cursor:pointer}
</style></head><body>
<header><h1>Mobile reception measurements</h1>
<p>Repeater-to-mobile direction. Measured points only; unmeasured areas have no coverage rating.
Signal strength is relative (16 levels scaled to 0–100), not dBm or a voice-quality score.
The transmitting station is not identified by this logger.</p>
<p><span class="scale"></span> 0 weaker → 100 stronger.
Replay points have larger purple outlines; closed-squelch points have dashed outlines.</p>
<p id="summary"></p><p id="warning"></p></header>
<div id="map" role="region" aria-label="Interactive reception map"></div>
<main><p id="filters"></p><p id="exclusions"></p>
<p>Measurements are embedded in this file. The interactive map needs Internet access for
Leaflet and OpenStreetMap tiles. The table below also works without those services.</p>
<details><summary>Show plotted measurements</summary><div class="table-wrap"><table>
<thead><tr><th>ID</th><th>Station</th><th>Session</th><th>Source</th><th>UTC sample time</th><th>GPS time</th>
<th>Latitude</th><th>Longitude</th><th>Relative RSSI</th><th>Squelch</th><th>Channel ID</th></tr></thead>
<tbody id="rows"></tbody></table></div></details></main>
<script id="measurement-data" type="application/json">__GPS_RSSI_DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
 integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>
const data=JSON.parse(document.getElementById('measurement-data').textContent);
const points=data.points;
const live=points.filter(p=>p.source!=='replay').length;
document.getElementById('summary').textContent=`Plotted ${points.length} points: ${live} live, ${points.length-live} replay.`;
document.getElementById('filters').textContent=`GPS filters: absolute fix/sample time difference ≤ ${data.max_age}s; reported accuracy ≤ ${data.max_accuracy}m.`;
document.getElementById('exclusions').textContent='Excluded from selected samples: '+
  (Object.entries(data.excluded).map(([k,v])=>`${k}: ${v}`).join('; ') || 'none');
const body=document.getElementById('rows');
for(const p of points){
  const row=document.createElement('tr');
  for(const value of [p.id,p.station,p.session,p.source,p.time,p.position_time,p.lat,p.lon,p.rssi,p.squelch_open?'open':'closed',p.channel]){
    const cell=document.createElement('td');cell.textContent=String(value);row.appendChild(cell);
  }body.appendChild(row);
}
if(!points.length){
  document.getElementById('warning').textContent='No eligible mobile receive points. Check report, source selection, GPS timestamps, and accuracy.';
  document.getElementById('map').style.display='none';
}else if(typeof L==='undefined'){
  document.getElementById('warning').textContent='Map library could not load. Connect to the Internet or use the measurement table below.';
}else{
  const map=L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  const layers={};
  for(const p of points){
    const replay=p.source==='replay';
    const label=`${replay?'REPLAY':'LIVE'} | ${p.station} | ${p.session}`;
    // Leaflet treats layer labels as HTML: escape database-supplied text.
    const node=document.createElement('span');node.textContent=label;
    const key=node.innerHTML;
    if(!layers[key])layers[key]=L.layerGroup().addTo(map);
    const marker=L.circleMarker([p.lat,p.lon],{
      radius:replay?7:5,color:replay?'#7e22ce':'#243746',weight:replay?3:1,
      dashArray:p.squelch_open?null:'3 3',fillOpacity:.8,
      fillColor:`hsl(${p.rssi*1.2},75%,42%)`
    });
    const popup=document.createElement('div');
    popup.style.whiteSpace='pre-line';
    popup.textContent=`${label}\\nSample ${p.id} | ${p.time}\\nGPS ${p.position_time}\\nRelative RSSI ${p.rssi.toFixed(1)} / 100\\nChannel ID ${p.channel} | Squelch ${p.squelch_open?'open':'closed'}`;
    marker.bindPopup(popup).addTo(layers[key]);
  }
  L.control.layers(null,layers,{collapsed:false}).addTo(map);
  map.fitBounds(L.latLngBounds(points.map(p=>[p.lat,p.lon])),{padding:[25,25],maxZoom:16});
}
</script></body></html>
"""

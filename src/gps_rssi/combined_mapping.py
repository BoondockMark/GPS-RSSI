"""Standalone two-direction map and table for time-associated observations."""

from html import escape
import json


def render_combined_map(rows, summary):
    payload = json.dumps({"rows": rows, "summary": summary}, allow_nan=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    # A static table remains available even without JavaScript or map services.
    table = []
    fields = ("direction", "receiver_label", "receiver_session", "receiver_sample", "corrected_time",
              "rssi", "rssi_unit", "lat", "lon", "gps_gap", "status", "reason")
    for row in rows:
        table.append("<tr>" + "".join(f"<td>{escape(str(row.get(k) if row.get(k) is not None else ''))}</td>" for k in fields) + "</tr>")
    before, _, after = TEMPLATE.partition("__TABLE__")
    return before + "".join(table) + after.replace("__DATA__", payload)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Combined RF survey</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous">
<style>body{font:16px system-ui;margin:0;color:#152431}header,main{padding:1rem}
#map{height:65vh;background:#e3e9ed}.scroll{overflow:auto}table{border-collapse:collapse;font-size:.85rem}
td,th{padding:.4rem;border-bottom:1px solid #ddd;text-align:left}#error{color:#9b1c1c}</style></head>
<body><header><h1>Combined RF survey</h1><p id="summary"></p>
<p>Downlink: fixed to mobile. Uplink: mobile to fixed. Each point uses the mobile GPS position.
RSSI remains relative to its receiver, not dBm. Time association does not identify a transmitter
or establish two-way voice coverage. Unconfirmed layers are hidden initially.</p>
<p id="error">Map requires JavaScript, Internet access, Leaflet and OpenStreetMap tiles. The table remains available below.</p>
</header><div id="map" aria-label="Combined directional measurements"></div><main>
<p>0 weaker → 100 stronger. Associated points have dark outlines; unconfirmed points have purple outlines.
Closed-squelch receive points have dashed outlines. CSV includes every receive observation and its rejection reason.</p>
<details><summary>All receive observations, including unmatched results</summary><div class="scroll"><table>
<thead><tr><th>Direction</th><th>Receiver label</th><th>Session</th><th>Sample</th><th>Corrected UTC time</th>
<th>RSSI</th><th>Unit</th><th>Latitude</th><th>Longitude</th><th>GPS gap (s)</th><th>Status</th><th>Reason</th></tr></thead>
<tbody>__TABLE__</tbody></table></div></details></main>
<script id="combined-data" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('combined-data').textContent);
document.getElementById('summary').textContent=(data.summary.source==='replay'?'REPLAY — synthetic input | ':'LIVE input | ')+
 (Object.entries(data.summary.counts).map(([k,v])=>`${k}: ${v}`).join(' | ')||'No receive observations.');
if(typeof L!=='undefined'){
 const map=L.map('map',{preferCanvas:true}).setView([33.96,-117.95],13), layers={}, associated=[];
 L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,
 attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
 for(const row of data.rows){
  if(row.lat===null||row.lon===null)continue;
  const label=`${row.direction==='uplink'?'Mobile → fixed':'Fixed → mobile'} | ${row.status}`;
  if(!layers[label]){layers[label]=L.layerGroup();if(row.status==='associated')layers[label].addTo(map);}
  const popup=document.createElement('div');popup.style.whiteSpace='pre-line';
  popup.textContent=`${label} | ${row.source==='replay'?'REPLAY':'LIVE'}\\n${row.receiver_station} | ${row.receiver_label}\\nSample ${row.receiver_sample} | ${row.corrected_time}\\nRelative RSSI ${row.rssi} | GPS gap ${row.gps_gap.toFixed(2)}s\\n${row.reason}`;
  L.circleMarker([row.lat,row.lon],{radius:6,color:row.status==='associated'?'#243746':'#7e22ce',
    fillColor:`hsl(${row.rssi*1.2},75%,42%)`,fillOpacity:.8,dashArray:row.squelch_open?null:'3 3'})
    .bindPopup(popup).addTo(layers[label]);
  if(row.status==='associated')associated.push([row.lat,row.lon]);
 }
 L.control.layers(null,layers,{collapsed:false}).addTo(map);
 if(associated.length)map.fitBounds(L.latLngBounds(associated),{padding:[25,25],maxZoom:16});
 document.getElementById('error').textContent=associated.length?'':'No associated map points. Inspect the table, summary and optional unconfirmed layers.';
}
</script></body></html>
"""

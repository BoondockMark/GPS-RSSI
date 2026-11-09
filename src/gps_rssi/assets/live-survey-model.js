/* Geometry and freshness rules for observed live routes; no coverage interpolation. */
(function(root){
  'use strict';
  const located=row=>Number.isFinite(row?.lat)&&Number.isFinite(row?.lon)&&Math.abs(row.lat)<=90&&Math.abs(row.lon)<=180;
  function distance(a,b){const rad=x=>x*Math.PI/180,dlat=rad(b.lat-a.lat),dlon=rad(b.lon-a.lon),h=Math.sin(dlat/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dlon/2)**2;return 6371000*2*Math.asin(Math.sqrt(Math.max(0,Math.min(1,h))));}
  function segments(records){const result=[];let previous=null;
    for(const row of records){if(!located(row)||!Number.isFinite(Date.parse(row.time))){previous=null;continue;}
      if(previous){const elapsed=(Date.parse(row.time)-Date.parse(previous.time))/1000;
        if(previous.session===row.session&&!row.break_before&&elapsed>0&&elapsed<=30&&distance(previous,row)/elapsed<=80)result.push([previous,row]);
      }previous=row;
    }return result;
  }
  function colors(ids){return Object.fromEntries([...new Set(ids)].sort().map((id,i)=>[id,`hsl(${(i*137.508)%360} 65% 33%)`]));}
  function age(at,now){const milliseconds=now-Date.parse(at);return Number.isFinite(milliseconds)&&milliseconds>=-2000?Math.max(0,Math.floor(milliseconds/1000)):null;}
  function presentation(row,{now=Date.now(),workspaceAt,mapAt,statusModel}={}){
    const fresh=Number.isFinite(workspaceAt)&&Number.isFinite(mapAt)&&now-workspaceAt<8000&&now-mapAt<8000&&now-mapAt>=-2000&&now-workspaceAt>=-2000;
    const connection=statusModel.radioStatus(row.profile,row.job,{now,workspaceAt,lastJob:row.job});
    const latest=row.latest,seconds=age(latest?.received_at,now),limit=row.profile.telemetry_max_age_seconds||30;
    const current=fresh&&!row.error&&row.job?.state==='running'&&['recording','receiving'].includes(connection.code)&&seconds!==null&&seconds<=limit&&latest?.recording_session_id===row.job?.result?.recording_session_id;
    const rx=current&&latest?.is_in_rx&&!latest.is_in_tx&&latest.rssi_unit==='benshi_relative_0_100'&&Number.isFinite(latest.rssi_value)&&latest.rssi_value>=0&&latest.rssi_value<=100;
    const fix=row.latest_fix,gpsSeconds=age(fix?.position_time,now),last=row.records.at(-1);
    const fixCurrent=current&&row.profile.role==='mobile'&&row.profile.gps!==false&&located(fix)&&fix.id===last?.id&&gpsSeconds!==null&&gpsSeconds<=10;
    return {connection,current,rssi:rx?latest.rssi_value:null,seconds,gpsSeconds,fixCurrent,fresh,
      gps:row.profile.role==='fixed'?'Fixed receiver · no mobile position':!row.profile.gps?'Position recording off':row.error?'Position unavailable':row.gps_reason?row.gps_reason.replaceAll('_',' '):fixCurrent?'GPS current':fix?'Last fix · historical':'Waiting for a qualified GPS fix'};
  }
  const api={located,distance,segments,colors,age,presentation};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.LiveSurveyModel=api;
})(globalThis);

'use strict';
// Geometry is independent of Leaflet and the DOM; distances are WGS84-sphere estimates.
globalThis.RouteAnalysis=(()=>{
  const R=6371000,rad=n=>n*Math.PI/180;
  const located=p=>Number.isFinite(p.lat)&&Number.isFinite(p.lon)&&Math.abs(p.lat)<=90&&Math.abs(p.lon)<=180;
  function distance(a,b){
    const h=Math.sin(rad(b.lat-a.lat)/2)**2+Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(rad(b.lon-a.lon)/2)**2;
    return 2*R*Math.asin(Math.sqrt(Math.min(1,Math.max(0,h))));
  }
  function routes(rows){
    const sessions=new Map();
    for(const row of rows){if(!sessions.has(row.session))sessions.set(row.session,[]);sessions.get(row.session).push(row);}
    const result=[];
    for(const [session,entries] of sessions){
      const points=[],segments=[];let previous=null,travelled=0,part=0;
      for(const row of entries.slice().sort((a,b)=>Date.parse(a.time)-Date.parse(b.time))){
        if(!located(row)||!Number.isFinite(Date.parse(row.time))){previous=null;part++;continue;}
        const elapsed=previous?(Date.parse(row.time)-Date.parse(previous.time))/1000:0;
        const metres=previous?distance(previous,row):0;
        const sameChannel=previous&&previous.channel===row.channel&&previous.frequency_hz===row.frequency_hz;
        const connected=previous&&sameChannel&&elapsed>0&&elapsed<=60&&metres/elapsed<=70;
        if(connected)travelled+=metres;else part++;
        const point={...row,distance:travelled,part};
        if(connected)segments.push([previous,point]);
        points.push(point);previous=point;
      }
      result.push({session,points,segments,distance:travelled});
    }
    return result;
  }
  function sample(points,limit=96){
    if(points.length<=limit)return points.slice();
    return Array.from({length:limit},(_,i)=>points[Math.round(i*(points.length-1)/(limit-1))]);
  }
  function path(start,end,count=65){
    const angle=distance(start,end)/R;
    if(angle<1e-10)return [{...start}];
    const vector=p=>[Math.cos(rad(p.lat))*Math.cos(rad(p.lon)),Math.cos(rad(p.lat))*Math.sin(rad(p.lon)),Math.sin(rad(p.lat))];
    const a=vector(start),b=vector(end);
    return Array.from({length:count},(_,i)=>{
      const f=i/(count-1),x=Math.sin((1-f)*angle)/Math.sin(angle),y=Math.sin(f*angle)/Math.sin(angle);
      const v=a.map((value,j)=>x*value+y*b[j]);
      return {lat:Math.atan2(v[2],Math.hypot(v[0],v[1]))*180/Math.PI,lon:Math.atan2(v[1],v[0])*180/Math.PI};
    });
  }
  function clearance(points,elevations,startHeight,endHeight){
    const total=points.length>1?distance(points[0],points.at(-1)):0;
    if(points.length<3||!Number.isFinite(total)||total<1||!points.every(located)||elevations.length!==points.length||elevations.some(e=>!Number.isFinite(e))||![startHeight,endHeight].every(h=>Number.isFinite(h)&&h>=0))return {status:'unknown',samples:[],total};
    const start=elevations[0]+startHeight,end=elevations.at(-1)+endHeight;
    const samples=points.map((point,i)=>{
      const d=total*i/(points.length-1),line=start+(end-start)*d/total;
      const terrain=elevations[i]+d*(total-d)/(2*R);
      return {...point,distance:d,elevation:elevations[i],terrain,line,clearance:line-terrain};
    });
    const worst=samples.slice(1,-1).reduce((a,b)=>a.clearance<b.clearance?a:b);
    return {status:worst.clearance<0?'obstructed':'sampled-clear',samples,total,worst,spacing:total/(points.length-1)};
  }
  return {located,distance,routes,sample,path,clearance};
})();

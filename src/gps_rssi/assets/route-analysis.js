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
  function samplingPlan(total,nodes,{maxSamples=1025,maxSpacing=10}={}){
    if(!Number.isFinite(total)||total<=0||!Number.isInteger(maxSamples)||maxSamples<17||maxSamples>4097||!Number.isFinite(maxSpacing)||maxSpacing<.5||maxSpacing>100)throw Error('Invalid path sampling settings.');
    if(!Array.isArray(nodes)||nodes.length<2||nodes[0].fraction!==0||nodes.at(-1).fraction!==1||nodes.some((n,i)=>!Number.isFinite(n.fraction)||(i>0&&n.fraction<=nodes[i-1].fraction)))throw Error('Path samples must be ordered from 0 to 1.');
    const gaps=[];let maxGap=0,minGap=Infinity,targetMin=Infinity,targetMax=0,unknown=0,finerThanFloor=false;
    const resolution=n=>{
      const r=n.metadata?.resolution_m;
      if(!Number.isFinite(r)||r<=0){unknown++;return maxSpacing;}
      if(r<.5)finerThanFloor=true;
      return Math.max(.5,Math.min(maxSpacing,r));
    };
    const targets=nodes.map(resolution);
    for(let i=0;i<nodes.length-1;i++){
      const gap=total*(nodes[i+1].fraction-nodes[i].fraction),target=Math.min(targets[i],targets[i+1]);
      maxGap=Math.max(maxGap,gap);minGap=Math.min(minGap,gap);targetMin=Math.min(targetMin,target);targetMax=Math.max(targetMax,target);
      if(gap>target*(1+1e-9))gaps.push({fraction:(nodes[i].fraction+nodes[i+1].fraction)/2,left:i,right:i+1,priority:gap/target});
    }
    gaps.sort((a,b)=>b.priority-a.priority||a.fraction-b.fraction);
    const next=gaps.slice(0,Math.min(16,Math.max(0,maxSamples-nodes.length)));
    return {next,maxGap,minGap,targetMin,targetMax,unknownResolution:unknown,finerThanFloor,
      incomplete:gaps.length>0||finerThanFloor,capped:gaps.length>0&&nodes.length>=maxSamples};
  }
  function matchLocations(aRows,bRows,{radius=25,sameFrequency=true,limit=5000}={}){
    if(!Number.isFinite(radius)||radius<1||radius>200)throw Error('Choose a matching radius between 1 and 200 metres.');
    const eligible=rows=>{
      const seen=new Set();
      return rows.filter(p=>{
        if(!located(p)||!Number.isFinite(Date.parse(p.time))||!Number.isFinite(p.rssi)||p.rssi<0||p.rssi>100||p.rssi_unit!=='benshi_relative_0_100'||!['downlink','uplink'].includes(p.direction)||p.key==null||seen.has(p.key))return false;
        if(sameFrequency&&!(Number.isFinite(p.frequency_hz)&&p.frequency_hz>0))return false;
        seen.add(p.key);return true;
      }).slice().sort((a,b)=>Date.parse(a.time)-Date.parse(b.time)||String(a.key).localeCompare(String(b.key)));
    };
    const allA=eligible(aRows),allB=eligible(bRows),a=allA.slice(0,limit),b=allB.slice(0,limit),grid=new Map();
    // Cartesian cells avoid longitude wrapping and polar singularities.
    const cell=p=>[R*Math.cos(rad(p.lat))*Math.cos(rad(p.lon)),R*Math.cos(rad(p.lat))*Math.sin(rad(p.lon)),R*Math.sin(rad(p.lat))].map(v=>Math.floor(v/radius));
    for(const p of b){const key=cell(p).join(',');if(!grid.has(key))grid.set(key,new Set());grid.get(key).add(p);}
    const pairs=[];
    for(const p of a){
      const [x,y,z]=cell(p);let best=null,separation=Infinity,bucket=null;
      for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++)for(let dz=-1;dz<=1;dz++){
        const candidates=grid.get([x+dx,y+dy,z+dz].join(','));if(!candidates)continue;
        for(const q of candidates){
          if(p.session===q.session||p.direction!==q.direction||(sameFrequency&&p.frequency_hz!==q.frequency_hz))continue;
          const metres=distance(p,q);
          if(metres<=radius&&(metres<separation||(metres===separation&&String(q.key)<String(best?.key)))){best=q;separation=metres;bucket=candidates;}
        }
      }
      if(best){bucket.delete(best);pairs.push({a:p,b:best,separation,delta:best.rssi-p.rssi});}
    }
    return {pairs,eligibleA:allA.length,eligibleB:allB.length,consideredA:a.length,consideredB:b.length,truncated:allA.length>a.length||allB.length>b.length};
  }
  function clearance(points,elevations,startHeight,endHeight,{frequencyHz=null}={}){
    const total=points.length>1?distance(points[0],points.at(-1)):0;
    if(points.length<3||!Number.isFinite(total)||total<1||!points.every(located)||elevations.length!==points.length||elevations.some(e=>!Number.isFinite(e))||![startHeight,endHeight].every(h=>Number.isFinite(h)&&h>=0))return {status:'unknown',fresnelStatus:'unknown',samples:[],total};
    const candidate=Number.isFinite(frequencyHz)&&frequencyHz>0?299792458/frequencyHz:null;
    const wavelength=Number.isFinite(candidate)?candidate:null;
    const distances=points.map(point=>distance(points[0],point));
    if(distances.some((d,i)=>i>0&&d<=distances[i-1]))return {status:'unknown',fresnelStatus:'unknown',samples:[],total};
    const start=elevations[0]+startHeight,end=elevations.at(-1)+endHeight;
    const samples=points.map((point,i)=>{
      const d=distances[i],line=start+(end-start)*d/total;
      const terrain=elevations[i]+d*(total-d)/(2*R);
      const fresnelRadius=wavelength===null?null:Math.sqrt(wavelength*d*(total-d)/total);
      const fresnelLower=fresnelRadius===null?null:line-.6*fresnelRadius,fresnelUpper=fresnelRadius===null?null:line+.6*fresnelRadius;
      return {...point,distance:d,elevation:elevations[i],terrain,line,clearance:line-terrain,
        fresnelRadius,fresnelLower,fresnelUpper,fresnelClearance:fresnelLower===null?null:fresnelLower-terrain};
    });
    const worst=samples.slice(1,-1).reduce((a,b)=>a.clearance<b.clearance?a:b);
    const fresnelWorst=wavelength===null?null:samples.slice(1,-1).reduce((a,b)=>a.fresnelClearance<b.fresnelClearance?a:b);
    const spacing=distances.slice(1).map((d,i)=>d-distances[i]);
    return {status:worst.clearance<0?'obstructed':'sampled-clear',samples,total,worst,spacing:Math.max(...spacing),minSpacing:Math.min(...spacing),frequencyHz:wavelength===null?null:frequencyHz,
      fresnelWorst,fresnelStatus:fresnelWorst===null?'unknown':fresnelWorst.fresnelClearance<0?'intruded':'sampled-clear'};
  }
  return {located,distance,routes,sample,path,clearance,matchLocations,samplingPlan};
})();

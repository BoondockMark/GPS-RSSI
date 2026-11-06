'use strict';
// Shared measured-data views. No inferred coverage or cross-receiver averaging.
globalThis.RssiVisuals=(()=>{
  const palette=['#440154','#3b528b','#21918c','#5ec962','#fde725'];
  function valid(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=100;}
  function color(value){
    if(!valid(value))return '#94a3b8';
    const scaled=value/25,index=Math.min(3,Math.floor(scaled)),weight=scaled-index;
    const rgb=hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
    const a=rgb(palette[index]),b=rgb(palette[index+1]);
    return '#'+a.map((v,i)=>Math.round(v+(b[i]-v)*weight).toString(16).padStart(2,'0')).join('');
  }
  function statistics(items){
    const values=items.map(p=>p.rssi).filter(valid).sort((a,b)=>a-b);
    function percentile(fraction){
      if(!values.length)return null;
      const at=(values.length-1)*fraction,low=Math.floor(at),high=Math.ceil(at);
      return values[low]+(values[high]-values[low])*(at-low);
    }
    const bins=Array(10).fill(0);
    for(const value of values)bins[Math.min(9,Math.floor(value/10))]++;
    return {count:values.length,min:values[0]??null,max:values.at(-1)??null,median:percentile(.5),p10:percentile(.1),bins};
  }
  function timelinePoints(items,limit=600){
    const sorted=items.filter(p=>valid(p.rssi)&&Number.isFinite(Date.parse(p.time)))
      .slice().sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
    if(sorted.length<=limit)return sorted;
    const bucketCount=Math.floor(limit/2),start=Date.parse(sorted[0].time),span=Date.parse(sorted.at(-1).time)-start;
    const buckets=new Map(),chosen=new Set([sorted[0],sorted.at(-1)]);
    for(const point of sorted){
      const key=Math.min(bucketCount-1,Math.floor((Date.parse(point.time)-start)/(span||1)*bucketCount));
      if(!buckets.has(key))buckets.set(key,{min:point,max:point});
      const bucket=buckets.get(key);
      if(point.rssi<bucket.min.rssi)bucket.min=point;
      if(point.rssi>bucket.max.rssi)bucket.max=point;
    }
    for(const bucket of buckets.values()){chosen.add(bucket.min);chosen.add(bucket.max);}
    return [...chosen].sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  }
  function node(tag,text,attributes={}){
    const item=['svg','circle','rect','text','line'].includes(tag)?
      document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
    if(text!==null)item.textContent=text;
    for(const [name,value] of Object.entries(attributes))item.setAttribute(name,String(value));
    return item;
  }
  function svg(label){return node('svg',null,{viewBox:'0 0 640 170',role:'group','aria-label':label});}
  function caption(value){return value===null?'—':value.toFixed(1);}
  function render(container,items,{onSelect=()=>{},selected=null,empty='No qualified receive samples in this view.'}={}){
    container.replaceChildren();
    if(!items.length){container.appendChild(node('p',empty));return;}
    const groups=new Map();
    for(const point of items){
      const key=point.direction+'|'+(point.seriesKey||'');
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(point);
    }
    for(const entries of groups.values()){
      const group=entries.filter(p=>valid(p.rssi)),direction=entries[0].direction;
      if(!group.length)continue;
      const stats=statistics(group),panel=node('section',null,{class:'signal-panel'});
      panel.appendChild(node('h3',(direction==='downlink'?'Fixed → mobile':'Mobile → fixed')+(group[0].seriesLabel?' · '+group[0].seriesLabel:'')));
      panel.appendChild(node('p',`${stats.count} receive samples · Median ${caption(stats.median)} · 10th percentile ${caption(stats.p10)} · Range ${caption(stats.min)}–${caption(stats.max)} / 100`,{class:'signal-stats'}));
      const plotted=timelinePoints(group),chart=svg('Receive RSSI over UTC time; select a measured point to inspect it');
      for(const level of [0,50,100]){
        const y=135-level*1.1;
        chart.appendChild(node('line',null,{x1:38,x2:625,y1:y,y2:y,stroke:'#cbd5e1'}));
        chart.appendChild(node('text',String(level),{x:5,y:y+4,'font-size':12,fill:'#475569'}));
      }
      const times=plotted.map(p=>Date.parse(p.time)),start=times[0],end=times.at(-1);
      for(const point of plotted){
        const x=end===start?330:38+(Date.parse(point.time)-start)/(end-start)*587,y=135-point.rssi*1.1;
        const dot=node('circle',null,{cx:x,cy:y,r:point.key===selected?6:3.5,fill:color(point.rssi),
          stroke:point.key===selected?'#e11d48':'#152431','stroke-width':point.key===selected?2:0.5,
          tabindex:0,role:'button','aria-label':`${point.time}: relative RSSI ${point.rssi}; select measurement`});
        dot.onclick=()=>onSelect(point.key);
        dot.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(point.key);}};
        chart.appendChild(dot);
      }
      if(plotted.length){
        chart.appendChild(node('text',new Date(start).toISOString().slice(11,23)+' UTC',{x:38,y:160,'font-size':12,fill:'#475569'}));
        chart.appendChild(node('text',new Date(end).toISOString().slice(11,23)+' UTC',{x:625,y:160,'text-anchor':'end','font-size':12,fill:'#475569'}));
      }
      const figure=node('figure',null);figure.appendChild(chart);
      const startDate=plotted.length?new Date(start).toISOString().slice(0,10):'',endDate=plotted.length?new Date(end).toISOString().slice(0,10):'';
      const dates=startDate?(startDate===endDate?startDate:startDate+' to '+endDate)+' UTC · ':'';
      figure.appendChild(node('figcaption',`RSSI over time · ${dates}${plotted.length} of ${group.length} samples shown. Large series retain weakest/strongest observations per time bucket; no line joins gaps.`));
      panel.appendChild(figure);
      const histogram=svg('Distribution of receive RSSI, ten equal relative-strength bins'),peak=Math.max(1,...stats.bins);
      for(let index=0;index<10;index++){
        const height=stats.bins[index]/peak*110,x=38+index*58;
        histogram.appendChild(node('rect',null,{x,y:135-height,width:48,height,fill:color(index*10+5)}));
        histogram.appendChild(node('text',String(stats.bins[index]),{x:x+24,y:Math.max(16,129-height),'text-anchor':'middle','font-size':12,fill:'#152431'}));
        histogram.appendChild(node('text',`${index*10}–${index<9?'<':''}${index*10+10}`,{x:x+24,y:157,'text-anchor':'middle','font-size':10,fill:'#475569'}));
      }
      const distribution=node('figure',null);distribution.appendChild(histogram);
      distribution.appendChild(node('figcaption','Receive sample counts by relative RSSI. All samples contribute; 100 is included in the final bin.'));
      panel.appendChild(distribution);container.appendChild(panel);
    }
  }
  return {color,valid,statistics,timelinePoints,render};
})();

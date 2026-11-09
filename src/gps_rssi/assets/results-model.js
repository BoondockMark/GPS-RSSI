/* Completed-run filters use the browser's calendar, not UTC date truncation. */
(function(root){
  'use strict';
  function localDay(value){const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';return date.getFullYear()+'-'+String(date.getMonth()+1).padStart(2,'0')+'-'+String(date.getDate()).padStart(2,'0');}
  function radioKey(radio){return String(radio.address||radio.id).replace(/[:-]/g,'').toLowerCase();}
  function recordedAt(row){return row.quality?.first_sample_at||row.created_at;}
  function filtered(rows,filters={}){
    const query=(filters.query||'').trim().toLocaleLowerCase();
    if(filters.from&&filters.to&&filters.from>filters.to)return [];
    return rows.filter(row=>{
      const day=localDay(recordedAt(row));
      return (!query||row.name.toLocaleLowerCase().includes(query))&&(!filters.type||row.sweep_type===filters.type)&&
        (!filters.radio||row.radios.some(r=>radioKey(r)===filters.radio))&&
        (!filters.from||(day&&day>=filters.from))&&(!filters.to||(day&&day<=filters.to));
    }).sort((a,b)=>Date.parse(recordedAt(b))-Date.parse(recordedAt(a))||a.name.localeCompare(b.name)||a.id.localeCompare(b.id));
  }
  function duration(seconds){if(!Number.isFinite(seconds))return 'Unavailable';const total=Math.round(Math.max(0,seconds)),hours=Math.floor(total/3600),minutes=Math.floor(total%3600/60),rest=total%60;return hours?`${hours}h ${minutes}m`:minutes?`${minutes}m ${rest}s`:`${rest}s`;}
  function gps(quality){if(!quality)return 'Unavailable';return quality.gps_coverage_percent===null?'No mobile RX':`${quality.gps_coverage_percent}% · ${quality.gps_points}/${quality.mobile_receive_samples}`;}
  function mixed(rows){return rows.length===2&&rows[0].sweep_type!==rows[1].sweep_type;}
  const api={localDay,radioKey,recordedAt,filtered,duration,gps,mixed};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.ResultsModel=api;
})(globalThis);

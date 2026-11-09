const test=require('node:test'),assert=require('node:assert/strict');
const model=require('../src/gps_rssi/assets/live-survey-model.js');
const statusModel=require('../src/gps_rssi/assets/workspace-status.js');
const now=Date.parse('2026-11-09T12:00:10Z');
const p=(seconds,extra={})=>({id:seconds,session:'a',time:new Date(now+seconds*1000).toISOString(),lat:33.96,lon:-117.95,...extra});
const sample={recording_session_id:'a',received_at:new Date(now).toISOString(),rssi_value:0,rssi_unit:'benshi_relative_0_100',is_in_rx:true,is_in_tx:false,gps_locked:true};
const row=()=>({profile:{id:'a',name:'Vehicle',role:'mobile',gps:true,telemetry_max_age_seconds:30},job:{kind:'record',state:'running',created_at:new Date(now-60000).toISOString(),result:{recording_session_id:'a',state:'recording',latest_telemetry:sample,reported_at:new Date(now).toISOString()}},latest:sample,latest_fix:{...p(0),position_time:new Date(now).toISOString()},records:[p(0)]});
const present=(r,extra={})=>model.presentation(r,{now,workspaceAt:now,mapAt:now,statusModel,...extra});
test('short observed fixes connect without interpolation',()=>assert.equal(model.segments([p(0),p(10),p(20)]).length,2));
test('missing GPS, reconnect, identity change, long gaps and jumps break lines',()=>{
 for(const middle of [p(10,{lat:null}),p(10,{break_before:true}),p(40),p(10,{session:'b'}),p(10,{lon:0})])assert.equal(model.segments([p(0),middle]).length,0);
 assert.equal(model.segments([p(0),p(0)]).length,0);
});
test('radio colors are distinct and independent of input ordering',()=>{const ids=Array.from({length:20},(_,i)=>'radio'+i);assert.equal(new Set(Object.values(model.colors(ids))).size,20);assert.deepEqual(model.colors(ids),model.colors([...ids].reverse()));});
test('zero receive RSSI is current and preserved',()=>{const state=present(row());assert.equal(state.rssi,0);assert.equal(state.fixCurrent,true);});
test('workspace or map update loss clears live values while retaining history',()=>{
 for(const options of [{workspaceAt:now-10000},{mapAt:now-10000},{mapAt:0}]){const state=present(row(),options);assert.equal(state.rssi,null);assert.equal(state.fixCurrent,false);}
});
test('stopped, reconnecting and transmitting runs never present old RX as current',()=>{
 for(const [state,phase] of [['stopped','recording'],['running','reconnecting'],['running','transmitting']]){const r=row();r.job.state=state;r.job.result.state=phase;if(phase==='transmitting')r.job.kind='transmit';const result=present(r);assert.equal(result.rssi,null);assert.equal(result.fixCurrent,false);}
});
test('lost GPS leaves the last position historical',()=>{const r=row();r.records.push(p(1,{lat:null}));r.gps_reason='no_gps_lock';assert.equal(present(r).fixCurrent,false);assert.match(present(r).gps,/no gps lock/);});
test('stale fix, future clock, and unreadable data do not become current GPS',()=>{
 for(const change of [r=>r.latest_fix.position_time=new Date(now-11000).toISOString(),r=>r.latest_fix.position_time=new Date(now+10000).toISOString(),r=>r.error='unavailable']){const r=row();change(r);assert.equal(present(r).fixCurrent,false);}
});
test('fixed receivers have no invented mobile position',()=>{const r=row();r.profile.role='fixed';r.latest_fix=null;assert.match(present(r).gps,/Fixed receiver/);assert.equal(present(r).fixCurrent,false);});

test('another recording identity is never presented as the current sample',()=>{const r=row();r.latest={...r.latest,recording_session_id:'earlier'};assert.equal(present(r).rssi,null);assert.equal(present(r).fixCurrent,false);});

test('map update failure preserves independently confirmed radio phase',()=>{const state=present(row(),{mapAt:now-10000});assert.equal(state.connection.code,'recording');assert.equal(state.rssi,null);assert.equal(present(row(),{workspaceAt:now-10000}).connection.code,'workspace_lost');});

const test=require('node:test');
const assert=require('node:assert/strict');
process.env.TZ='America/Los_Angeles';
const model=require('../src/gps_rssi/assets/results-model.js');
const row=(id,name,type,at,address='AA:BB:CC:DD:EE:01')=>({id,name,sweep_type:type,created_at:at,quality:{first_sample_at:at},radios:[{id:'radio.toml',name:'Vehicle',address}]});
const runs=[row('a','East Ridge','passive','2026-11-09T07:30:00Z'),row('b','West ridge','active','2026-11-09T08:30:00Z','AA-BB-CC-DD-EE-02'),row('c','Earlier test','passive','2026-11-07T18:00:00Z')];
test('recorded dates use local calendar boundaries rather than UTC truncation',()=>{
 assert.equal(model.localDay(runs[0].created_at),'2026-11-08');
 assert.deepEqual(model.filtered(runs,{from:'2026-11-08',to:'2026-11-08'}).map(r=>r.id),['a']);
});
test('name search is case-insensitive and composes with sweep type',()=>{
 assert.deepEqual(model.filtered(runs,{query:' RIDGE ',type:'passive'}).map(r=>r.id),['a']);
});
test('radio filtering recognizes the same address across naming formats',()=>{
 const alias=row('d','Second radio run','active','2026-11-09T09:30:00Z','aa-bb-cc-dd-ee-01');
 assert.deepEqual(model.filtered([...runs,alias],{radio:model.radioKey(runs[0].radios[0])}).map(r=>r.id),['d','a','c']);
});
test('date bounds are inclusive and reversed bounds produce no matches',()=>{
 assert.equal(model.filtered(runs,{from:'2026-11-09',to:'2026-11-08'}).length,0);
 assert.deepEqual(model.filtered(runs,{from:'2026-11-09'}).map(r=>r.id),['b']);
});
test('sorting uses recorded dates, with creation dates for empty runs',()=>{
 const empty={...runs[0],id:'empty',quality:null,created_at:'2026-11-10T18:00:00Z'};
 assert.deepEqual(model.filtered([...runs,empty]).map(r=>r.id),['empty','b','a','c']);
});
test('zero duration and zero percent GPS are preserved',()=>{
 assert.equal(model.duration(0),'0s');
 assert.equal(model.duration(null),'Unavailable');
 assert.equal(model.duration(125),'2m 5s');
 assert.equal(model.gps({gps_coverage_percent:0,gps_points:0,mobile_receive_samples:3}),'0% · 0/3');
});
test('fixed-only and unavailable results do not invent zero GPS coverage',()=>{
 assert.equal(model.gps({gps_coverage_percent:null}),'No mobile RX');
 assert.equal(model.gps(null),'Unavailable');
});
test('mixed passive and active comparisons remain distinguishable',()=>{
 assert.equal(model.mixed([runs[0],runs[1]]),true);
 assert.equal(model.mixed([runs[0],runs[2]]),false);
 assert.equal(model.mixed([runs[0]]),false);
});

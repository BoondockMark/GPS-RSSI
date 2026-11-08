const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={};vm.createContext(context);vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../src/gps_rssi/assets/route-analysis.js'),'utf8'),context);
const A=context.RouteAnalysis;
const row=(id,lat=34,seconds=id*5,extra={})=>({key:String(id),session:'a',lat,lon:-118,time:new Date(Date.UTC(2026,0,1,0,0,seconds)).toISOString(),channel:1,frequency_hz:146520000,rssi:50,...extra});
test('observed distance joins only plausible consecutive positions in a session',()=>{
  const routes=A.routes([row(0),row(1,34.001),row(2,34.002),row(3,34.003,15,{session:'b'})]);
  assert.equal(routes.length,2);assert.equal(routes[0].segments.length,2);assert.equal(routes[1].segments.length,0);
  assert.ok(Math.abs(routes[0].distance-222.39)<1);
});
test('missing GPS, pauses, time duplicates, channel changes and jumps break route continuity',()=>{
  for(const middle of [row(1,null),row(1,34.001,100),row(1,34.001,0),row(1,34.001,5,{channel:2}),row(1,35)]){
    const route=A.routes([row(0),middle])[0];
    assert.equal(route.segments.length,0);
    assert.equal(route.distance,0);
  }
  assert.equal(A.routes([row(0),row(1,null),row(2,34.002)])[0].segments.length,0);
  assert.equal(A.routes([row(0),row(1,34.001,5,{frequency_hz:147000000})])[0].segments.length,0);
});
test('routes sort each session chronologically and preserve selected keys',()=>{
  const route=A.routes([row(2,34.002),row(0),row(1,34.001)])[0];
  assert.deepEqual(Array.from(route.points,p=>p.key),['0','1','2']);
  assert.equal(route.points[2].distance,route.distance);
});
test('bounded route samples include both endpoints',()=>{
  const points=Array.from({length:1000},(_,i)=>row(i));
  const sampled=A.sample(points);
  assert.equal(sampled.length,96);assert.equal(sampled[0].key,'0');assert.equal(sampled.at(-1).key,'999');
});
test('spherical path follows endpoints across longitude wrapping',()=>{
  const p=A.path({lat:0,lon:179.9},{lat:0,lon:-179.9});
  assert.equal(p.length,65);assert.ok(Math.abs(p[0].lon-179.9)<1e-8);assert.ok(Math.abs(p.at(-1).lon+179.9)<1e-8);
  assert.ok(Math.abs(p[32].lon)>179.99);
});
test('terrain clearance distinguishes sampled ridges, curvature, missing data and antenna height',()=>{
  const points=A.path({lat:34,lon:-118},{lat:34.01,lon:-118});
  const ground=Array(65).fill(100);
  const clear=A.clearance(points,ground,10,2);
  assert.equal(clear.status,'sampled-clear');assert.ok(clear.worst.clearance>0);assert.ok(clear.spacing>17&&clear.spacing<18);
  ground[32]=150;
  assert.equal(A.clearance(points,ground,10,2).status,'obstructed');
  ground[32]=null;assert.equal(A.clearance(points,ground,10,2).status,'unknown');
  assert.equal(A.clearance(points,Array(65).fill(0),0,0).status,'obstructed');
  assert.equal(A.clearance(points,Array(64).fill(0),10,10).status,'unknown');
  assert.equal(A.clearance(points,Array(65).fill(0),NaN,10).status,'unknown');
  assert.equal(A.clearance(points,Array(65).fill(0),10,-1).status,'unknown');
});

test('Fresnel radius matches the standard midpoint formula and scales with frequency',()=>{
  const points=A.path({lat:0,lon:0},{lat:10000/6371000*180/Math.PI,lon:0});
  const low=A.clearance(points,Array(65).fill(0),20,20,{frequencyHz:146520000});
  assert.equal(low.status,'sampled-clear');assert.equal(low.fresnelStatus,'intruded');
  assert.ok(Math.abs(low.samples[32].fresnelRadius-Math.sqrt((299792458/146520000)*2500))<1e-8);
  assert.equal(low.samples[0].fresnelRadius,0);assert.equal(low.samples.at(-1).fresnelRadius,0);
  assert.ok(Math.abs(low.samples[32].fresnelLower-(20-.6*low.samples[32].fresnelRadius))<1e-8);
  const high=A.clearance(points,Array(65).fill(0),20,20,{frequencyHz:1465200000});
  assert.equal(high.fresnelStatus,'sampled-clear');
  assert.equal(A.clearance(points,Array(65).fill(0),100,100,{frequencyHz:146520000}).fresnelStatus,'sampled-clear');
});
test('missing frequency and missing terrain cannot produce a clear Fresnel result',()=>{
  const points=A.path({lat:34,lon:-118},{lat:34.01,lon:-118});
  for(const frequencyHz of [null,undefined,0,-1,NaN,Infinity,Number.MIN_VALUE,'146520000']){
    const result=A.clearance(points,Array(65).fill(0),10,10,{frequencyHz});
    assert.equal(result.fresnelStatus,'unknown');assert.equal(result.samples[32].fresnelRadius,null);
  }
  assert.equal(A.clearance(points,Array(65).fill(null),10,10,{frequencyHz:146520000}).fresnelStatus,'unknown');
});
const measured=(id,session,lat=34,extra={})=>row(id,lat,id*5,{session,rssi_unit:'benshi_relative_0_100',direction:'downlink',...extra});
test('location matching uses proximity across opposite travel orders and never reuses B',()=>{
  const a=[measured(0,'a'),measured(1,'a',34.001),measured(2,'a',34.002)];
  const b=[measured(3,'b',34.002,{rssi:70}),measured(4,'b',34.001,{rssi:20}),measured(5,'b',34,{rssi:80})];
  const result=A.matchLocations(a,b);
  assert.deepEqual(Array.from(result.pairs,p=>[p.a.key,p.b.key,p.delta]),[['0','5',30],['1','4',-30],['2','3',20]]);
  const parked=A.matchLocations([measured(0,'a'),measured(1,'a')],[measured(2,'b')]);
  assert.equal(parked.pairs.length,1);
});
test('route distance or time similarity never matches distant locations',()=>{
  assert.equal(A.matchLocations([measured(0,'a')],[measured(1,'b',35)]).pairs.length,0);
  assert.equal(A.matchLocations([measured(0,'a')],[measured(1,'b',34.0002)],{radius:20}).pairs.length,0);
  assert.equal(A.matchLocations([measured(0,'a')],[measured(1,'b',34.0002)],{radius:25}).pairs.length,1);
});
test('matching excludes incompatible directions, missing GPS, unsupported units and same-session pairs',()=>{
  const a=[measured(0,'a')];
  for(const b of [measured(1,'a'),measured(1,'b',null),measured(1,'b',34,{rssi_unit:'dBm'}),measured(1,'b',34,{direction:'uplink'}),measured(1,'b',34,{rssi:NaN})])assert.equal(A.matchLocations(a,[b]).pairs.length,0);
});
test('frequency matching is strict by default and can be explicitly relaxed',()=>{
  for(const frequency_hz of [null,undefined,147000000]){
    const a=[measured(0,'a')],b=[measured(1,'b',34,{frequency_hz})];
    assert.equal(A.matchLocations(a,b).pairs.length,0);
    assert.equal(A.matchLocations(a,b,{sameFrequency:false}).pairs.length,1);
  }
});
test('location matching works across the antimeridian and close to the pole',()=>{
  assert.equal(A.matchLocations([measured(0,'a',0,{lon:179.99995})],[measured(1,'b',0,{lon:-179.99995})]).pairs.length,1);
  assert.equal(A.matchLocations([measured(0,'a',89.99999,{lon:0})],[measured(1,'b',89.99999,{lon:180})]).pairs.length,1);
});
test('matching bounds input size explicitly, rejects invalid radii, and is deterministic',()=>{
  const a=Array.from({length:3},(_,i)=>measured(i,'a'));
  const b=Array.from({length:3},(_,i)=>measured(i+3,'b'));
  const result=A.matchLocations(a,b,{limit:2});
  assert.equal(result.truncated,true);assert.equal(result.eligibleA,3);assert.equal(result.consideredA,2);assert.equal(result.pairs.length,2);
  assert.deepEqual(Array.from(result.pairs,p=>p.b.key),['3','4']);
  for(const radius of [NaN,0,201])assert.throws(()=>A.matchLocations(a,b,{radius}));
});

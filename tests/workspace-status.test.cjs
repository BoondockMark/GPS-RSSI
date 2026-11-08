const test = require('node:test');
const assert = require('node:assert/strict');
const {radioStatus} = require('../src/gps_rssi/assets/workspace-status.js');
const now = Date.parse('2026-11-08T12:00:00Z');
const stamp = (seconds = 0) => new Date(now + seconds * 1000).toISOString();
const telemetry = (seconds = 0) => ({received_at:stamp(seconds),rssi:50,rssi_unit:'benshi_relative_0_100',is_in_rx:true,is_in_tx:false,gps_locked:true,gps_recorded:true});
const profile = {id:'vehicle.toml',gps:true,telemetry_max_age_seconds:33,connect_timeout_seconds:60,latest:{...telemetry(-20),rssi_value:50}};
const job = (result = {}, extra = {}) => ({id:'job',kind:'record',state:'running',created_at:stamp(-10),result,...extra});
const report = (state, extra = {}) => ({state,reported_at:stamp(-1),expires_at:stamp(30),...extra});
const present = (operation, extra = {}, p = profile) => radioStatus(p,operation,{now,workspaceAt:now,...extra});

test('a newly running process cannot claim an older database sample is live', () => {
  const status = present(job());
  assert.equal(status.code,'connecting');
  assert.equal(status.currentSample,null);
  assert.equal(status.historical,true);
  assert.equal(status.displaySample.rssi,50);
});
test('an explicit connected state waits for a complete sample', () => {
  const status = present(job(report('connected')));
  assert.equal(status.label,'Waiting for telemetry');
  assert.equal(status.currentSample,null);
});
test('a current sample from this recorder confirms recording', () => {
  const status = present(job(report('recording',{latest_telemetry:telemetry(-1)})));
  assert.equal(status.code,'recording');
  assert.equal(status.currentSample.rssi,50);
  assert.equal(status.historical,false);
});
test('a retry suppresses live readings even if the last sample was recent', () => {
  const status = present(job(report('reconnecting',{latest_telemetry:telemetry(-1),error:'status timed out',error_stage:'status',retry_at:stamp(5)})));
  assert.equal(status.label,'Reconnecting');
  assert.equal(status.currentSample,null);
  assert.match(status.detail,/5s/);
  assert.match(status.guidance,/retries automatically/);
});
test('GPS and channel failures have specific corrective guidance', () => {
  assert.match(present(job(report('reconnecting',{error_stage:'position'}))).guidance,/position request failed/);
  assert.match(present(job(report('reconnecting',{error_stage:'channel'}))).guidance,/channel read failed/);
});
test('an expired producer report cannot confirm a healthy connection', () => {
  const status = present(job(report('recording',{expires_at:stamp(-1),latest_telemetry:telemetry(-1)})));
  assert.equal(status.code,'stale');
  assert.equal(status.currentSample,null);
});
test('a future or stale sample is never treated as current', () => {
  for (const age of [10,-40]) {
    const status = present(job(report('recording',{latest_telemetry:telemetry(age)})));
    assert.equal(status.code,'stale');
    assert.equal(status.currentSample,null);
  }
});
test('workspace disconnection overrides a recent recording or TX report', () => {
  for(const operation of [job(report('recording',{latest_telemetry:telemetry(-1)})),job(report('transmitting'),{kind:'transmit'})]) {
    const status = present(operation,{workspaceAt:now-7000});
    assert.equal(status.code,'workspace_lost');
    assert.equal(status.currentSample,null);
    assert.match(status.detail,/unknown/);
  }
});
test('stopping a transmitter is distinct from verified stop', () => {
  const status = present(job({stop_verified:true,state:'receiving'},{state:'stopping',kind:'transmit'}));
  assert.equal(status.label,'Verifying TX stop');
  assert.match(status.guidance,/not yet a stopped/);
});
test('unknown operations require explicit review rather than an automatic restart', () => {
  const status = present(job({}, {state:'unknown',kind:'transmit'}));
  assert.equal(status.code,'unknown');
  assert.equal(status.action,'activity');
  assert.match(status.guidance,/acknowledging/);
});
test('automatic waiting and receiving use separate labels and limited sample age', () => {
  const waiting = present(job({automatic:true,state:'waiting',recording:true},{kind:'transmit'}));
  assert.equal(waiting.label,'Armed · waiting');
  assert.equal(waiting.action,'field');
  const receiving = present(job({automatic:true,state:'receiving',latest_telemetry:telemetry(-1)},{kind:'transmit'}));
  assert.equal(receiving.label,'Listening');
  assert.equal(receiving.currentSample.rssi,50);
  const old = present(job({automatic:true,state:'receiving',latest_telemetry:telemetry(-6)},{kind:'transmit'}));
  assert.equal(old.currentSample,null);
});
test('transmission suppresses receive telemetry and points to halt controls', () => {
  const status = present(job({automatic:true,state:'transmitting',latest_telemetry:telemetry(-1)},{kind:'transmit'}));
  assert.equal(status.code,'transmitting');
  assert.equal(status.tone,'tx');
  assert.equal(status.currentSample,null);
  assert.match(status.guidance,/Halt automatic mode/);
});
test('a stopped recorder retains history without claiming it is connected', () => {
  const status = present(null,{lastJob:job({}, {state:'stopped'})});
  assert.equal(status.label,'Stopped');
  assert.equal(status.currentSample,null);
  assert.equal(status.displaySample.rssi,50);
});
test('failed preflight explains what to correct without retrying RF', () => {
  const lastJob=job({ready:false,checks:[{state:'unavailable',detail:'Audio not paired'}]},{state:'completed',kind:'preflight'});
  const status=present(null,{lastJob});
  assert.equal(status.detail,'Audio not paired');
  assert.equal(status.action,'field');
});
test('missing GPS lock does not misclassify an otherwise connected recorder', () => {
  const status=present(job(report('recording',{latest_telemetry:{...telemetry(-1),gps_locked:false}})));
  assert.equal(status.label,'Recording');
  assert.match(status.guidance,/fresh GPS fix/);
});
test('a saved profile does not imply a connected radio', () => {
  assert.equal(present(null).label,'Not connected');
  assert.equal(present(null,{bluetooth:false}).label,'Tools unavailable');
});
test('historical SQLite boolean integers retain receive and GPS meaning', () => {
  const stored={...profile,latest:{...profile.latest,is_in_rx:1,is_in_tx:0,gps_locked:1}};
  const status=present(null,{},stored);
  assert.equal(status.displaySample.is_in_rx,true);
  assert.equal(status.displaySample.is_in_tx,false);
  assert.equal(status.displaySample.gps_locked,true);
  assert.equal(status.historical,true);
});
test('zero receive RSSI is a valid current measurement', () => {
  const status=present(job(report('recording',{latest_telemetry:{...telemetry(-1),rssi:0}})));
  assert.equal(status.currentSample.rssi,0);
  assert.equal(status.code,'recording');
});
test('unverified stop takes priority over a newly invalid setup file', () => {
  const status=present(job({}, {state:'unknown',kind:'transmit'}),{},{...profile,error:'Invalid TOML'});
  assert.equal(status.code,'unknown');
  assert.equal(status.action,'activity');
});

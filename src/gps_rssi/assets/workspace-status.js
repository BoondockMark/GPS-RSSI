/* Shared presentation rules: a running process alone never proves a radio link. */
(function (root) {
  'use strict';
  const ongoing = new Set(['running', 'stopping', 'unknown']);
  const radioKinds = new Set(['record', 'transmit', 'capture', 'preflight', 'services']);
  function normalize(value, producer = false) {
    if (!value) return null;
    const rssi = value.rssi_value ?? value.rssi;
    return {
      received_at: value.received_at,
      rssi, rssi_unit: value.rssi_unit || (producer ? 'benshi_relative_0_100' : null),
      is_in_rx: value.is_in_rx === true || value.is_in_rx === 1,
      is_in_tx: value.is_in_tx === true || value.is_in_tx === 1,
      gps_locked: value.gps_locked === true || value.gps_locked === 1, gps_recorded: value.gps_recorded
    };
  }
  function recent(sample, now, seconds, startedAt) {
    if (!sample) return false;
    const at = Date.parse(sample.received_at), start = Date.parse(startedAt);
    return Number.isFinite(at) && Number.isFinite(start) && at >= start &&
      now - at >= -2000 && now - at <= seconds * 1000;
  }
  function workspaceFresh(now, at) {
    return Number.isFinite(at) && now - at >= -2000 && now - at <= 6500;
  }
  function reportFresh(report, now) {
    if (!report.reported_at) return true; // Older audio reports have no heartbeat.
    const at = Date.parse(report.reported_at);
    if (!Number.isFinite(at) || now - at < -2000) return false;
    if (report.expires_at) return now <= Date.parse(report.expires_at);
    return now - at <= 15000;
  }
  function recovery(report) {
    if (report.error_stage === 'position') return 'The position request failed. Keep the radio nearby while the recorder reconnects; saved RSSI is retained.';
    if (report.error_stage === 'channel') return 'The channel read failed. The recorder will use a fresh connection; readings with unknown frequency remain labeled.';
    if (report.cleanup_error) return 'The Bluetooth disconnect reported an error. Close other radio apps and check the radio before using it again.';
    return 'Check radio power, Bluetooth pairing, and distance. Close other radio-control apps. The recorder retries automatically; you can stop it here.';
  }
  function radioStatus(profile, job, options = {}) {
    const now = options.now ?? Date.now(), workspaceAt = options.workspaceAt ?? now;
    const lastJob = options.lastJob, report = job?.result || {};
    const saved = normalize(profile.latest), producer = normalize(report.latest_telemetry, true);
    const displaySample = producer && (!saved || Date.parse(producer.received_at) >= Date.parse(saved.received_at)) ? producer : saved;
    function status(code, label, tone, detail, guidance, action = null, currentSample = null) {
      return {code, label, tone, detail, guidance, action, currentSample,
        displaySample: currentSample || displaySample, historical: !currentSample};
    }
    if (!workspaceFresh(now, workspaceAt)) return status('workspace_lost', 'Status unavailable', 'attention',
      'Cannot reach the local workspace. The current radio state is unknown.',
      job ? 'Wait for reconnection. Existing recordings or transmissions may still be running.' :
        'Reconnect to the local workspace to read current status.', job ? 'activity' : null);
    if (job?.state === 'unknown') return status('unknown', 'Check radio', 'failed', job.message || 'The prior operation did not finish cleanup.',
      'Check the radio and confirm the prior process has ended. Review the operation before acknowledging that it is stopped.', 'activity');
    if (job?.state === 'stopping') return status('stopping', job.kind === 'transmit' ? 'Verifying TX stop' : 'Stopping', 'attention',
      job.kind === 'transmit' ? 'Stop requested; waiting for radio TX verification.' : 'Stop requested; disconnecting and saving data.',
      'Allow cleanup to finish. This is not yet a stopped operation.', 'activity');
    if (profile.error) return status('setup_error', job ? 'Setup changed' : 'Needs setup', 'failed', profile.error,
      job ? 'The managed operation is still active. Stop it before correcting the saved radio setup.' :
        'Correct the saved setup file ' + profile.id + ', or add a replacement setup. Saved recordings are retained.', job ? 'activity' : 'setup');
    if (job?.state === 'running') {
      if (!reportFresh(report, now)) return status('stale', 'Status stale', 'attention',
        'This operation is still running, but its status report is overdue.',
        'Check the radio and operation details. Stop the operation if it does not recover; do not assume the link is healthy.', 'activity');
      if (job.kind === 'record') {
        if (report.state === 'reconnecting') {
          const retry = Date.parse(report.retry_at), seconds = Math.max(0, Math.ceil((retry - now) / 1000));
          const detail = (report.error || 'The recorder lost its radio connection.') +
            (Number.isFinite(retry) ? (seconds ? ` Next attempt in ${seconds}s.` : ' Retrying now…') : ' Closing the prior connection before retrying.');
          return status('reconnecting', 'Reconnecting', 'attention', detail, recovery(report), 'activity');
        }
        if (['stopping', 'stopped'].includes(report.state)) return status('stopping', 'Stopping', 'attention',
          'The recorder is ending its connection.', 'Wait for the operation to finish. Saved data is retained.', 'activity');
        if (report.state === 'failed') return status('failed', 'Recording failed', 'failed',
          report.error || job.message, 'Cleanup is finishing. Review the error before starting again.', 'activity');
        const sample = producer || (!report.state ? saved : null);
        if (recent(sample, now, profile.telemetry_max_age_seconds || 30, job.created_at) &&
            (!report.state || report.state === 'recording')) {
          const detail = sample.is_in_tx ? 'Live radio TX state; this is not a receive-signal measurement.' :
            sample.is_in_rx ? 'Receiving current radio telemetry and saving samples.' : 'Radio connected; saving idle-state samples.';
          const hint = profile.gps && !sample.gps_locked ? 'RSSI is saved. Mobile map points need a fresh GPS fix; check the radio has a clear sky view.' :
            profile.gps && sample.gps_recorded === false ? 'GPS reports lock, but this sample has no recorded position. Check operation details.' : 'Open Live view to follow the recording.';
          return status('recording', 'Recording', 'good', detail, hint, 'live', sample);
        }
        if (report.state === 'recording') return status('stale', 'Telemetry stale', 'attention',
          'No recent sample confirms the current connection.', recovery(report), 'activity');
        if (report.state === 'connected') return status('waiting', 'Waiting for telemetry', 'attention',
          'Bluetooth connected; waiting for the first complete sample.', 'Keep the radio nearby. Open operation details if no sample arrives.', 'activity');
        const age = now - Date.parse(job.created_at);
        if (!report.state && age > ((profile.connect_timeout_seconds || 60) + 40) * 1000)
          return status('stale', 'Status unavailable', 'attention', 'The recorder has not produced current telemetry or a status report.', recovery(report), 'activity');
        return status('connecting', 'Connecting', 'attention',
          `Opening the radio connection${report.attempt > 1 ? ' · attempt ' + report.attempt : ''}.`,
          'Turn on the radio and enable Bluetooth. Close other control apps; no current connection is confirmed yet.', 'activity');
      }
      if (job.kind === 'transmit') {
        const field = report.recording || (job.title?.startsWith('Field survey · ')||job.title?.startsWith('Active sweep · '));
        const action = field ? 'field' : 'audio';
        if (report.state === 'transmitting') return status('transmitting', 'Transmitting', 'tx',
          report.automatic ? 'Sending the scheduled 1 kHz tone.' : 'Sending the bounded test tone.',
          report.automatic ? 'Use Halt automatic mode to stop the repeating cycle.' : 'Use Stop tone to request cleanup.', action);
        if (report.automatic && ['waiting', 'receiving'].includes(report.state)) {
          const sample = recent(producer, now, 5, job.created_at) ? producer : null;
          return status(report.state, report.state === 'waiting' ? 'Armed · waiting' : 'Listening',
            report.state === 'waiting' ? 'attention' : 'good', report.state === 'waiting' ?
              'Automatic mode is armed for its synchronized first tone.' : 'TX was checked off between tones; the cycle is still armed.',
            field ? 'Open Field survey to see the countdown, clock, and halt control.' : 'The cycle continues until halted. Open Audio tests or use the transmit banner.', action, sample);
        }
        return status('connecting', 'Connecting audio', 'attention', 'Preparing the transmitter connection.',
          'No tone start is confirmed yet. Use the operation stop control if you need to cancel.', action);
      }
      if (job.kind === 'capture') return status(report.state === 'capturing' ? 'capturing' : 'connecting',
        report.state === 'capturing' ? 'Capturing audio' : 'Connecting audio', 'attention',
        report.state === 'capturing' ? 'Receive-only audio capture is in progress.' : 'Opening the receiver audio connection.',
        'RSSI recording is unavailable while this radio is capturing audio.', 'audio');
      return status('checking', 'Checking', 'attention', 'Checking radio or audio support without transmitting.',
        'Wait for this check to finish before using the radio.', job.kind === 'preflight' ? 'field' : 'audio');
    }
    if (lastJob?.state === 'failed') {
      const failed = lastJob.result || {}, checks = failed.checks || [];
      return status('failed', lastJob.kind === 'record' ? 'Recording failed' : 'Needs attention', 'failed',
        failed.error || [...checks].reverse().find(c => c.state === 'unavailable')?.detail || lastJob.message,
        lastJob.kind === 'record' ? 'Review the error, correct the cause, then start recording when ready. Saved samples are retained.' :
          'Review the operation before retrying. RF transmission always requires a new review.', 'activity');
    }
    if (lastJob?.kind === 'preflight' && lastJob.result?.ready === false) return status('preflight', 'Needs attention', 'attention',
      [...(lastJob.result.checks || [])].reverse().find(c => c.state === 'unavailable')?.detail || 'Preflight did not pass.',
      'Open Field survey to correct the readiness check and run it again.', 'field');
    if (options.bluetooth === false) return status('unavailable', 'Tools unavailable', 'attention',
      'Bluetooth support is not installed on this computer.', 'Install the Bluetooth extras, restart the workspace, then check the radio.', 'setup');
    const stopped = lastJob && ['stopped', 'completed', 'interrupted'].includes(lastJob.state) && lastJob.kind !== 'preflight';
    return status(stopped ? 'stopped' : 'configured', stopped ? 'Stopped' : 'Not connected', 'neutral',
      stopped ? 'This radio has no active managed operation. Saved telemetry is historical.' : 'A saved setup does not confirm a radio connection.',
      lastJob?.result?.cleanup_error ? 'The last disconnect reported an error. Check the radio and operation details before starting again.' :
        'Start recording for fresh telemetry, or use Field survey for a reviewed automatic test.', lastJob?.result?.cleanup_error ? 'activity' : null);
  }
  const api = {radioStatus, normalize, workspaceFresh, radioKinds, ongoing};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GpsRssiStatus = api;
})(globalThis);

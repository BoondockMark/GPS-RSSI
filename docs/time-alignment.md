# Aligning mobile and fixed-site logs

This is the proposed design for the next implementation. Combining logs is not
implemented yet. Start with offline analysis of two saved databases, keeping
the input databases unchanged.

## What we are associating

The two radios alternate on simplex. There is no simultaneous pair of receive
RSSI readings to join. Produce two measurement layers:

| Layer | Receive measurement | Location | Supporting opposite-end state |
|---|---|---|---|
| Fixed to mobile | Mobile RX RSSI | Mobile GPS at measurement time | Fixed TX observations |
| Mobile to fixed | Fixed RX RSSI | Mobile GPS at measurement time | Mobile TX observations |

Keep both RSSI values in their original relative units and identify the receiver
model. Do not average or subtract readings from different receivers as if their
scales were calibrated equally. Do not call nearby observations a successful
two-way voice contact.

## Existing data

- `samples.received_at` is the host UTC time when the status reply arrived.
- `raw_json.status_requested_at` records when that status request started.
- `is_in_tx` and `is_in_rx` describe the polled radio state.
- Mobile rows can contain latitude, longitude, and `position_time`, the radio's
  GPS fix time. GPS accuracy remains in `raw_json.position.accuracy`.
- GPS is requested after status, including when the status reports TX. Actual
  availability during TX needs testing on each radio.
- Sessions identify station, role, source, and recording metadata. Connection
  events help identify interruptions.

Use mobile GPS rows regardless of their receive direction: a mobile TX row can
be essential for locating the signal received at the fixed site. The existing
map's mobile-receive filter must not be applied to the GPS track first.

## Timing rules

Synchronize both computers to UTC before recording and maintain synchronization
during long surveys. Matching timezone settings alone is insufficient. Check
clock status before and after the bench test; a ten-hour run may accumulate
drift or encounter a clock adjustment.

Treat the status request/reply interval as a timing bound, not an exact RF
measurement timestamp. Its midpoint is a useful alignment estimate; save the
interval length as a quality indicator. Long requests, reconnects, and clock
discontinuities prevent a confident match. Radio firmware can add further
unknown status/RSSI latency that this interval does not measure.

Permit explicit, recorded clock corrections per input:

`corrected host time = logged host time + configured correction`

For example, a host known to be two seconds fast needs a correction of -2
seconds. Apply it to host request/reply times, not to GPS fix timestamps. A
constant correction cannot repair changing drift; split the affected recording
or reject the affected interval. Do not automatically infer clock correction
from GPS/host differences until GPS timestamp behavior is characterized.

## Proposed matching procedure

1. Select one mobile and one fixed station/session set, restrict measurements
   to live recordings by default, and find their overlapping corrected times.
   Require an unambiguous selection when inputs contain multiple stations.
2. Extract the mobile GPS track. Validate coordinates, fix lock, fix timestamps,
   reported accuracy, and fix age relative to its own observation. Deduplicate
   repeated fix timestamps; flag conflicting positions for the same timestamp.
3. Select each receiver's actual RX/non-TX observations with valid relative RSSI.
   Keep zero-strength and closed-squelch receive samples, retaining their flags.
4. Locate each RX observation using the nearest valid GPS fix by GPS UTC time.
   Start with configurable limits such as a two-second separation and 50-meter
   reported accuracy. Report ties or conflicting fixes as ambiguous. These are
   provisional thresholds to evaluate on bench data, not accuracy guarantees.
5. Check the opposite radio's TX observations independently. A conservative
   association requires TX reports bracketing the receive interval, a short
   configurable gap between them, and no observed contradictory state or
   connection interruption. Exclude observations near uncertain transitions.
   Polling can miss a short release/re-key between samples, so even bracketing
   supports an association rather than proving continuous transmission.
6. Classify results as associated, location-only/unconfirmed, or unmatched with
   a reason. The combined coverage map should default to associated results;
   expose unconfirmed observations separately for diagnosis. Keep original
   receiver sample IDs and session IDs with every result.

Do not interpolate GPS positions in the first version. Nearest-fix assignment is
easier to inspect, and an age limit exposes its limits: at 30 mph, two seconds
corresponds to about 27 meters of travel, in addition to GPS error. Never carry a
position across an outage simply because it is the nearest available record.

Channel IDs are local memory-slot numbers, not frequencies. Confirm both radios'
simplex frequency, bandwidth, power, and test configuration separately. Timing
and overlapping TX/RX flags cannot rule out another transmitter on the channel.

## Outputs and implementation boundaries

Add a read-only alignment module operating on data loaded through
`storage.load_log`. Add a CLI command after the matching rules are tested.
The command should generate a separate result CSV plus an HTML map with both
directions as selectable layers; it should not insert inferred coordinates into
the original fixed-site database.

Each result should include the receiver sample/session, supporting transmitter
sample/session references, GPS sample/session reference, original and corrected
times, applied clock corrections, timing gaps, GPS accuracy, relative RSSI and
unit, direction, receiver identity, and association status/rejection reason.
Include a summary of matched, unconfirmed, rejected, and out-of-overlap rows.

Load consistent read snapshots and use a sorted time index for efficient joins.
Recording sessions, reconnects, and gaps are boundaries; do not silently bridge
them. Preserve a configuration summary so results can be reproduced.

## Bench acceptance

1. Record both computers with synchronized clocks, with the mobile stationary
   outdoors and GPS locked. Alternate deliberate transmissions of about ten
   seconds so the one-second polling has several observations per transmission.
2. Confirm GPS availability during mobile TX and inspect actual request latency,
   fix update rate, TX/RX flag transitions, and RSSI behavior on both receivers.
   Use these observations to set matching and transition thresholds.
3. Repeat while moving slowly. Inspect the two map layers and the recorded
   matching gaps before using them for a long drive.

Automated tests should cover known coordinate matches, explicit clock offsets,
timezones, alternating directions, TX-boundary ambiguity, GPS available only
before/after TX, stale/conflicting fixes, connection gaps, duplicate local sample
IDs across databases, multiple sessions, non-overlapping logs, clock jumps,
replay exclusion, and preservation of both input databases.

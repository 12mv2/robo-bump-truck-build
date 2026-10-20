# Sample data

Author: Colin Rooney · © 2026 Rooney Industries LLC · sample data CC BY 4.0

This page describes five sample runs from the truck and the format of the controller log in them. The runs are not in this repository and are not yet published. They are planned release assets containing logs and probability arrays: no images, no video. They are not needed for the [first ROS checkpoint](../docs/first-checkpoint.md).

## What is in the five runs

All five were recorded indoors, on a blue tape oval. Sizes describe the prepared sample set after scrubbing, not the original run directories. The preparation record reports 1,133 files and 174,337,689 bytes in total <!-- B48: manifest.json -->.

| # | Name | Date | Size | What it shows |
|---|---|---|---|---|
| 1 | Shadow run | 2026-10-09 | 9.5 MB, 9 files <!-- B48: manifest.json --> | The controller ran perception and logging with no actuation: 1765 frames over 118 s <!-- B28 listing -->, state stop_sw throughout. The baseline for the log format. |
| 2 | Short recovery | 2026-10-09 | 14.0 MB, 11 files <!-- B48: manifest.json --> | A short assisted drive, 2686 frames over 179 s <!-- B28 listing -->, with 1 recovery_request and 17 recovery events <!-- B28 listing -->. The smallest run with a complete recovery sequence. |
| 3 | Recovery return | 2026-10-09 | 53.0 MB, 25 files <!-- B48: manifest.json --> | 9628 frames over 642 s <!-- B28 listing -->: 7126 assisted <!-- B28 listing -->, 1879 under human control <!-- B28 listing -->. 17 recovery_request rows and 280 recovery events <!-- B28 listing -->, covering the return to the tape after a departure. |
| 4 | Long sweep | 2026-10-09 | 97.7 MB, 1,085 files <!-- B48: manifest.json --> | A long continuous drive, 16187 frames over 1079 s <!-- B28 listing -->, 14554 of them assisted <!-- B28 listing -->, with a parameter sweep logged as sweep_* rows: 1 sweep plan, 4 stage ends, 3 gain applies <!-- B28 listing -->. Its frames.jsonl alone is 62.7 MB <!-- B48: manifest.json -->. |
| 5 | Manual lap, overhead replay | 2026-10-06 | 3.5 KB, 3 files <!-- B48: manifest.json --> | A human-driven pass round the oval, replayed through the ceiling camera's tracker. It ships only the replay summary, the manifest of the frozen tracker files, and the visual review: no frames.jsonl, no decisions log, no images or video. The run's own review notes say the tracking failed the visual identity checks, so treat it as a negative example for the ceiling camera's tracker, which is described in the [research record](../docs/research/method.md#ceiling-camera-grader). |

The planned release folders are `shadow-run`, `short-recovery`, `recovery-return`, `long-sweep` and `manual-lap-overhead-replay`.

| Folder | Files |
|---|---|
| `shadow-run`, `short-recovery`, `recovery-return`, `long-sweep` | `frames.jsonl`; `probs.npz` (the whole run); `probs_chunk_NNNN.npz` (the same maps in chunks: 4, 6, 20 and 1,080 files) <!-- B48: manifest.json -->; `status.json`; `setup-snapshot.json`; `setup-observed.json` |
| `manual-lap-overhead-replay` | `replay/summary.json`, `frozen-manifest.json`, `visual-review.json` <!-- B48: manifest.json --> |


## Why text only

The full run directories of runs 1 to 4 range from 51 MB to 1.1 GB each <!-- B28 listing -->, and the images in them show the inside of a home. None of that ships.

The rule for the originals is the opposite: offload source recordings, never delete them. The truck's run store reached 102 GB in 370 runs <!-- F:2978 -->. The originals are kept; only the scrubbed text is published.

## Download

No download is available yet. The planned release is named `sample-data-v1`; when published, it must include a manifest giving the sha256 of each file. The counts above describe the earlier preparation record, not a newly verified downloadable release. After a release is available and downloaded, check each file against its manifest, for example:

```
shasum -a 256 frames.jsonl
```

The sample data is licensed under CC BY 4.0 (see [LICENSE](LICENSE)). Attribution: Colin Rooney, Rooney Industries LLC. Code in this repository is MIT.

## How the runs were scrubbed

`scripts/scrub-run.py` copies the text of a run and rewrites paths, hostnames, user names and IP addresses; it never copies images or video. Paths, user names and host names were replaced by tokens before release (`user`, `host`, `0.0.0.0`, `::`, run-relative paths), and numbers were left untouched: the script compares every number bit for bit before and after.

## Reading a run

`frames.jsonl` is one JSON object per line. The `event` key names the row kind; a row with no `event` key is a frame row.

```python
import json
frames, events = [], []
with open("frames.jsonl") as f:
    for line in f:
        row = json.loads(line)
        (events if "event" in row else frames).append(row)
```

The probability maps are float16 arrays. List what a file holds with `numpy.load(path).files`.

Two cautions:

- The controller wrote every frame's timestamp `t` from the Orin's wall clock. The phone's own clock is carried separately (`phone.utc_s`), and `clock_off` in the first frame gives the difference between them.
- The phone app's `session.json` has no field for an encoder failure <!-- F:1142 -->. A session file that looks clean does not show that the recording was clean; check the frame log too.

## Record format

The rows are written by the controller's frame-record function. Types retain the sample preparation record's observations; N means the value can be null. The October sample controller versions have not been checked against the bundled runs 27–31 source here; fields and semantics may differ by version. The telemetry sequence description below is verified only for that bundled snapshot. Fields that appear only under some controller flags are not listed.

### Row kinds

| Kind | Where | Notes |
|---|---|---|
| frame | every run | No `event` key. One per processed camera frame. |
| start | first row | All command-line arguments, setup and recovery parameters, a short git commit prefix. |
| recovery_config, output_owner | a few rows per run | Recovery configuration, and output-owner records. |
| recovery_request, recovery | runs 2 and 3 | A request to recover, and the recovery events that follow. |
| sweep_* kinds (sweep_plan, sweep_start, sweep_resume, sweep_pause, sweep_request, sweep_stage_end, sweep_apply, sweep_complete) | run 4 | The parameter sweep. Run 4 holds sweep_plan, sweep_start, sweep_resume, sweep_stage_end, sweep_apply and sweep_complete; it has no sweep_pause or sweep_request row. |
| end | last row | Row totals and timing summary. |

### Frame row

| Field | Type | Unit and meaning |
|---|---|---|
| n | int | Frame counter, starting at 1. |
| t | float | Orin wall clock, epoch seconds. |
| kept | int | Trail rows kept by the detector, a count. |
| state | str | Controller state: stop_sw, assist, human, recovery_stop, fc_mode_HOLD, heartbeat_stale, recovery, cell_stop. |
| goal | [float, float] or N | Lookahead goal, metres. The axis order is not documented here. |
| kappa_raw | float or N | Curvature of the fitted line, 1/m. |
| kappa_cmd | float or N | Smoothed curvature command, 1/m. |
| steer_norm | float or N | Steer command, normalised, positive to the right. |
| steer_i, steer_i_why | float or N, str or N | Steering integrator term, and the reason for it. |
| pwm | int | Throttle PWM, µs. |
| gain, look | float | Steering gain (unitless) and lookahead distance (m). |
| cruise_cmd, cruise_on, cruise_why, cruise_pwm | int or N, bool, str, int | Cruise throttle state; PWM in µs. |
| laps_n | float or N | Laps completed; null unless a lap stop is set. |
| ms | float | Frame processing latency, ms. |
| danger_max_band | float or N | Largest no-go probability over the rows from a start row onward, 0 to 1. The key keeps its original name. |
| trail | float or N | Trail score. |
| fit_deg | int | Degree of the polynomial actually fitted. |
| cam_state | str | Camera lane state. |
| heading | dict | source, psi_deg (deg), rate_dps (deg/s), age_ms (ms), epoch, fused_yaw_deg (deg), tilt_ok (bool). |
| motion | dict | fresh (bool), img_mad (mean absolute grey difference), dt_s (s), prob_dp1, prob_dt_s (s), driving (bool), would_stall {img_mad, prob_dp1}. |
| owner, sa_mode | str, str or N | Which source owns the output, and its mode. |
| gain_pwm, look_pwm | int or N | Gain knob and lookahead knob, µs. |
| rc* | int or N (the steer_norm mapping key: float or N) | Receiver channel values, µs, one key per channel read, plus a raw copy, a stick copy, and one channel mapped to the steer_norm convention. |
| rssi | int or N | Radio link signal strength. |
| servo1, servo3 | int or N | Reported servo outputs, µs: steering on servo1 and throttle on servo3 for the recorded configuration. |
| servo3_seq | int | In the bundled snapshot, accepted servo-output telemetry sequence counter: increments for accepted `SERVO_OUTPUT_RAW` on port 0 from the target system/component, first packet then forward source timestamps (including uint32 wrap, excluding repeats/replay). It is not a write or steering-command count. Sample-version behaviour needs its own source check. <!-- src/orin/live_trail.py:786-795 --> |
| mode, armed | str, bool | Flight-controller mode and armed flag. |
| vbat, ibat | float | Pack voltage (V) and current (A). |
| rc_age_ms, hb_age_ms, servo3_age_ms | int or N | Age of the last receiver, heartbeat and servo message, ms. |
| output | dict | Output-owner snapshot: owner, mode, state, pwm, throttle, steer, blend weight, override and veto flags, send and tick counts, recovery phase, and the steer and throttle sources. |
| fwd_sends | int | Count of forward commands sent. |
| pts_us, pts_rx_us | int | Phone frame timestamp and receive stamp, µs. |
| dec_depth | int | Decoded frames waiting to be released. |
| stage_ms | dict | Per-stage timings: rx_dec, dec_take, take_pub, take_rec. |
| age_ms | float | Capture-to-record latency, ms, on the phone clock. Not on every frame row. |
| pitch | float | Camera pitch, degrees. |
| phone | dict | Phone state: stream state, pitch and roll (deg), thermal state, capture and stream frame rates, encoder fields, recording id and seconds, free disk (MB), low-power flag, battery, utc_s, and pose {x, y, z in metres, local frame, clock domain}; pose is absent on some frames. |
| hello, clock_off | dict, float | First frame only: the phone's hello message, and the phone's UTC minus the Orin's time, seconds. |
| skipped | int | Frames skipped at ingest. On a few frame rows only. |

### End row

| Field | Meaning |
|---|---|
| event, t, n | Row kind, time, and the last frame number. |
| wheel_sends, wheel_hz_meas | Wheel-command count and measured rate. |
| ms_mean, ms_p99 | Frame latency summary, ms. |
| rec_dropped, rec_chunks | Log-writer drops, and the chunk count. |

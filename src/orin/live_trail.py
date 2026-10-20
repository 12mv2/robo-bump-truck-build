#!/usr/bin/env python3
# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""LIVE trail-centre inference: iPhone -> DINOv2-S/14 (FP16) -> 385-param head -> line -> pure
pursuit -> RC override on the steering channel.  Rewritten 2026-09-06 (v2).

Current local control repair (2026-09-14): OutputOwner alone sends both channels,
at --wheel-hz in all switch positions. Frames publish an expiring Intent; every
cruise withdrawal inhibits restart until HOLD + bottom dial, pose standstill,
then raised dial in HOLD and an exit from HOLD. Optional --stall-recovery adds
one bounded XL-5 retreat, with validated FC configuration and downstream output
feedback. It remains off by default pending physical characterization. Command
tests are offline evidence; physical stopping acceptance is still required.

The learned head consumes RGB. The current controller also uses phone pose for
taught-path feed-forward, speed and motion checks, and live camera pitch when
requested. Depth-ahead and IMU shock telemetry are logged; they do not currently
provide obstacle-clearance or collision-stop control.

WHAT CHANGED FROM v1 (2026-09-06 review):
  * The line is drawn EXACTLY as validated against the operator's 53 strokes (line_rules_v1.py):
    mass-weighted per-row centroid, per-row confidence = mass within +-2 patches, keep rows with
    conf >= 0.4, mass-weighted cubic with quadratic/line fallback on a curvature cap.
    v1 used per-row argmax + a max-prob gate + an unweighted cubic, which was never measured.
    An offline check (not shipped) reproduces the validated line rules from THIS file's code.
  * Mount pitch is a flag (--pitch up|normal|down|<deg>), not a constant buried in the source.
  * The 6 cm camera-to-truck-centre offset is applied (FINDINGS 12).
  * It actually SENDS: RC_CHANNELS_OVERRIDE on the steering channel only, radio keeps every other channel.
    Release on exit, release after --hold-ms without a lane, sysid gate before the perception model loads.
  * --dry-run computes and logs everything and sends nothing (shadow mode while the operator drives).
  * v3 (2026-09-06, field iteration): every frame ALSO logs the radio (RC_CHANNELS: steering, throttle, mode switch, kill dial),
    the servo outputs (SERVO_OUTPUT_RAW 1/3), mode and armed state read back from the FC, and
    the 476x350 feed frame as a JPEG (feed/). In shadow mode the steering channel is the OPERATOR'S steering -- the free
    label grade_run.py fits gain/sign/lookahead against. In auto mode the steering channel is the override and
    servo1 proves it reached the wheels. --note records what changed for this run.
  * Every frame is logged (jsonl + probs npz + a jpg every --jpg-every frames) so a live run can
    be graded against strokes and replayed against any head afterwards.
  * --video runs the identical code path on a corpus rgb.mp4 -- no phone needed to test.
  * Optional --danger-head runs DangerNet on the same backbone pass (display/log only).
  * v6 (2026-09-08): OPTIONAL --cruise holds a fixed throttle on the RC throttle channel while the steering head
    steers, so a tuning pass is one cell (lookahead, gain) and not also a hand-throttle
    (ANALYSIS-2026-09-08-tuning-process 6B/9: "no sweep is interpretable" without it). Off by
    default; with no --cruise flag not one byte of the command path changes. The human's real
    trigger is read from the radio's trigger-copy mixer channel (the FC reports the OVERRIDE, not the stick,
    on an overridden channel -- the same trap the wheel copy solves for steering), and ANY
    trigger movement, HOLD, a mode change, a disarm or stale RC telemetry hands throttle straight
    back to the receiver.
  * v7 (2026-09-09): --min-rows refuses to steer on a line fitted through too few band rows and
    holds the last confident command instead (the sign reversal that lost the kitchen oval's
    corner happened on 6-9 rows). 0 = off, the pre-v7 behaviour exactly.
  * v8 (2026-09-09): OPTIONAL --kff CURVATURE FEED-FORWARD, off by default. Pure pursuit steers
    only on EXISTING lateral error, so it enters a bend late and runs wide at any gain -- bracketed
    from both ends on the kitchen oval in one hour (FINDINGS 36) and structural, not a knob
    (ANALYSIS-2026-09-08-tuning-process 9: Snider CMU-RI-TR-09-08 4.3, ETH MAP "drives wide in all
    corners"). kappa_total = kff*kappa_path + gain*kappa_pursuit, where kappa_path is the
    GROUND-FRAME curvature of the trail near the goal point, from a mass-weighted quadratic over
    the ground-projected per-row CENTROIDS in a window around the lookahead (see path_curvature:
    the tool's own fitted line is curvature-CAPPED and comes back straight on 84-97 % of bend
    frames, so its curvature is identically zero exactly where the term is needed). The
    feed-forward is clamped on its own (--kff-max) before the sum, needs more rows than the
    pursuit term (--kff-min-rows) plus its own window/points/residual test, and never fires on a
    frame --min-rows distrusts. With no --kff flag not one byte of the command path or of a frame
    record changes -- proven by a replay diff against v7. NOT YET BENCH-TESTED OR DRIVEN.
  * v9 (2026-09-09): the LINE FIT's curvature cap can be expressed in the GROUND FRAME
    (--cap-kappa, 1/m), and the fitted DEGREE is now logged on every frame (`fit_deg`). The
    shipped cap of 0.05 patches/row^2 is not a physical quantity: measured over 9 sessions it
    fires on 7.5 % of wedge-down and 17.9 % of wedge-up frames of the SAME trail, and it caps the
    line the controller sees at 0.6 1/m on the oval mount where the bend needs 1.2-1.5, so every
    tight bend arrives as a STRAIGHT LINE (FINDINGS 37, 87 % of tight frames). --cap-kappa keeps
    the same fallback chain and the same protection -- with no cap at all the fit reaches
    1,309 1/m on noise -- but asks the question in 1/m near the goal point, which is comparable
    with KAPPA_FULL_LOCK and roughly 4x more consistent across mounts (it is an upper bound, not
    an unbiased curvature -- calibration in `fit_ground_kappa`'s docstring). OFF by default (--cap-kappa 0 = the validated rule, byte
    for byte; `check_line_rules.py` still PASSES). Numbers and the recommended setting:
    research results (line-cap v2), FINDINGS 39.
  * v11 (2026-09-09 evening): OPTIONAL --fit-max-y <m>: only band rows within that ground distance
    VOTE in the line's polyfit; the line is still drawn, logged and counted (`kept`, --min-rows)
    on every kept row. Why: on corner-ENTRY frames the cubic sat 2.7 patches OUTSIDE the coming
    bend while the head's own far centroids were 1.2 off -- the fit was worse than its inputs,
    because band rows 0-2 (1.5-3 m ahead, where the tape has left the frame and the head reports
    the far side of the loop) drag the far end straight. Nothing in a 108-cell fitter sweep
    (confidence, weights, fit space, fallback) moved the entry by more than 0.04 patches; which
    rows vote did: rows <= 1.1 m voting took the entry goal-row error 1.11 -> 0.82 patches and
    the 1 m lateral error 6.6 -> 3.8 cm, bias gone, straight better too (0.48 -> 0.31). Cost: a
    flatter line inside the (blind) bend and 6 points more cap fallback there. Numbers:
    research results (line-fit sweep v3, H/I). The boundary is
    in METRES through --pitch (1.1 m = band rows 4-16 at 14.55 deg, 3-16 at 16.3), `fit_vote` is
    logged per frame when on. OFF by default: `check_line_rules.py` PASSES, and a whole-tool
    --video replay at the default is byte-identical to v10. NOT YET DRIVEN (pass 6 = pass 3 +
    --fit-max-y 1.12).

Run ON THE ORIN, phone tunnelled and Moblin LIVE:
    sudo usbmuxd --no-preflight
    iproxy 7790 7790 -u $(idevice_id -l | head -1) &
    python3 live_trail.py --head centre_head_lag0_r8c_v1_ALL-SESSIONS.npy --pitch up \
        --fc /dev/ttyACM0 --dry-run --log runs/shadow1          # shadow: log only
    python3 live_trail.py --head ... --pitch up --fc /dev/ttyACM0 --log runs/auto1   # closed loop

Geometry matches the training path exactly (feats_v1.py): 1920x1440 in, cv2.resize to 476x350
(INTER_LINEAR), BGR->RGB, /255, ImageNet mean/std, rows R0..24 of the 25x34 grid.
FP16 costs nothing: 18.9 ms vs 63.7 ms, AUC delta -0.0000 (FINDINGS 29).
"""
import argparse, glob, json, math, os, queue, signal, socket, struct, subprocess, sys, threading, time, warnings
from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
import numpy as np
import launch_phase
from output_owner import Intent, OutputOwner, Pose, finite
from recovery import RecoveryConfig, HardwareProfile
from inspect_reverse import PARAMETERS as REVERSE_PARAMETERS, wait_rover_heartbeat

HOST_HELLO, DEVICE_HELLO, VIDEO_CONFIG, VIDEO_FRAME, STATUS = 1, 2, 3, 4, 0x10
START_RECORD, STOP_RECORD = 0x0A, 0x0B   # host -> phone: record on the phone without touching it (operator, 2026-10-02 23:0x).
                                           # `touch ~/live/RECORD_ON` starts a recording, removing the file stops it; the phone's
                                           # status `recording` is the receipt. Needs "Computer may start/stop recording" ON.
RECORD_FLAG = Path.home() / "live" / "RECORD_ON"
RECORD_SENT = [None]      # what we last asked for (True = start sent, False = stop sent)

STREAM_FLAG = Path.home() / "live" / "STREAM_FPS"   # 2026-10-02 23:4x (operator: "change it yourself"): a number in this file = the stream
STREAM_SENT = [None]                                 # target fps asked of the phone via 0x0C (k=1 native size); session-only on the phone.
SET_STREAM = 0x0C

def send_stream_flag():
    """Once a second: if ~/live/STREAM_FPS holds a number we have not sent on this connection, send 0x0C k=1 fps=N."""
    s = HOST_SOCK[0]
    if s is None: return
    try:                                              # file holds "fps" or "scale fps" (scale 1, 2, 4 or 12 = 1/k of 1920x1440)
        parts = STREAM_FLAG.read_text().split() if STREAM_FLAG.exists() else []
        want = (1, int(parts[0])) if len(parts) == 1 else (int(parts[0]), int(parts[1])) if len(parts) == 2 else None
    except (ValueError, OSError): want = None
    if want is None or want == STREAM_SENT[0] or want[0] not in (1, 2, 4, 12) or not (1 <= want[1] <= 60): return
    try:
        with HOST_TX_LOCK: s.sendall(struct.pack(">BI", SET_STREAM, 2) + bytes([want[0], want[1]]))
        STREAM_SENT[0] = want
        print(f"stream: asked the phone for 1/{want[0]} size at {want[1]} fps (0x0C; receipt = fps_stream and the videoConfig size)")
    except OSError: pass

def send_record_flag():
    """Once a second from the loop: reconcile the flag file with what we last sent."""
    send_stream_flag()
    s = HOST_SOCK[0]
    if s is None: return
    want = RECORD_FLAG.exists()
    if want == RECORD_SENT[0]: return
    try:
        with HOST_TX_LOCK: s.sendall(struct.pack(">BI", START_RECORD if want else STOP_RECORD, 0))
        RECORD_SENT[0] = want
        print(f"record: asked the phone to {'START' if want else 'STOP'} recording ({'touch' if want else 'rm'} {RECORD_FLAG})")
    except OSError: pass

HOST_STATE = 0x0D        # host -> phone (robot-eyes ticket 12, 2026-10-02): {"armed", "owner", "mode"} about once a second. The phone's
                         # HUD puts the word first (red while ARMED) and marks it "stale N s" 3 s after the last one, "NO ORIN" without a host.
HOST_SOCK = [None]       # the live phone connection, set by moblin_frames(); the loop sends on it, the reader thread only receives
HOST_TX_LOCK = threading.Lock()

def send_host_state(armed, owner, mode):
    """Tell the phone what the robot is doing. Never raises: a failure only leaves the HUD stale, which is the honest state."""
    s = HOST_SOCK[0]
    if s is None: return False
    body = json.dumps({"armed": armed, "owner": owner, "mode": None if mode is None else str(mode)}).encode()
    try:
        with HOST_TX_LOCK: s.sendall(struct.pack(">BI", HOST_STATE, len(body)) + body)
        return True
    except OSError: return False
DECODER = ["sw"]         # set from --decoder in main(); read by the reader thread when it spawns ffmpeg. sw since 2026-09-13 02:25 (see below)
AU_DELIM = [True]        # set from --au-delim in main(); read by the reader per access unit (see AUD below)
# 2026-09-13, external review round 3, rank 1 (verified on the Orin with last night's real 15 fps 8 Mbit stream): ffmpeg's
# raw hevc/h264 demuxer+parser cannot know an access unit has ENDED until the NEXT one begins, so on an Annex-B pipe every
# picture waited one frame period (67 ms) for its successor before the decoder even saw it. Appending one tiny NAL after each
# AU gives the parser its boundary at once: fed->decoded on the hardware path 93 -> 27 ms median, held frames 1 -> 0.
# WHICH NAL matters: an access-unit delimiter (nut 35) made hevc_nvv4l2dec emit one EXTRA picture during the start-up drain
# (picture 19 of 180, FIFO depth 3 at the time), which would have shifted every later stamp by a frame. An UNSPECIFIED NAL
# (nut 48, discarded by decoders, a boundary for the parser) does not: 180/180 pictures identical to the plain path, in order,
# two runs (an offline decoder test, not shipped; modes plain/aud/unspec). H264 (not the truck's codec) keeps the AUD; its parser does
# not end a frame on unspecified types. --au-delim off restores the old byte stream for A/B.
AU_END_HEVC = b"\x00\x00\x00\x01\x60\x01\x50"   # nut 48 (unspecified), layer 0, tid 1, one payload byte
AU_END_H264 = b"\x00\x00\x00\x01\x09\xf0"       # AUD type 9; primary_pic_type 7 + stop bit
DEC_MAX_DEPTH = 300     # access units ffmpeg may hold before we call it a desync -- a LEAK guard only. ffmpeg holds ~50 AUs
                        # (3.6 s) while it probes the stream at start-up, then catches up; measured 2026-09-12 on a synthetic 15 fps stream
# v12 (2026-09-10): what the phone says about itself, kept by moblin_frames() and logged per frame.
#   hello    the deviceHello JSON (utc_s, pitch_deg, app_version, files_port, ...) -- once per connect
#   status   the latest 1 Hz status JSON (stream_state, pitch_deg, enc_failed, recording, thermal, ...)
#   pts_us   the phone's timestamp of the IMAGE being processed (ARFrame.timestamp * 1e6 = odometry.csv's clock)
# None of it changes what the tool DOES unless --pitch live is set; it changes what the tool RECORDS, so a run and
# the phone's own session can be joined on one clock and a dead stream is visible in frames.jsonl.
# 2026-09-12 (external review, STANDING.md "Known defects" 1): until this date pts_us was set by the PACKET reader
# when a VIDEO_FRAME arrived, while the image the loop worked on came out of ffmpeg several access units LATER --
# so every logged pts_us belonged to the newest packet, not to the image (the review matched predictions to video
# ~0.5 s earlier than the logged stamp). The stamp now travels WITH the access unit through the decoder (a FIFO of
# pending stamps, popped in order as decoded frames emerge; the wire is display order: 09-12 logs show pts strictly
# monotonic at 66.7 ms). Two new fields measure what the old stamp hid:
#   pts_rx_us  the stamp of the newest packet RECEIVED when this image was yielded (old pts_us); pts_rx_us - pts_us
#              is the decoder's depth in phone time
#   dec_depth  access units still inside ffmpeg when this image came out (frames of pipeline latency)
#   clock_off  phone UTC minus Orin time.time(), the max over the last 30 status packets (least transport delay wins;
#              a WINDOW, not max-ever, because the Orin's wall clock was stepped 2.3 s mid-run on 2026-09-12 and a
#              max-ever estimate then inflated age_ms for the rest of the run -- external review 2026-09-13), so
#              age_ms = capture-to-record latency on the phone's own clock (pose.t_utc/t_us are the same clock as pts)
# v15 (2026-09-13, round-4 review): a timestamped history of the phone's poses, written by the reader thread from every
# status packet that carries a NEW pose_seq. The frame loop reads the newest; nothing else in the loop changes.
POSES = deque(maxlen=64)
OUTPUT_POSE = None                    # immutable receive-stamped sample for the independent sender
def note_pose(st):
    global OUTPUT_POSE
    pz = st.get("pose") if isinstance(st, dict) else None
    if isinstance(pz, dict) and isinstance(pz.get("t_utc"), (int, float)) and isinstance(pz.get("x"), (int, float)):
        seq = st.get("pose_seq")
        if not POSES or POSES[-1][0] != seq or seq is None:
            POSES.append((seq, float(pz["t_utc"]), float(pz["x"]), float(pz.get("z", 0.0)), pz.get("heading_deg"), pz.get("v_mps"), pz.get("tracking"), st.get("recording")))
            received = time.monotonic()
            off = PHONE["clock_off"]
            age = time.time() + off - pz["t_utc"] if finite(off) else float("inf")
            tracking = pz.get("tracking")
            if OUTPUT_POSE is not None and st.get("recording") == OUTPUT_POSE.recording:
                if pz["t_utc"] <= OUTPUT_POSE.stamp: tracking = "nonadvancing"
            OUTPUT_POSE = Pose(seq, pz["t_utc"], received, age, pz["x"], pz.get("z"),
                               pz.get("v_mps"), tracking, st.get("recording"),
                               math.radians(pz["heading_deg"]) if finite(pz.get("heading_deg")) else None)
PHONE = {"hello": None, "status": None, "video_config": None, "status_t": 0.0, "pts_us": None, "pts_rx_us": None, "dec_depth": None,
         "clock_off": None, "clock_offs": deque(maxlen=30), "dec_desync": 0,
         "t_rx": None, "t_dec": None, "t_take": None, "skipped": None}   # async ingest stage stamps (monotonic) + frames skipped
# CLOCKS (external review 2026-09-13): every LOCAL timer -- watchdog, holds, latches, freshness, intervals -- uses
# time.monotonic(); time.time() appears only where a wall clock is the point: the record's "t", the hello/status
# offset, and age_ms. The Orin's wall clock is set from the phone by auto_boot and can step mid-run.
PHONE_LOG_KEYS = ("stream_state", "pitch_deg", "roll_deg", "thermal_name", "fps_capture", "fps_stream", "enc_failed",
                  "enc_consecutive_failed", "enc_last_status", "recording", "record_seconds", "disk_free_mb", "low_power", "battery", "utc_s",
                  # 2026-09-12: the pose contract. They appear in rec["phone"] only when the phone sends them, so a
                  # phone without the pose build leaves the record byte for byte as before. `pose` carries
                  # {x, y, z, yaw_deg, heading_deg, v_mps, t_utc, t_us, tracking} -- STEER ON heading_deg, never yaw_deg
                  # (walk test 2026-09-12, session afbfee6219: heading tracks travel to -9.1 deg, yaw wanders -65..-90).
                  "pose", "depth_ahead_m", "imu_a_peak", "pose_seq")
SC = b"\x00\x00\x00\x01"
FEED_W, FEED_H, PATCH = 476, 350, 14
GW, GH = FEED_W // PATCH, FEED_H // PATCH        # 34 x 25
SRC_W, SRC_H = 1920, 1440
# Intrinsics of the corpus capture (camera_matrix.csv, all 33 files agree). Moblin sends no
# intrinsics; Back Wide at 1x is ~70.7 deg HFOV against 70.2 from fx, close enough (FINDINGS 19).
FX, FY, CX, CY = 1365.6527, 1365.6527, 958.99664, 723.1488
CAM_H = 0.26                 # m, SPEC 2b MEASURED lens height (0.30 is retired)
CAM_OFFSET_X = 0.06          # m, camera is 6 cm LEFT of truck centre (FINDINGS 12)
# Pose-derived mount pitch, degrees below horizon, per session (FINDINGS 12, field ledger):
#   normal 19.7/20.1  wedge-down 24.5/24.5  wedge-up 14.4/14.7
PITCH_PRESETS = {"normal": 19.9, "down": 24.5, "up": 14.55}
# EMPIRICAL, not kinematic (SPEC 3): the operator's 140 cm circle -> R 0.70 m at full lock.
# v10 (2026-09-09): this module constant is now only the DEFAULT of --kappa-full-lock. FINDINGS 38
# measured the plant from pose + servo1 on data already on disk:
#     |kappa| = 0.004778 * |servo1 - SERVO1_TRIM| / (1 + 0.22 v^2)   ->  full lock 1.734/(1+0.22 v^2)
# i.e. 1.66 1/m at 0.45 m/s (kitchen oval), 1.42 at ~1 m/s (trail). The constant below is 16 % low
# indoors. It is NOT changed here because gain and this number multiply (steer = gain*kappa/KFL):
# raising it silently lowers every measured gain by the same factor. Pass --kappa-full-lock and
# rescale --gain by new/old in the same edit (FINDINGS 36 bracket at 1.43: gain 1.0 -> 1.16, 2.0 -> 2.32;
# the 2.0 end was saturating, so it is a different setting under the raised clip, not a rescaled one).
KAPPA_FULL_LOCK = 1.0 / 0.70
# Line rules as validated (results/line_rules_v1.md): conf 0.4, curvature cap 0.05 patches/row^2
DEFAULT_CONF, DEFAULT_CAP = 0.4, 0.05

# --- cruise throttle (v6, 2026-09-08) --------------------------------------------------------
# Throttle is the RC throttle channel on this vehicle (RCMAP_THROTTLE = 2, VERIFIED at startup -- assuming
# the channel is exactly what caused the 2026-09-06 runaway, FINDINGS 30b / arm_guard.py guard 1).
CRUISE_THR_NEUTRAL = 1500     # fallback only; RC2_TRIM is read from the FC when it answers
CRUISE_THR_DZ = 30            # fallback only; RC2_DZ is read from the FC when it answers
CRUISE_SPAN_US = 250.0        # speed dial 1000..2000 us -> neutral .. neutral + 250 us of throttle.
                              # A trim click is ~65 us on the channel (measured for the speed dial, FINDINGS
                              # 30g) = 16.25 us of throttle; ~31 clicks span stopped -> ceiling.
CRUISE_ABS_MAX = 1800         # the tool REFUSES any ceiling above this, whatever the flags say.
                              # Measured field speeds: ~1650-1700 us gave 0.4-1.4 m/s (ANALYSIS 1).
CRUISE_RC_MAX_AGE_MS = 250    # RC_CHANNELS is requested at 50 Hz; 250 ms = 12 missed frames
CRUISE_MIN_BAND = 20          # floor on the trigger neutral band and on the "stopped" margin

# --- v15.2 speed setpoint (2026-09-13 18:1x, operator at the truck: "one click is crawl or too fast") ------------
# A dialled PWM is not a speed: the pack sags inside a session and the same PWM gave 0.16-0.48 m/s on one click
# and 0.80-0.90 on the next (FINDINGS 67). --cruise-mps makes the speed dial dial METRES PER SECOND and a PI trim on the
# throttle PWM holds it, using the phone's own 15 Hz pose speed. Feed-forward from TODAY'S measured floor:
# 1640 us -> ~0.30 m/s and 1655 -> ~0.80 (run auto-boot-160828), i.e. 30 us per m/s through v = 0 at 1631 us.
# These are the pack and floor of 2026-09-13; the integrator carries whatever the day's plant actually is.
CRUISE_FF_US = 1631.0         # PWM at which this truck starts to roll (feed-forward intercept)
CRUISE_US_PER_MPS = 30.0      # feed-forward slope
CRUISE_MPS_ABS_MAX = 1.5      # the tool REFUSES a setpoint band above this, whatever the flags say

# ---------------------------------------------------------------------------------------------
# 1. The line -- byte-for-byte the logic of line_rules_v1.py (fit() + conf), nothing else.
#
# v9 (2026-09-09): the fallback test may OPTIONALLY be made in the GROUND FRAME (--cap-kappa).
#
# WHAT THE CAP IS FOR. `cap` rejects a fit that bends more than `cap` patches per row squared and
# steps down cubic -> quadratic -> straight line. It exists to stop a high-degree polynomial
# swinging wildly through a noisy or collapsed set of rows and handing pure pursuit a goal point
# at nothing. That failure is real: with no cap at all the fitted line's own ground curvature
# reaches 1,309 1/m (a 1 mm radius) on the kitchen oval -- pure noise read as a hairpin.
#
# WHY THE UNIT IS WRONG. `patches per row squared` is not a physical quantity. A band row maps to
# a ground distance through the mount pitch, and the map is strongly non-linear: wedge-up the 17
# rows span 3.07 m down to 0.30 m, wedge-down 0.98 m down to 0.21 m. The SAME ground curvature
# therefore produces very different image curvature on different mounts, and 0.05 means different
# things on each. Measured over the five trail corpus sessions, the shipped cap fires on 7.5 % of
# wedge-down frames and 17.9 % of wedge-up frames of the same trail. On the kitchen oval it fires
# on 87 % of the frames whose true path curvature is above 0.9 1/m (FINDINGS 37) and hands the
# controller a STRAIGHT LINE through the bend.
#
# WHAT 0.05 ACTUALLY ALLOWS. Converting every accepted fit to its ground-frame curvature at the
# goal (2,338 moving oval frames, 6,689 trail, LOSO heads): under `cap 0.05` the line the
# controller sees can NEVER exceed 0.6 1/m on the oval mount and 2.6 1/m on the trail mounts. The
# oval's bends need 1.2-1.5 1/m (FINDINGS 37). The cap is not a filter there, it is a ceiling
# below the answer.
#
# WHAT --cap-kappa DOES. Same fallback chain, same code, but the test is "does this fit imply a
# ground curvature above `kappa_max` anywhere near the goal point?" -- one number, in 1/m, that
# is directly comparable with KAPPA_FULL_LOCK and means MUCH more nearly the same thing on every
# mount and lookahead (about 4x more comparable; see the calibration in `fit_ground_kappa`). The window is [0.6, 1.4] x lookahead, the same window `path_curvature` was
# validated on (FINDINGS 37), clamped into the rows the fit actually spans so it is never empty.
#
# MEASURED (results/line_cap_v2.md, 2026-09-09, LOSO heads, error = median |line - the driver's
# own path| in patch columns):
#   oval  cap 0.05 -> p50 0.87, within-1 0.57, p90 2.49, 67.5 % of frames a STRAIGHT LINE
#         cap-kappa 2.86 -> p50 0.73, within-1 0.64, p90 2.19, 10.8 % straight; ceiling 2.9 1/m
#   trail cap 0.05 -> p50 1.24, within-1 0.38, p90 3.66, 12.2 % straight; ceiling 2.6 1/m
#         cap-kappa 2.86 -> p50 1.23, within-1 0.39, p90 3.66,  2.0 % straight; ceiling 2.9 1/m
# The straight-line fallback is worse than the cubic against the driver's own path on 8 OF THE 9
# sessions, oval and trail alike (on the ninth the QUADRATIC is best and the cubic worst -- degree
# 2 would have been right there, degree 1 was not) -- and `line_rules_v1.md`'s own table already
# showed the cap never improving the stroke score (rule B: p50 0.75 -> 0.76, within-1 0.61 -> 0.59).
#
# STILL DEFAULT-OFF. --cap-kappa 0 is the shipped default and reproduces the validated behaviour
# byte for byte (`check_line_rules.py` PASSES unchanged). The trail numbers above are neutral, not
# better, and FINDINGS 36's standing consequence applies: an offline frame-by-frame comparison
# does not rank closed-loop settings. Turn it on for the oval, drive it, then decide about trail.
# ---------------------------------------------------------------------------------------------
CAP_WIN_NEAR, CAP_WIN_FAR = 0.6, 1.4      # --cap-kappa window, as multiples of the lookahead
CAP_KAPPA_SAMPLES = 41                    # curve samples across the fitted row span
DEFAULT_CAP_KAPPA = 2.0 * KAPPA_FULL_LOCK  # 2.86 1/m: the line may not claim a bend more than
                                           # twice as tight as the truck's own full lock. Only
                                           # used as the *suggested* value; the default is OFF.
                                           # NOTE (FINDINGS 38, same day): KAPPA_FULL_LOCK itself
                                           # is now measured at 1.66 1/m at 0.45 m/s, not 1.43, and
                                           # is speed-dependent. When that correction lands this
                                           # expression follows it (-> 3.32) but a literal
                                           # `--cap-kappa 2.86` written into auto.conf will NOT.
                                           # The measurements in line_cap_v2.md were taken at 2.86
                                           # and the calibration there is geometric, not vehicle-
                                           # dependent, so they stand either way.

def fit_ground_kappa(cf, r_lo, r_hi, r0, pitch, cam_h=CAM_H, y_lo=None, y_hi=None,
                     n=CAP_KAPPA_SAMPLES):
    """Max |ground-frame curvature| (1/m) of an image-space fit `cf` (patch column = P(band row)).

    The fitted line is a polynomial in the BAND ROW; `ground_point` turns a (row, column) pair into
    a ground (x right, y forward) pair through smooth analytic functions of the row alone, so the
    ground curve the controller will consume is itself a smooth function of the row and its
    curvature can be computed exactly -- no circle fit, no differencing of noisy data (FINDINGS 37
    rejected an algebraic circle fit for exactly that: it read noise as curvature).

    `y_lo`/`y_hi` restrict the search to a distance window (the goal region), CLAMPED into the
    span the fit covers so it is never empty -- a wedge-down mount at --lookahead 2.0 sees no
    ground past 0.98 m and would otherwise have no window at all.
    Returns inf for a fit that runs through the horizon, i.e. reject.

    THIS IS AN UPPER BOUND, NOT AN UNBIASED CURVATURE. A cubic fitted in IMAGE space is not a
    circle in the GROUND frame, so it overshoots locally where the far rows are sparse. Calibrated
    against synthetic arcs at the real band-row distances (results/line_cap_v2.md 1b, Ld 0.8 on all
    three mounts): a true 1.00 1/m reads 1.62 wedge-up / 2.31 normal / 2.09 wedge-down. THE READING
    MOVES 1.4-3.0x ACROSS MOUNTS, against 1.5-10.7x for patches/row^2 -- far more comparable, NOT
    perfectly mount-neutral, and it depends on --lookahead through the window. It errs toward
    rejecting, which is the safe direction for a rejection test. The unbiased estimator is `path_curvature` (FINDINGS 37); it
    is not used here because it is a fit to data and a rejection test must not be fooled by the
    same noise it is meant to catch.
    """
    th = math.radians(pitch); s_th, c_th = math.sin(th), math.cos(th)
    r = np.linspace(r_lo, r_hi, n)
    yc = ((r + r0 + 0.5) * SRC_H / GH - CY) / FY
    y_w = s_th + yc * c_th
    z_w = c_th - yc * s_th
    if np.any(y_w <= 1e-6) or np.any(z_w <= 0): return float("inf")
    t = cam_h / y_w
    x = t * ((np.polyval(cf, r) * SRC_W / GW - CX) / FX) - CAM_OFFSET_X
    y = t * z_w
    dr = r[1] - r[0]
    xp = np.gradient(x, dr); yp = np.gradient(y, dr)
    xpp = np.gradient(xp, dr); ypp = np.gradient(yp, dr)
    k = np.abs(xp * ypp - yp * xpp) / np.maximum((xp * xp + yp * yp) ** 1.5, 1e-12)
    m = np.ones(n, bool); m[:2] = False; m[-2:] = False     # gradient endpoints are one-sided
    if y_lo is not None:
        lo, hi = max(y_lo, float(y.min())), min(y_hi, float(y.max()))
        if hi > lo:
            w = m & (y >= lo) & (y <= hi)
            if w.sum() >= 3: m = w
    return float(np.nanmax(k[m])) if m.any() else float("inf")

def line_from_probs(prob, conf_thr=DEFAULT_CONF, cap=DEFAULT_CAP, curve=None, info=None, vote=None):
    """prob (NR,34) -> (line (NR,) patch-centre column or nan, conf (NR,), mass (NR,), cent (NR,)).

    mass-weighted centroid of the above-0.5 probability, per-row confidence = fraction of that
    mass within +-2 patches of the centroid, keep rows with conf >= conf_thr, then a mass-weighted
    cubic; if its second derivative exceeds `cap` anywhere fall back to quadratic, then to a line.
    Columns are in patch-centre units (col+0.5), i.e. 0.5 .. 33.5.

    `curve` (v9, OPTIONAL) replaces the patch-space cap with the ground-frame one:
    (kappa_max 1/m, r0, pitch deg, cam_h m, y_lo m, y_hi m). Same chain, physical units -- see the
    block comment above. `curve=None` is the shipped default and the validated behaviour.
    `info` (optional dict) is filled with `deg` (the degree actually used, 0 = no line) and, when
    `curve` is on, `kappa` (the accepted fit's own ground curvature) -- diagnostics only, so that a
    fallback to a straight line is VISIBLE in the log instead of silent.
    `vote` (v11, OPTIONAL) is a bool mask (NR,) of the rows ALLOWED TO VOTE in the polyfit. `keep`
    and `ok` are unchanged; the polynomial is fitted through `ok & vote` only, then evaluated on
    every `ok` row as before, so the far rows keep their ground points for the display, the log
    and `kept`. The `curve` cap samples the voting span, exactly as line_fit_sweep_v3 scored it;
    the patch-space `cap` still tests every drawn row. Needs >= 4 voting rows (else no line, the
    same path as < 4 kept rows). `vote=None` (the default) = every kept row votes, byte for byte
    the validated behaviour. `info["vote"]` = the number of rows that voted.
    """
    NR = prob.shape[0]; rr = np.arange(NR)
    wm = np.clip(prob - 0.5, 0, None) ** 2; m = wm.sum(1)
    cols = np.arange(34) + 0.5
    cent = np.array([(wm[q] * cols).sum() / m[q] if m[q] > 0 else np.nan for q in range(NR)])
    conf = np.array([wm[q][max(0, int(cent[q]) - 2):int(cent[q]) + 3].sum() / m[q]
                     if m[q] > 0 and not np.isnan(cent[q]) else 0.0 for q in range(NR)])
    keep = conf >= conf_thr
    ok = keep & ~np.isnan(cent); line = np.full(NR, np.nan)
    vo = ok if vote is None else (ok & np.asarray(vote, bool))     # rows that VOTE; `ok` = rows drawn
    if info is not None: info["deg"] = 0; info["kappa"] = None; info["vote"] = int(vo.sum())
    if ok.sum() < 4 or vo.sum() < 4: return line, conf, m, cent
    for deg in ((3, 2, 1) if (cap or curve) else (3,)):
        if vo.sum() <= deg: continue
        cf = np.polyfit(rr[vo], cent[vo], deg, w=np.sqrt(m[vo]))
        if curve is not None:
            kg = (0.0 if deg == 1 else
                  fit_ground_kappa(cf, rr[vo].min(), rr[vo].max(), curve[1], curve[2], curve[3],
                                   curve[4], curve[5]))
            good = deg == 1 or kg <= curve[0]
        else:
            kg = None
            good = cap is None or deg == 1 or \
                np.abs(np.polyval(np.polyder(cf, 2), rr[ok])).max() <= cap
        if good:
            line[ok] = np.polyval(cf, rr[ok])
            if info is not None: info["deg"] = deg; info["kappa"] = kg
            return line, conf, m, cent
    return line, conf, m, cent

# ---------------------------------------------------------------------------------------------
# 2. Rig geometry: an image point on a patch row -> a ground point in the TRUCK frame
# ---------------------------------------------------------------------------------------------
def ground_point(row_abs, col_pc, pitch_deg, h=CAM_H):
    """Absolute grid row (0..24) and patch-centre column (0.5..33.5) -> (x right, y forward) m,
    in the truck frame (camera offset removed). None if the ray is at/above the horizon.
    Exact pinhole ray rotated by the measured pitch, intersected with flat ground."""
    th = math.radians(pitch_deg)
    v = (row_abs + 0.5) * SRC_H / GH
    u = col_pc * SRC_W / GW
    xc, yc = (u - CX) / FX, (v - CY) / FY
    y_w = math.sin(th) + yc * math.cos(th)            # downward component after pitch
    if y_w <= 1e-6: return None
    t = h / y_w
    z_w = math.cos(th) - yc * math.sin(th)
    if z_w <= 0: return None
    return t * xc - CAM_OFFSET_X, t * z_w

def horizon_row_px(pitch_deg):
    """Image row (px of 1440) where flat ground meets the sky for this pitch -- an eyeball check
    that the mount is what --pitch says: ~373 px (26%) wedge-up, ~234 (16%) normal, ~101 down."""
    return CY - FY * math.tan(math.radians(pitch_deg))

# ---------------------------------------------------------------------------------------------
# 2b. v15 TAUGHT-PATH FEED-FORWARD (2026-09-13, after the round-4 review; STANDING section 1: teach-and-repeat = a taught path's
# curvature as feed-forward PLUS the camera as the correction; FINDINGS 63-65).  OFF unless --path is passed; byte-identical off.
#
# What it does, per frame, when --path is on and the phone's pose is usable:
#   * the pose (phone) is moved to the REAR AXLE with the body heading if the path was taught at the axle (meta ref_point),
#     predicted forward by the pose's own age (+ --ff-act-s) with the last commanded curvature, and localised on the path with a
#     direction-aware window; DIRECTION comes from the pose heading against the path tangent, never from the camera's command;
#   * FRESH frame (the camera steered this frame):  u = (1 - w) * kappa_ref + w * kappa_cam   (kappa_cam = today's gained, clipped,
#     EMA'd camera command; the reference is NOT filtered and NOT gained -- it is raw 1/R at the truck's convention, + = RIGHT);
#   * GATED / held / released frame: the camera term is WITHDRAWN and the reference keeps advancing:  u = kappa_ref  -- bounded by
#     --path-max-blind-m of travel without an accepted camera frame (0.5 m: round 4 would not certify 2 m; derive longer from data);
#   * pose stale (> --pose-max-age-ms), tracking not normal, off the path (> --path-max-dist-m), or the ALIGNMENT INVALID after a
#     reset / jump / re-position (position or heading jump incompatible with v*dt, recording id change, pose_seq going backwards)
#     -> no path term: v13 behaviour exactly (hold, then release).  An invalid alignment stays invalid for the run (path_epoch
#     counts them); re-teach and restart.  The truck is never steered from an old map through a turn.
# Replay of FINDINGS 65: on the 199 ms drive the whole exit gain of a 50/50 blend came from the held frames (1.00 -> 1.30); the
# fresh-frame blend changed nothing at the exit.  So the withdraw-on-gate rule is the experiment, not a detail.
# ---------------------------------------------------------------------------------------------
class TaughtPath:
    """record_path.py npz in the phone's CURRENT ARKit frame (xy, s, heading, kappa, sections, centre, meta)."""
    CAM_FWD_M, CAM_LEFT_M = 0.24, 0.06     # STANDING: lens 0.24 m ahead of the rear axle, 0.06 m left of the centreline (operator, tape, twice)
    def __init__(self, fn):
        z = np.load(fn, allow_pickle=True)
        self.xy = np.asarray(z["xy"], float); self.s = np.asarray(z["s"], float); self.kappa = np.asarray(z["kappa"], float)
        # v15.6 (2026-09-19, "out-and-back"): a route may be OPEN -- a straight or a corner taught there-and-back has a first
        # point and a LAST point. record_route.py writes closed=False; every path before it is closed and reads exactly as before.
        self.closed = bool(z["closed"]) if "closed" in z.files else True
        if self.closed: self.length = float(z["L"]) if "L" in z.files else float(self.s[-1] + math.hypot(*(self.xy[0] - self.xy[-1])))
        else: self.length = float(self.s[-1])                       # no closing chord on an open route
        self.sections = [str(x) for x in z["sections"]] if "sections" in z.files else ["?"] * len(self.xy)
        self.meta = {}
        if "meta" in z.files:
            try: self.meta = json.loads(str(z["meta"]))
            except Exception: self.meta = {}
        h = np.asarray(z["heading"], float)
        unit = str(z["heading_unit"]) if "heading_unit" in z.files else ("deg" if np.abs(h).max() > 2 * math.pi + 1e-6 else "rad")
        if unit == "deg": h = np.radians(h)
        if self.closed: d = np.roll(self.xy, -1, 0) - np.roll(self.xy, 1, 0)
        else: d = np.gradient(self.xy, axis=0)                      # open: no chord between the two ends
        h_xy = np.arctan2(d[:, 1], d[:, 0])
        dis = np.degrees(np.abs((h - h_xy + math.pi) % (2 * math.pi) - math.pi))
        self.heading_rebuilt = bool(np.median(dis) > 10.0)
        self.heading = h_xy if self.heading_rebuilt else h        # a heading that disagrees with its own xy is never used (FINDINGS 64)
        self.ref_point = self.meta.get("ref_point", "phone"); self.mount_yaw = float(self.meta.get("mount_yaw_motion_deg", self.meta.get("mount_yaw_deg", 0.0)))
        self.out_and_back = bool(self.meta.get("out_and_back", False))   # v15.6.1: two legs on one trail -> heading breaks the cold-start tie
        self.n = len(self.xy)
    def axle(self, x, z, heading_rad):
        """Phone floor position -> rear-axle centre, with the body forward axis = look axis minus the mount yaw."""
        if self.ref_point != "axle": return x, z
        h = heading_rad - math.radians(self.mount_yaw); fx, fz = math.cos(h), math.sin(h); rx, rz = -fz, fx      # right = (f x up) on the floor
        return x - self.CAM_FWD_M * fx + self.CAM_LEFT_M * rx, z - self.CAM_FWD_M * fz + self.CAM_LEFT_M * rz
    def ds_from(self, idx):
        """Signed arc length of every point from idx: wrapped to (-L/2, L/2] on a closed path, plain on an open route (v15.6)."""
        if self.closed: return (self.s - self.s[idx] + self.length / 2) % self.length - self.length / 2
        return self.s - self.s[idx]
    def remaining_m(self, idx, direction=1):
        """v15.6: metres of route left AHEAD of idx in the direction of travel. Closed paths never run out (inf)."""
        if self.closed: return float("inf")
        return float(self.length - self.s[idx]) if direction >= 0 else float(self.s[idx])
    def nearest(self, x, z, last_idx=None, direction=1, ahead_m=1.5, back_m=0.5, heading=None):
        """Nearest path index; with last_idx only points within (back_m behind, ahead_m ahead) IN THE DIRECTION OF TRAVEL are candidates.
        v15.6.1 (2026-09-20, the out-and-back LOOP, record_route.py --loop): a loop taught out and back on one trail has two legs
        lying on top of each other, running opposite ways. A cold start (no last_idx) or a relocalisation (window empty) picks by
        distance alone, and on such a loop that is a coin toss -- and the losing side is NOT harmless: read backwards, a
        turnaround's exit is not its approach, so at the marker the reference would steer up the exit curve while the truck is
        still on the straight (bench, tests/test_route_loop.py). So on a loop whose file says `out_and_back` (meta, written by the
        recorder) and with `heading` (body heading, rad): if the nearest point's tangent is more than 90 deg from the heading, the
        nearest point within 3 m whose tangent AGREES is taken instead. Every other path lacks the flag and reads exactly as
        before (2,000-pose bench); the windowed search is untouched on every path."""
        d = np.hypot(self.xy[:, 0] - x, self.xy[:, 1] - z)
        if last_idx is not None:
            ds = self.ds_from(last_idx)
            if direction < 0: ds = -ds
            m = (ds >= -back_m) & (ds <= ahead_m)
            if m.any(): cand = np.where(m)[0]; j = int(cand[np.argmin(d[cand])]); return j, float(d[j])
        j = int(np.argmin(d))
        if heading is not None and self.out_and_back and math.cos(heading - self.heading[j]) < 0:
            near = np.where(d <= d[j] + 3.0)[0]; agree = near[np.cos(heading - self.heading[near]) >= 0]
            if len(agree): j = int(agree[np.argmin(d[agree])])
        return j, float(d[j])
    def direction(self, idx, heading_rad):
        return 1 if math.cos(heading_rad - self.heading[idx]) >= 0 else -1

    def goal_ahead(self, idx, Ld, direction=1):
        """v15.3: the index Ld metres along the path AHEAD of idx in the direction of travel (wraps on a closed path;
        v15.6: CLAMPS to the last point of an open route -- the goal never comes round from the start)."""
        ds = self.ds_from(idx)
        if direction < 0: ds = -ds
        return int(np.argmin(np.abs(ds - Ld)))

    def to_body(self, gx, gz, ax, az, heading_rad):
        """v15.3: a path point -> the truck's body frame at the REAR AXLE, in `ground_point`'s convention:
        (right, forward) with RIGHT positive, so kappa = 2*right/d^2 keeps STANDING's sign rule (+ = steer right).
        The body forward axis is the look axis minus the mount yaw, the same axis `axle()` uses."""
        h = heading_rad - math.radians(self.mount_yaw)
        c, sn = math.cos(h), math.sin(h)
        dx, dz = gx - ax, gz - az
        return -dx * sn + dz * c, dx * c + dz * sn        # right = (f x up) on the floor = (-f_z, f_x); forward = along f

def return_kappa(tpath, pred, idx, direction, Ld, gain, kappa_max):
    """v15.3 -> (kappa, record) to steer the truck BACK onto the taught path, or (None, None) if the goal is degenerate.

    Pure pursuit at the REAR AXLE onto a point Ld metres along the reference ahead of the nearest one. Sign follows
    STANDING: + = steer right, so a truck LEFT of the path gets a POSITIVE curvature. Extracted from the loop so the
    bench can drive it (an offline test, not shipped)."""
    ax, az, h = pred
    gi = tpath.goal_ahead(idx, Ld, direction)
    gx, gz = float(tpath.xy[gi, 0]), float(tpath.xy[gi, 1])
    rx, fy = tpath.to_body(gx, gz, ax, az, h)
    d2 = rx * rx + fy * fy
    if d2 < 1e-6: return None, None
    k = max(-kappa_max, min(kappa_max, gain * 2.0 * rx / d2))
    return k, {"goal": [round(gx, 3), round(gz, 3)], "body": [round(rx, 3), round(fy, 3)],
               "kappa": round(k, 4), "behind": bool(fy < 0)}


def pure_pursuit_kappa(pts, Ld, fwd=0.0):
    """Standard pure pursuit at the goal point nearest the lookahead: kappa = 2x / d^2.
    2026-09-12 `fwd` (--cam-fwd): the derivation (Coulter 1992) takes (x, y) from the REAR AXLE; `ground_point` returns y
    from the LENS, which sits 0.24 m ahead of the axle (operator, tape, twice: wheelbase 0.28, lens 0.04 behind the front
    axle). Omitting it over-commands: replay of auto-boot-133556 (957 fresh model-steered moving frames) has 144 raw
    requests at full lock without the term and 0 with it; median |gain*kappa| 0.60 -> 0.33. The GOAL is still chosen
    by its distance from the lens (so --lookahead and --fit-max-y keep their meaning and the same row is aimed at);
    only the curvature is computed from the axle. fwd = 0 is byte-identical to before. The goal returned is (x, y_lens)."""
    if not pts: return None, None
    x, y = min(pts, key=lambda p: abs(math.hypot(p[0], p[1]) - Ld))
    ya = y + fwd
    d2 = x * x + ya * ya
    if d2 < 1e-6: return None, None
    return 2.0 * x / d2, (x, y)

# ---------------------------------------------------------------------------------------------
# 2a. Path curvature for the v8 feed-forward (2026-09-09). OFF unless --kff is passed.
#
# WHY A SEPARATE FIT AND NOT `pts`. `line_from_probs` applies the validated curvature CAP
# (0.05 patches/row^2): when the cubic and the quadratic both exceed it the fit falls back to a
# STRAIGHT LINE. Measured over the four kitchen-oval sessions with their leave-one-session-out
# heads, that fallback fires on 84-97 % of the frames whose path curvature is above 0.9 1/m --
# i.e. exactly the corner frames. The ground points the tool already computes are therefore
# straight to a median of 0.0 cm on a bend, and a curvature read off them is identically zero.
# The feed-forward instead re-fits the SAME per-row centroids the line is drawn from (`cent`,
# weighted by `mass`, gated by the same `conf`), before the cap. The pure-pursuit path is not
# touched: the line, the goal point and kappa = 2x/d^2 are byte for byte what they were.
# v9 NOTE: with --cap-kappa on, the line is no longer straight on most bend frames (10.8 % rather
# than 67.5 % on the oval, FINDINGS 39), so the two fits see much the same shape. The feed-forward
# still uses the raw centroids on purpose -- it must not inherit whatever the cap decides.
#
# WHY A WINDOWED QUADRATIC IN THE GROUND PLANE AND NOT A CIRCLE FIT. Curvature must be in 1/m in
# the ground frame, and `ground_point` maps rows to distances non-linearly, so image-space
# curvature is not proportional to it. Two ground-frame estimators were measured against the
# ARKit pose (the curvature of the path the truck actually drove, at the goal distance ahead):
#   * algebraic (Kasa) circle fit -- the near rows are packed into 0.30-0.60 m and are very close
#     to collinear, so the normal equations are near-singular; it returned |kappa| up to 2.4 1/m
#     on frames whose true curvature was zero (23-62 % of frames above 1.5 1/m). It reads noise as
#     curvature, which is the one failure that would wreck the straight. REJECTED.
#   * mass-weighted quadratic x = f(y) over a distance window, exact curvature
#     kappa = x'' / (1 + x'^2)^1.5 at the goal. Well conditioned, and the residual of the fit is
#     itself a usable quality gate. CHOSEN.
#
# WHY THE WINDOW. Past ~1.4x the lookahead the estimate REVERSES SIGN on a tight course: at 0.7 m
# bend radius the tape has already left the frame edge and the head locks onto the far side of the
# loop, which curves the other way. Opening the window to the whole band takes the correlation with
# pose from +0.72 to -0.5 and the sign agreement from 0.94 to 0.33. The window is expressed in
# multiples of the lookahead so it follows --lookahead / --look-trim.
#
# VALIDATED (2026-09-09, 2,468 moving frames, 4 oval sessions, LOSO heads, Ld 0.8):
#   r vs pose +0.72 | sign agreement in bends 0.94 | bend |kappa| p50 1.24 against a pose truth of
#   1.19 (ratio 1.04) | on straight frames p50 0.18, p90 0.43, max 1.02 | fires on 65 % of frames.
KFF_NEAR_FRAC, KFF_FAR_FRAC = 0.6, 1.4    # fit window, as multiples of the lookahead
KFF_MIN_PTS = 6                           # points needed inside the window (7 band rows land there
                                          # at Ld 0.8 wedge-up, so this is "6 of 7 rows confident")
KFF_MIN_SPAN = 0.25                       # m of forward span the window must actually cover
KFF_MAX_RESID = 0.02                      # m, mass-weighted rms residual of the quadratic

def path_curvature(rows, Ld, min_pts=KFF_MIN_PTS, max_resid=KFF_MAX_RESID):
    """Ground-frame curvature of the trail near the goal point -> (kappa 1/m, reason).

    `rows` is [(x right, y forward, mass)] for the confident band rows, ground-projected from the
    per-row centroids. kappa is positive when the trail curves RIGHT, the same convention as
    pure_pursuit_kappa. A non-None reason means NO estimate: the caller must then feed nothing
    forward, never a guess."""
    if Ld <= 0: return None, "no_look"
    lo, hi = KFF_NEAR_FRAC * Ld, KFF_FAR_FRAC * Ld
    P = [r for r in rows if lo <= r[1] <= hi]
    if len(P) < min_pts: return None, "few_pts"
    y = np.array([r[1] for r in P]); x = np.array([r[0] for r in P]); m = np.array([r[2] for r in P])
    if y.max() - y.min() < KFF_MIN_SPAN: return None, "short_span"
    if not np.isfinite(m).all() or m.sum() <= 0: return None, "no_mass"
    try:
        with warnings.catch_warnings():        # a rank-deficient frame is a "no estimate", not a
            warnings.simplefilter("ignore")    # message on a field console
            cf = np.polyfit(y, x, 2, w=np.sqrt(m))
    except Exception:
        return None, "fit_failed"
    if not np.isfinite(cf).all(): return None, "fit_failed"
    res = float(np.sqrt(np.average((np.polyval(cf, y) - x) ** 2, weights=m)))
    if not np.isfinite(res) or res > max_resid: return None, "resid"
    d1 = float(np.polyval(np.polyder(cf, 1), Ld)); d2 = 2.0 * float(cf[0])
    k = d2 / (1.0 + d1 * d1) ** 1.5
    if not np.isfinite(k): return None, "nan"
    return k, None

# ---------------------------------------------------------------------------------------------
# 3. Flight controller: the steering-channel override with the radio keeping everything else
# ---------------------------------------------------------------------------------------------
class FC:
    def __init__(self, port, sysid, steer_sign, cruise=False, recovery=False, heading=None, heading_receipt_path=None):
        launch_phase.before_fc_access()
        self.heading = heading
        from pymavlink import mavutil
        self.m = mavutil.mavlink_connection(port, baud=115200, source_system=sysid)
        if wait_rover_heartbeat(self.m) is None:
            sys.exit(f"REFUSING: no FC heartbeat on {port}")
        self.sign = steer_sign
        p = self._params(["MAV_GCS_SYSID", "SYSID_MYGCS", "RC_OVERRIDE_TIME",
                          "RC1_MIN", "RC1_TRIM", "RC1_MAX", "RC5_OPTION"])
        # Capture calibration on this connection, before the output owner exists.
        # Missing readbacks stay missing; a saved setup must not invent defaults.
        self.setup_parameters = dict(p)
        gcs = p.get("MAV_GCS_SYSID", p.get("SYSID_MYGCS"))
        # RC override is SILENTLY discarded from any other sysid (FINDINGS 16): no error, no motion.
        if gcs is None: sys.exit("REFUSING: could not read MAV_GCS_SYSID / SYSID_MYGCS")
        if int(gcs) != sysid:
            sys.exit(f"\nREFUSING TO COMMAND: FC expects GCS sysid {int(gcs)}, this sender is "
                     f"{sysid}. ArduPilot drops the override silently. Fix one side. FINDINGS 16.\n")
        self.rc_min, self.rc_trim, self.rc_max = int(p["RC1_MIN"]), int(p["RC1_TRIM"]), int(p["RC1_MAX"])
        self.override_time = float(p.get("RC_OVERRIDE_TIME", 3.0))
        print(f"FC ok: sysid gate {int(gcs)}; RC1 {self.rc_min}/{self.rc_trim}/{self.rc_max}; "
              f"RC_OVERRIDE_TIME {self.override_time}s; RC5_OPTION {int(p.get('RC5_OPTION', -1))} "
              f"(31 = killswitch); steer sign {steer_sign:+d} (higher PWM = right if +1)")
        # v6 cruise: the throttle channel's own neutral and dead zone, and a hard refusal if the
        # FC does not actually read throttle from the channel the tool overrides (RCMAP_THROTTLE 2), and
        # an override on the wrong channel is silent and wrong (arm_guard.py exists for this).
        self.thr_neutral, self.thr_dz, self.thr_ch = CRUISE_THR_NEUTRAL, CRUISE_THR_DZ, None
        if cruise:
            q = self._params(["RCMAP_THROTTLE", "RC2_TRIM", "RC2_DZ"])
            self.setup_parameters.update(q)
            self.thr_ch = int(q["RCMAP_THROTTLE"]) if "RCMAP_THROTTLE" in q else None
            if self.thr_ch != 2:
                sys.exit(f"\nREFUSING --cruise: RCMAP_THROTTLE reads {self.thr_ch}, not 2. This "
                         "tool overrides the RC throttle channel (RCMAP_THROTTLE 2); on any other mapping the "
                         "override is silently wrong. Fix the FC or drop --cruise. FINDINGS 30b.\n")
            if "RC2_TRIM" in q and 900 <= int(q["RC2_TRIM"]) <= 2100: self.thr_neutral = int(q["RC2_TRIM"])
            if "RC2_DZ" in q and int(q["RC2_DZ"]) > 0: self.thr_dz = int(q["RC2_DZ"])
            print(f"cruise: throttle is channel {self.thr_ch} (RCMAP_THROTTLE); "
                  f"RC2_TRIM {self.thr_neutral}, RC2_DZ {self.thr_dz}")
        self.recovery_profile = None
        self.recovery_parameters = None
        if recovery:
            self.recovery_parameters = self._params(REVERSE_PARAMETERS)
            try:
                self.recovery_profile = HardwareProfile.from_params(self.recovery_parameters)
            except ValueError as exc:
                self.m.close()
                sys.exit(f"REFUSING --stall-recovery: {exc}")
        setup_names = ["RCMAP_STEER", "RCMAP_THROTTLE", "RC1_DZ", "RC1_REVERSED",
                       "SERVO1_MIN", "SERVO1_TRIM", "SERVO1_MAX", "SERVO1_REVERSED", "SERVO1_FUNCTION",
                       "RC2_MIN", "RC2_TRIM", "RC2_MAX", "RC2_DZ", "RC2_REVERSED",
                       "SERVO3_MIN", "SERVO3_TRIM", "SERVO3_MAX", "SERVO3_REVERSED", "SERVO3_FUNCTION"]
        self.setup_parameters.update(self._params([name for name in setup_names if name not in self.setup_parameters]))
        for name in setup_names:
            self.setup_parameters.setdefault(name, None)
        # v3: ask for RC_CHANNELS + SERVO_OUTPUT_RAW at 20 Hz (ArduPilot's RC_CHANNELS stream
        # carries both). Heartbeat is 1 Hz on its own. Drained non-blocking once per frame.
        self.tele = {"rc1": None, "rc2": None, "rc3": None, "rc5": None, "rc6": None, "rc8": None, "gain_pwm": None, "look_pwm": None, "rc1_raw": None, "rssi": None,
                     "cruise_pwm": None, "rc2_stick": None,
                     "servo1": None, "servo3": None, "servo3_seq": 0, "mode": None, "armed": None,
                     "vbat": None, "ibat": None}     # v12.1 (2026-09-10): pack voltage (V) / current (A) from SYS_STATUS
        self._output_thread = None       # first sender owns all subsequent actuator writes
        self.tele_t = {"rc": 0.0, "servo": 0.0, "hb": 0.0}
        self.servo_stamp = None
        self.lock = threading.RLock()     # v5: the wheel forwarder polls/sends from its own thread
        for _ in range(3):
            self.m.mav.request_data_stream_send(self.m.target_system, self.m.target_component,
                                                mavutil.mavlink.MAV_DATA_STREAM_RC_CHANNELS, 50, 1)   # v5: 50 Hz so the wheel forwarder sees fresh stick values
        self.modes = {v: k for k, v in (self.m.mode_mapping() or {}).items()}
        self.heading_startup = None
        if heading is not None:
            # v12.1: SYS_STATUS (pack voltage/current) at 2 Hz. 2026-09-10 15:10: the truck could not hold cruise on a pack
            # the operator believed was full, and nothing in the log could say what the pack was doing.
            self.m.mav.request_data_stream_send(self.m.target_system, self.m.target_component,
                                                mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 2, 1)
            from heading_telemetry import negotiate
            self.heading_startup = negotiate(self.m, self._ingest, heading_receipt_path, servo=recovery)
            print("heading startup:", self.heading_startup["status"], self.heading_startup["cadence"])
        else:
            # v4 test: RC_CHANNELS_RAW (msg 35) is not in any default stream -- ask for it at 20 Hz.
            self.m.mav.command_long_send(self.m.target_system, self.m.target_component,
                                         mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, 35, 50000, 0, 0, 0, 0, 0)
            if recovery:
                self.m.mav.command_long_send(self.m.target_system, self.m.target_component,
                                             mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, 36, 20000, 0, 0, 0, 0, 0)
            # v12.1: SYS_STATUS (pack voltage/current) at 2 Hz. 2026-09-10 15:10: the truck could not hold cruise on a pack
            # the operator believed was full, and nothing in the log could say what the pack was doing.
            self.m.mav.request_data_stream_send(self.m.target_system, self.m.target_component,
                                                mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 2, 1)
    def poll(self, max_msgs=64):
        """Drain pending telemetry without blocking; return the latest values + their ages (ms)."""
        with self.lock: return self._poll(max_msgs)
    def _poll(self, max_msgs=64):
        for _ in range(max_msgs):
            r = self.m.recv_match(blocking=False)
            if r is None: break
            self._ingest(r, time.monotonic())
        now = time.monotonic()
        out = dict(self.tele)
        out["rc_age_ms"] = None if not self.tele_t["rc"] else round((now - self.tele_t["rc"]) * 1000)
        out["hb_age_ms"] = None if not self.tele_t["hb"] else round((now - self.tele_t["hb"]) * 1000)
        out["servo3_age_ms"] = None if not self.tele_t["servo"] else round((now - self.tele_t["servo"]) * 1000)
        out["rc1_steer_norm"] = None if out["rc1"] is None else round(self.steer_from_pwm(out["rc1"]), 4)
        return out
    def _ingest(self, r, now):
        t = r.get_type()
        if t == "RC_CHANNELS":
            self.tele.update(rc1=r.chan1_raw, rc2=r.chan2_raw, rc3=r.chan3_raw, rc5=r.chan5_raw,
                             rc6=r.chan6_raw, rc8=r.chan4_raw, gain_pwm=r.chan8_raw, look_pwm=r.chan7_raw, rssi=r.rssi,
                             cruise_pwm=r.chan9_raw, rc2_stick=r.chan10_raw)
            # "rc8" = the wheel copy (2026-09-07). gain_pwm = the gain trim x400.
            # v6: cruise_pwm = the speed dial x400 (the cruise level); rc2_stick = the trigger copy, the human's
            # REAL trigger -- the throttle channel itself reports OUR override once we send one, never the stick.
            self.tele_t["rc"] = now
        elif t == "RC_CHANNELS_RAW":      # v4 test: does this show the STICK while the steering channel is overridden?
            self.tele.update(rc1_raw=r.chan1_raw)
        elif (t == "SERVO_OUTPUT_RAW" and r.port == 0
              and r.get_srcSystem() == self.m.target_system
              and r.get_srcComponent() == self.m.target_component):
            # A repeated packet cannot fill a neutral dwell. Accept forward
            # uint32 source-time movement, including wrap, but not replay.
            delta = None if self.servo_stamp is None else (r.time_usec - self.servo_stamp) % (1 << 32)
            if delta is None or 0 < delta < (1 << 31):
                self.servo_stamp = r.time_usec
                self.tele.update(servo1=r.servo1_raw, servo3=r.servo3_raw,
                                 servo3_seq=self.tele["servo3_seq"] + 1)
                self.tele_t["servo"] = now
        elif (t == "ATTITUDE" and self.heading is not None
              and r.get_srcSystem() == self.m.target_system
              and r.get_srcComponent() == self.m.target_component):
            self.heading.ingest_attitude(r.time_boot_ms, now, r.roll, r.pitch, r.yaw,
                                         r.rollspeed, r.pitchspeed, r.yawspeed)
        elif t == "SYS_STATUS":           # v12.1: voltage_battery mV (65535 = none), current_battery cA (-1 = none)
            if 0 < r.voltage_battery < 65535: self.tele["vbat"] = round(r.voltage_battery / 1000.0, 2)
            if r.current_battery >= 0: self.tele["ibat"] = round(r.current_battery / 100.0, 1)
        elif t == "HEARTBEAT" and r.type != 6:            # 6 = GCS, ignore our own
            from pymavlink import mavutil
            self.tele.update(mode=self.modes.get(r.custom_mode, r.custom_mode),
                             armed=bool(r.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))
            self.tele_t["hb"] = now
    def steer_from_pwm(self, pwm):
        """Inverse of pwm(): radio PWM -> steer_norm in the tool's convention (+ = right)."""
        if pwm >= self.rc_trim:
            f = (pwm - self.rc_trim) / max(1, self.rc_max - self.rc_trim)
        else:
            f = (pwm - self.rc_trim) / max(1, self.rc_trim - self.rc_min)
        return max(-1.0, min(1.0, f)) * self.sign
    def _params(self, names):
        got = {}
        for name in names:
            self.m.mav.param_request_read_send(self.m.target_system, self.m.target_component, name.encode(), -1)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.5:
                r = self.m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
                if r and r.param_id == name: got[name] = r.param_value; break
        return got
    def pwm(self, steer_norm):
        f = max(-1.0, min(1.0, steer_norm)) * self.sign
        span = (self.rc_max - self.rc_trim) if f >= 0 else (self.rc_trim - self.rc_min)
        return int(round(self.rc_trim + f * span))
    def send(self, pwm, thr):
        """Explicit channel pair; no cached throttle. Only the output thread may send."""
        with self.lock:
            thread = threading.get_ident()
            if self._output_thread is None: self._output_thread = thread
            if self._output_thread != thread: raise RuntimeError("actuator write outside output owner")
            self.m.mav.rc_channels_override_send(self.m.target_system, self.m.target_component,
                                                 int(pwm), int(thr), 0, 0, 0, 0, 0, 0)
    def release(self, n=20):
        for _ in range(n):
            self.send(0, 0)
            time.sleep(0.02)

# ---------------------------------------------------------------------------------------------
# 3a. Cruise throttle (v6, 2026-09-08): hold a fixed throttle on the throttle channel so a tuning pass is one
# cell and not also a hand-throttle (ANALYSIS-2026-09-08-tuning-process 6B, 9).
#
# THE HUMAN ALWAYS WINS. The trigger is read from the trigger copy, a radio mixer copy of the throttle channel -- NOT from it
# itself, because once the tool overrides a channel ArduPilot's RC_CHANNELS reports the OVERRIDE
# on it, not the stick (the same trap the wheel copy solves for steering). Any movement of the
# real trigger, FORWARD OR BRAKE/REVERSE, is |stick - neutral| > band and drops cruise on that
# frame; the tool then sends 0 on the throttle channel and the FC hands throttle back to the receiver, which
# is already carrying the human's demand, so the very pull that broke the neutral band is what
# drives the truck. A brake pull is the same path and is not slowed by cruise for even one frame.
# If the trigger copy is not mapped, unreadable or out of range, cruise NEVER engages -- with no view of the
# real trigger the tool cannot know whether the human is asking for anything.
# ---------------------------------------------------------------------------------------------
def brake_latch_update(latched, stick, hold_sw, thr_neutral, band):
    """v13d (2026-09-12 09:50, the reverse finding): a brake/reverse pull LATCHES cruise off; the latch clears on a FORWARD
    trigger pull or the HOLD switch. Without it cruise re-engaged the instant the trigger returned to neutral between the
    XL-5's two reverse taps, so the ESC saw forward, never neutral, and reverse almost never engaged (run 091339, 69 pulls)."""
    if stick is None: return latched
    if stick < thr_neutral - band: return True
    if stick > thr_neutral + band or hold_sw: return False
    return latched

def cruise_setpoint_mps(a, ch9):
    """v15.2 -> the SPEED setpoint the dial is asking for (m/s), or None when --cruise-mps is off / the dial is
    at its bottom tenth (= stopped, the trigger owns throttle). Pure, so the decider and the loop agree."""
    mps = getattr(a, "cruise_mps", None)
    if not mps or not ch9 or not (900 <= ch9 <= 2100) or ch9 <= 1100: return None
    lo, hi = float(mps[0]), float(mps[1])
    return lo + (min(ch9, 2000) - 1100) / 900.0 * (hi - lo)


class CruiseSpeedLoop:
    """v15.2: hold a commanded m/s by trimming the cruise PWM, from the phone's 15 Hz pose speed.

    out = clamp(feed_forward(setpoint) + Kp*e + I),  e = setpoint - v,  I integrated at Ki with conditional
    integration (the integral only moves while the output is off its limits, or while it is unwinding).

    SAFETY. The loop can only ever move the throttle inside [lo, hi] = [the band floor, --cruise-max], which are
    the same limits a dialled PWM obeys; it never reverses and never exceeds the ceiling. With NO usable pose it
    HOLDS the last output and freezes the integrator -- it never ramps blind, because the one failure that matters
    is a stale speed reading being read as "too slow" (FINDINGS 67: a wound-up throttle in a bend is a crash).
    Disengaging cruise resets it, so the truck cannot come back with a wound-up integrator."""

    def __init__(self, a, lo_pwm, hi_pwm):
        self.a = a; self.lo = float(lo_pwm); self.hi = float(hi_pwm); self._output_epoch = None; self.reset()

    def reset(self):
        self.i = 0.0; self.t = None; self.out = None
        self.launch_reset_pending = bool(getattr(self.a, "cruise_launch_reset", False))

    def reset_for_epoch(self, epoch):
        """The sender can disengage and rearm while the camera loop is frozen."""
        if epoch != self._output_epoch:
            self.reset(); self._output_epoch = epoch

    def update(self, ff_pwm, sp, v, v_age_ms, now, engaged):
        """-> (pwm, record dict or None). `ff_pwm` is cruise_decide's feed-forward level."""
        if not engaged or sp is None or sp <= 0.02:
            self.reset(); return ff_pwm, None
        dt = 0.0 if self.t is None else max(0.0, min(0.5, now - self.t)); self.t = now
        why = None
        if v is None: why = "no_pose"
        elif v_age_ms is None or v_age_ms > self.a.pose_max_age_ms: why = "pose_stale"
        if why is not None:                                            # HOLD, never ramp blind
            out = self.out if self.out is not None else float(ff_pwm)
            return int(round(out)), dict(sp=round(sp, 3), v=None, i=round(self.i, 1), ff=int(ff_pwm),
                                         out=int(round(out)), why=why)
        # Optional experiment: shed break-away trim once motion is observed. The
        # existing engagement/owner epoch resets rearm this; a later slowdown or
        # battery sag must not discard the ordinary integral again. Consume even
        # when I <= 0, and keep negative trim. Missing/stale pose returned above.
        launch_reset = False; removed_i = 0.0
        launch_threshold = min(0.10, sp * 0.5)
        if self.launch_reset_pending and v >= launch_threshold:
            removed_i = max(0.0, self.i)
            self.i -= removed_i
            self.launch_reset_pending = False
            launch_reset = True
        e = sp - v
        i_try = max(-self.a.cruise_i_max, min(self.a.cruise_i_max, self.i + self.a.cruise_ki * e * dt))
        raw = ff_pwm + self.a.cruise_kp * e + i_try
        if self.lo < raw < self.hi or (raw >= self.hi and e < 0) or (raw <= self.lo and e > 0):
            self.i = i_try                                             # conditional integration (anti-windup)
        out = max(self.lo, min(self.hi, ff_pwm + self.a.cruise_kp * e + self.i))
        self.out = out
        record = dict(sp=round(sp, 3), v=round(v, 3), e=round(e, 3), i=round(self.i, 1),
                      ff=int(ff_pwm), out=int(round(out)), why=None)
        if getattr(self.a, "cruise_launch_reset", False):
            record["launch_reset"] = dict(threshold_mps=round(launch_threshold, 4),
                                          triggered=launch_reset, removed_i_us=round(removed_i, 1),
                                          pending=self.launch_reset_pending)
        return int(round(out)), record


def cruise_decide(a, tele, hold_sw, thr_neutral=CRUISE_THR_NEUTRAL, thr_dz=CRUISE_THR_DZ, brake_latched=False):
    """-> (level_pwm or None, reason or None). reason None = cruise is ENGAGED this frame.

    `level_pwm` is what the flags/trim ASK for (logged even when disengaged, so a log says what
    the pass was set to); it is only commanded when reason is None. Every condition here is a
    veto: the first one that fails disengages, and the caller then sends 0 on the throttle channel."""
    if not getattr(a, "cruise", False): return None, "off"
    if tele is None: return None, "no_fc"
    band = max(thr_dz, CRUISE_MIN_BAND)
    ceiling = min(int(a.cruise_max), CRUISE_ABS_MAX)

    # the commanded level: live from the speed dial, or the fixed --cruise-pwm
    if a.cruise_trim:
        ch9 = tele.get("cruise_pwm")
        if not ch9 or not (900 <= ch9 <= 2100): return None, "no_ch9"
        mps = getattr(a, "cruise_mps", None)
        band_us = getattr(a, "cruise_band", None)
        if mps:
            # v15.2: the dial is a SPEED. `level` here is only the FEED-FORWARD guess for that speed, so that every
            # veto below (above all "stopped") behaves exactly as it does for a dialled PWM; the PI trim is applied
            # by the caller, which alone can see the pose. Dial at the bottom = stopped = the trigger owns throttle.
            sp = cruise_setpoint_mps(a, ch9)
            level = thr_neutral if not sp else int(round(CRUISE_FF_US + CRUISE_US_PER_MPS * sp))
        elif band_us:
            # v15.1 (operator, 2026-09-13 14:0x, "a consistent cruise for the rest of the testing"): the speed dial is the CRUISE DIAL over a NARROW band.
            # The bottom tenth of the trim (<= 1100) = STOPPED = the receiver owns throttle (your trigger); the rest maps linearly
            # LO..HI us, so at 1625..1725 one trim click (~65 us on the channel) is ~7 us of throttle instead of 16. Absent = the v6 formula.
            lo, hi = int(band_us[0]), int(band_us[1])
            level = thr_neutral if ch9 <= 1100 else int(round(lo + (min(ch9, 2000) - 1100) / 900.0 * (hi - lo)))
        else:
            level = int(round(thr_neutral + (ch9 - 1000) / 1000.0 * CRUISE_SPAN_US))
    else:
        level = int(a.cruise_pwm)
    level = max(thr_neutral, min(ceiling, level))      # never above the ceiling, never in reverse

    # RC telemetry must be fresh: a stale RC_CHANNELS means we cannot see the trigger or the switch
    age = tele.get("rc_age_ms")
    if age is None or age > CRUISE_RC_MAX_AGE_MS: return level, "rc_stale"
    # trim wound down to its minimum = STOPPED, the always-available way to disarm cruise
    if level <= thr_neutral + band: return level, "stopped"
    # the real trigger, from the trigger mixer copy -- forward OR brake/reverse hands throttle back
    stick = tele.get("rc2_stick")
    if not stick or not (900 <= stick <= 2100): return level, "no_ch10"
    if abs(stick - thr_neutral) > band: return level, "human_trigger"
    if brake_latched: return level, "post_brake"       # v13d: a brake pull holds cruise off until a forward pull / HOLD
    if hold_sw: return level, "hold_sw"                # mode switch at the HOLD end: the FC's own stop
    if tele.get("mode") != "MANUAL": return level, "mode"
    if not tele.get("armed"): return level, "disarmed"
    return level, None

# ---------------------------------------------------------------------------------------------
# Independent output: output_owner.py owns both channels. Preserve this wheel
# policy as a pure helper; the sender applies it from the freshly polled physical wheel copy.
# ---------------------------------------------------------------------------------------------
def override_weight(wheel, now, last_touch, dz, hold_ms):
    """v13d OVERRIDE (operator's ruling 2026-09-12 09:35): the steering head steers until the human touches the wheel; then the wheel
    steers ENTIRELY; the steering head gets the servo back --override-hold-ms after the wheel returns inside its deadband.
    Returns (w, last_touch): w = 1.0 model / 0.0 wheel (the blend's own convention), last_touch = time of the last touch."""
    if abs(wheel) > dz: last_touch = now
    w = 0.0 if (now - last_touch) * 1000.0 < hold_ms else 1.0
    return w, last_touch

# ---------------------------------------------------------------------------------------------
# 3b. Recording off the control path (external review 2026-09-13, verified in our own logs).
#
# Until this date the frame loop wrote the jsonl line, two JPEGs and -- every 500 frames -- np.savez_compressed of the
# WHOLE probability history, inline. The checkpoint interval grew with run length (102 ms at frame 500, 1,034 ms at
# 11,500) and past ~frame 5,000 it exceeded the wheel forwarder's --hold-ms 500 watchdog, which then substituted ZERO
# steering: 215204 frame 7000 -> 7001, dt 650 ms, servo 1811 -> 1463 us at full lock at 0.56 m/s. Every run over ~5.5 min
# on 2026-09-12 had its steering centred for a beat every 33 s. The per-frame `ms` never showed it (measured before the writes).
#
# Now: ONE bounded queue and one writer thread. The loop hands over the line, the arrays and the images and never
# touches the disk. Probabilities go out in fixed-size CHUNKS (probs_chunk_NNNN.npz, PROBS_CHUNK frames each) and are
# merged into probs.npz at exit, off the control path; a hard power-off leaves the chunks, which replay_fit_sweep.py
# reads directly. If the writer falls behind the queue fills and items are DROPPED and counted (rec["rec_dropped"]),
# never queued without bound and never allowed to block steering. The watchdog is unchanged.
# ---------------------------------------------------------------------------------------------
PROBS_CHUNK = 500
class LogWriter(threading.Thread):
    """Two queues: ESSENTIAL (jsonl lines, probability chunks, close) is never dropped -- it is ~1 KB per frame and a
    chunk per 500, so a stalled disk grows it slowly and boundedly by run length; IMAGES (feed + stills) are the only
    thing that may be dropped, and are, with a count, once their queue holds `img_max` of them."""
    def __init__(self, logdir, jsonl, img_max=64):
        super().__init__(daemon=True)
        self.logdir = Path(logdir); self.f = jsonl
        self.q = queue.Queue(); self.qimg = queue.Queue(maxsize=img_max)
        self.dropped = 0; self.written = 0; self.chunks = 0; self.err = None
        self.last_sync = 0.
    def put(self, kind, *args):
        if kind == "jpg":
            try: self.qimg.put_nowait(args)
            except queue.Full: self.dropped += 1
        else: self.q.put((kind, args))
    def qsize(self): return self.q.qsize() + self.qimg.qsize()
    def _do(self, kind, args):
        import cv2
        try:
            if kind == "line":
                self.f.write(args[0]); self.f.flush()
                if time.monotonic() - self.last_sync >= 1.:
                    os.fsync(self.f.fileno()); self.last_sync = time.monotonic()
            elif kind == "jpg": cv2.imwrite(str(args[0]), args[1], [cv2.IMWRITE_JPEG_QUALITY, args[2]])
            elif kind == "rm":
                try: Path(args[0]).unlink()
                except FileNotFoundError: pass
            elif kind == "chunk":
                k, probs, danger = args
                from durable_run import atomic_npz
                atomic_npz(self.logdir / f"probs_chunk_{k:04d}.npz", probs=np.stack(probs),
                                    danger=np.stack(danger) if danger else np.zeros((0,)))
                self.chunks += 1
            elif kind == "sweep-checkpoint":
                from durable_run import atomic_json
                self.f.flush(); os.fsync(self.f.fileno())
                atomic_json(self.logdir / "sweep-progress.json", args[0])
            elif kind == "setup-observation":
                observer, snapshot, frame, final = args
                observer(self.logdir, snapshot, frame=frame, final=final)
            self.written += 1
        except Exception as e:
            self.err = repr(e)
    def run(self):
        closing = False
        while True:
            try:
                kind, args = self.q.get_nowait()          # essential first, always
                if kind == "close": closing = True
                else: self._do(kind, args); continue
            except queue.Empty:
                pass
            if closing and self.q.empty():
                while not self.qimg.empty():              # drain whatever images are left, then go
                    self._do("jpg", self.qimg.get_nowait())
                self.f.flush(); os.fsync(self.f.fileno()); self.f.close(); return
            try: self._do("jpg", self.qimg.get(timeout=0.05))
            except queue.Empty: pass
    def close(self, timeout=60.0):
        self.put("close"); self.join(timeout)
def merge_probs_chunks(logdir, NR, GW):
    """probs_chunk_*.npz -> probs.npz (same layout as before the writer), at exit and off the control path."""
    logdir = Path(logdir); files = sorted(glob.glob(str(logdir / "probs_chunk_*.npz")))
    chunks, dang = [], []
    for f in files:
        with np.load(f) as z:
            chunks.append(z["probs"])
            if z["danger"].size: dang.append(z["danger"])
    probs = np.concatenate(chunks) if chunks else np.zeros((0, NR, GW), np.float16)
    from durable_run import atomic_npz
    atomic_npz(logdir / "probs.npz", probs=probs, danger=np.concatenate(dang) if dang else np.zeros((0,)))
    # Keep completed chunks: they are independent recovery/offload units.
    return probs.shape[0], len(files)

# ---------------------------------------------------------------------------------------------
# 4. Frame sources: Moblin over iproxy (live) or a corpus rgb.mp4 (identical downstream path)
# ---------------------------------------------------------------------------------------------
def rd(s, n):
    b = bytearray()
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c: return None
        b += c
    return bytes(b)

def msg(s):
    h = rd(s, 5)
    if h is None: return None, None
    t, ln = struct.unpack(">BI", h)
    return t, (rd(s, ln) if ln else b"")

def params_from(rec, codec):
    out = b""
    if codec == 0:                                    # avcC
        n = rec[5] & 0x1F; i = 6
        for _ in range(n):
            L = struct.unpack(">H", rec[i:i+2])[0]; i += 2; out += SC + rec[i:i+L]; i += L
        npps = rec[i]; i += 1
        for _ in range(npps):
            L = struct.unpack(">H", rec[i:i+2])[0]; i += 2; out += SC + rec[i:i+L]; i += L
        return out, (rec[4] & 3) + 1
    nls = (rec[21] & 3) + 1; na = rec[22]; i = 23     # hvcC
    for _ in range(na):
        i += 1
        cnt = struct.unpack(">H", rec[i:i+2])[0]; i += 2
        for _ in range(cnt):
            L = struct.unpack(">H", rec[i:i+2])[0]; i += 2; out += SC + rec[i:i+L]; i += L
    return out, nls

def moblin_frames(port, secs, ingest="async"):
    """--ingest async (default since 2026-09-13, external review step 3): reception and decoding run continuously in
    their own threads, independent of the perception loop; the loop wakes when a decoded frame is available and takes the
    NEWEST one (older undelivered frames are skipped and counted). Each image carries its own stamps: pts (the phone's
    capture clock, via the FIFO), t_rx (packet received), t_dec (out of ffmpeg), and the loop adds t_take. Before this,
    the loop read one packet per iteration and only then picked up whatever ffmpeg had finished -- up to one 66.7 ms
    frame period of avoidable age, and a packet backlog whenever inference ran long. Nothing about geometry, the perception model or
    steering changes. `PHONE[...]` is written from the taken snapshot at yield time, never from the reader threads."""
    if ingest == "sync":
        yield from moblin_frames_sync(port, secs); return
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    s.sendall(struct.pack(">BIB", HOST_HELLO, 1, 1)); HOST_SOCK[0] = s
    print("connected to Moblin (async ingest); waiting for videoConfig ...")
    cond = threading.Condition(); slot = {"img": None, "pts": None, "depth": None, "t_rx": None, "t_dec": None, "seq": 0,
                                          "pts_rx": None, "dead": None}
    pending = deque()                                 # (pts_us, t_rx) of access units written to ffmpeg, oldest first
    def _die(why):
        with cond: slot["dead"] = why; cond.notify_all()
    def _reader():
        dec = None; nls = 4; W = H = None
        cur = [None]                                  # the decoder that is live; a retired one's drain must not kill the run
        try:
            while True:
                t, p = msg(s)
                if t is None: _die("stream closed"); return
                if t == DEVICE_HELLO:
                    try:
                        PHONE["hello"] = json.loads(p[5:5 + struct.unpack(">I", p[1:5])[0]])
                        h = PHONE["hello"]; off = h.get("utc_s", 0) - time.time()
                        print(f"hello: {h.get('name')} {h.get('app', '')} {h.get('app_version', h.get('version'))} pitch={h.get('pitch_deg')} "
                              f"disk={h.get('disk_free_mb')}MB recording={h.get('recording')} phone-Orin clock offset {off:+.1f} s")
                    except Exception as e: print(f"hello: unreadable ({e})")
                elif t == STATUS:
                    try:
                        PHONE["status"] = json.loads(p); PHONE["status_t"] = time.monotonic(); note_pose(PHONE["status"])
                        u = PHONE["status"].get("utc_s")
                        if isinstance(u, (int, float)):
                            PHONE["clock_offs"].append(u - time.time()); PHONE["clock_off"] = max(PHONE["clock_offs"])
                    except Exception: pass
                elif t == VIDEO_CONFIG:
                    codec = p[0]; W, H = struct.unpack(">HH", p[1:5]); ln = struct.unpack(">I", p[5:9])[0]
                    PHONE["video_config"] = {"width": W, "height": H, "codec": codec, "received_epoch": time.time()}
                    params, nls = params_from(p[9:9+ln], codec)
                    print(f"codec={'H264' if codec == 0 else 'HEVC'} {W}x{H}")
                    if W * 3 != H * 4:   # 2026-10-03 00:0x (operator): any 4:3 size is the same field of view block-meaned on the phone; the feed is a resize of the whole frame
                        print(f"REFUSING: stream is {W}x{H}, not 4:3 (corpus geometry {SRC_W}x{SRC_H}). "
                              f"Set Moblin to 1440p (4:3) and verify from the wire (FINDINGS 20)."); _die("geometry"); return
                    if (W, H) != (SRC_W, SRC_H): print(f"stream {W}x{H}: 4:3 at 1/{SRC_W // W}, resized to the {FEED_W}x{FEED_H} feed as the full frame would be")
                    if dec is not None:
                        # 2026-10-02: since robot-eyes ticket 10 the phone sends a videoConfig every time its Record view
                        # reopens (the host keeps its connection). Retire the old ffmpeg instead of leaking one per reopen.
                        old, dec = dec, None; cur[0] = None   # cur first: the old drain thread wakes on the kill and must see it is retired
                        with cond: pending.clear()            # stamps of frames the old decoder never emitted: left in, every frame after the reopen takes the stamp of the one before it (Orin + Mac, 2026-10-02 19:1x)
                        try: old.stdin.close()
                        except Exception: pass
                        old.kill()
                        try: old.wait(timeout=2)              # reap it, or pgrep/ps keep showing a zombie decoder
                        except Exception: pass
                    fb = W * H * 3
                    # 2026-09-13 step 4a tried NVIDIA's hardware decoder (hevc_nvv4l2dec through ffmpeg) and WITHDREW it the same night:
                    # by picture COUNT it holds one frame, but by picture CONTENT it delivers 1-13 frames of older material from its
                    # output pool (FINDINGS 58 erratum, dec_content.py on the real stream). Software -threads 2 has zero content lag and,
                    # with the boundary NAL + probe flags below, 93 ms median fed->picture (was 160). hw is kept for A/B only.
                    dcodec = (["-c:v", "hevc_nvv4l2dec" if codec else "h264_nvv4l2dec"] if DECODER[0] == "hw" else ["-threads", "1"])
                    # 2026-09-13: -probesize 32 -analyzeduration 0 -- ffmpeg's stream probe held ~80-129 access units (5.3-8.7 s) before
                    # the first picture; the params are in-band and the geometry is checked above, so nothing needs probing. Benched on the
                    # real stream: first frame after 6 AUs instead of 79, and the per-frame tail is gone (aud path p90 27.6, max 28.9 ms).
                    # -fflags nobuffer / -flags low_delay were tried and made the hardware path WORSE (93 ms, one frame held): leave them out.
                    dec = subprocess.Popen(
                        ["ffmpeg", "-loglevel", "quiet", "-probesize", "32", "-analyzeduration", "0", *dcodec,
                         "-f", "hevc" if codec else "h264", "-i", "pipe:0",
                         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                    dec.stdin.write(params); dec.stdin.flush(); cur[0] = dec
                    def _drain(fh=dec.stdout, fb=fb, H=H, W=W, proc=dec):
                        while True:
                            raw = fh.read(fb)
                            if not raw or len(raw) < fb:
                                if proc is cur[0]: _die("decoder ended")   # a retired decoder ending is expected
                                return
                            if proc is not cur[0]: return     # a frame the kill flushed out of the retired decoder: drop it, its stamp is gone
                            a_ = np.frombuffer(raw, np.uint8).reshape(H, W, 3); t_dec = time.monotonic()
                            with cond:                # one decoded frame per access unit, in order: take the oldest stamp
                                pts, t_rx = pending.popleft() if pending else (None, None)
                                slot.update(img=a_, pts=pts, depth=len(pending), t_rx=t_rx, t_dec=t_dec, seq=slot["seq"] + 1)
                                cond.notify()
                    threading.Thread(target=_drain, daemon=True).start()
                elif t == VIDEO_FRAME and dec:
                    pts_rx = struct.unpack(">Q", p[:8])[0]; t_rx = time.monotonic()
                    au = p[9:]; buf = bytearray(); i = 0
                    while i + nls <= len(au):
                        L = int.from_bytes(au[i:i+nls], "big"); i += nls
                        buf += SC + au[i:i+L]; i += L
                    if AU_DELIM[0]: buf += AU_END_HEVC if codec else AU_END_H264   # end this AU now, do not wait for the next one
                    with cond:
                        pending.append((pts_rx, t_rx)); slot["pts_rx"] = pts_rx
                        if len(pending) > DEC_MAX_DEPTH:
                            while len(pending) > DEC_MAX_DEPTH: pending.popleft()
                            PHONE["dec_desync"] += 1
                    try: dec.stdin.write(bytes(buf)); dec.stdin.flush()
                    except BrokenPipeError: _die("decoder died"); return
        except Exception as e:
            _die(f"reader error: {e!r}")
    threading.Thread(target=_reader, daemon=True).start()
    t_end = time.monotonic() + secs if secs else None; last = 0
    while t_end is None or time.monotonic() < t_end:
        with cond:
            while slot["seq"] == last and slot["dead"] is None:
                if not cond.wait(timeout=1.0) and t_end is not None and time.monotonic() >= t_end: break
            if slot["dead"] is not None and slot["seq"] == last: print(slot["dead"]); return
            if slot["seq"] == last: continue
            img, pts, depth, t_rx, t_dec, seq, pts_rx = (slot["img"], slot["pts"], slot["depth"], slot["t_rx"], slot["t_dec"],
                                                        slot["seq"], slot["pts_rx"])
        PHONE["pts_us"], PHONE["dec_depth"], PHONE["pts_rx_us"] = pts, depth, pts_rx
        PHONE["t_rx"], PHONE["t_dec"], PHONE["t_take"], PHONE["skipped"] = t_rx, t_dec, time.monotonic(), seq - last - 1
        last = seq
        yield img

def moblin_frames_sync(port, secs):
    """The pre-2026-09-13 reader (--ingest sync): one packet per loop iteration, the decoded frame picked up only when the
    next packet arrives. Kept for A/B. Yields full-resolution BGR frames (SRC_H, SRC_W, 3), the freshest available."""
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    s.sendall(struct.pack(">BIB", HOST_HELLO, 1, 1)); HOST_SOCK[0] = s
    print("connected to Moblin; waiting for videoConfig ...")
    frames = deque(maxlen=2); qlock = threading.Lock(); dec = None; nls = 4
    pending = deque()                                 # pts_us of access units written to ffmpeg, oldest first
    W = H = None; frame_bytes = None; t_end = time.monotonic() + secs if secs else None
    while t_end is None or time.monotonic() < t_end:
        t, p = msg(s)
        if t is None: print("stream closed"); return
        if t == DEVICE_HELLO:
            try:
                PHONE["hello"] = json.loads(p[5:5 + struct.unpack(">I", p[1:5])[0]])
                h = PHONE["hello"]; off = h.get("utc_s", 0) - time.time()
                print(f"hello: {h.get('name')} {h.get('app', '')} {h.get('app_version', h.get('version'))} pitch={h.get('pitch_deg')} "
                      f"disk={h.get('disk_free_mb')}MB recording={h.get('recording')} phone-Orin clock offset {off:+.1f} s")
            except Exception as e: print(f"hello: unreadable ({e})")
        elif t == STATUS:
            try:
                PHONE["status"] = json.loads(p); PHONE["status_t"] = time.monotonic(); note_pose(PHONE["status"])
                u = PHONE["status"].get("utc_s")
                if isinstance(u, (int, float)):           # phone minus Orin; transport delay only lowers it, so keep the max of a window
                    PHONE["clock_offs"].append(u - time.time()); PHONE["clock_off"] = max(PHONE["clock_offs"])
            except Exception: pass
        elif t == VIDEO_CONFIG:
            codec = p[0]; W, H = struct.unpack(">HH", p[1:5]); ln = struct.unpack(">I", p[5:9])[0]
            PHONE["video_config"] = {"width": W, "height": H, "codec": codec, "received_epoch": time.time()}
            params, nls = params_from(p[9:9+ln], codec)
            print(f"codec={'H264' if codec == 0 else 'HEVC'} {W}x{H}")
            if W * 3 != H * 4:
                print(f"REFUSING: stream is {W}x{H}, not 4:3 (corpus geometry {SRC_W}x{SRC_H}). "
                      f"Set Moblin to 1440p (4:3) and verify from the wire (FINDINGS 20)."); return
            if dec is not None:                           # 2026-10-02: one videoConfig per Record-view reopen (robot-eyes ticket 10); retire, do not leak
                old, dec = dec, None
                with qlock: pending.clear()               # see moblin_frames: the retired decoder's unemitted stamps would mis-tag every later frame
                try: old.stdin.close()
                except Exception: pass
                old.kill()
                try: old.wait(timeout=2)
                except Exception: pass
            frame_bytes = W * H * 3
            dec = subprocess.Popen(
                # 2026-09-12 (operator's ruling after FINDINGS 54): -threads 2. ffmpeg's default frame-threading across the 6
                # cores held 7 decoded-but-unreleased frames = 533 ms of stale image (dec_depth in frames.jsonl). Real 1440p
                # 8 Mbit HEVC on the Orin: auto 55 fps / 7 frames; 2 threads 28 fps / ~1-2 frames; 1 thread 18 fps, too close
                # to the 15 fps stream beside the perception model's 45 ms. Expect dec_depth 1-2 and capture->record ~200-250 ms.
                ["ffmpeg", "-loglevel", "quiet", "-probesize", "32", "-analyzeduration", "0", "-threads", "2",
                 "-f", "hevc" if codec else "h264", "-i", "pipe:0",
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            dec.stdin.write(params); dec.stdin.flush()
            def _drain(fh=dec.stdout, fb=frame_bytes):
                while True:
                    raw = fh.read(fb)
                    if not raw or len(raw) < fb: return
                    a_ = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
                    with qlock:                       # one decoded frame per access unit, in order: take the oldest stamp
                        pts = pending.popleft() if pending else None
                        frames.append((a_, pts, len(pending)))
            threading.Thread(target=_drain, daemon=True).start()
        elif t == VIDEO_FRAME and dec:
            pts_rx = struct.unpack(">Q", p[:8])[0]
            au = p[9:]; buf = bytearray(); i = 0
            while i + nls <= len(au):
                L = int.from_bytes(au[i:i+nls], "big"); i += nls
                buf += SC + au[i:i+L]; i += L
            if AU_DELIM[0]: buf += AU_END_HEVC if codec else AU_END_H264
            with qlock:
                pending.append(pts_rx)
                if len(pending) > DEC_MAX_DEPTH:      # the decoder swallowed an access unit: drop the stale head, count it
                    while len(pending) > DEC_MAX_DEPTH: pending.popleft()
                    PHONE["dec_desync"] += 1
            try: dec.stdin.write(bytes(buf)); dec.stdin.flush()
            except BrokenPipeError: print("decoder died"); return
            with qlock:
                item = frames.pop() if frames else None; frames.clear()
            if item is not None:
                img, PHONE["pts_us"], PHONE["dec_depth"] = item
                PHONE["pts_rx_us"] = pts_rx
                yield img

def video_frames(path, start, step, secs, fps=0.0):
    """fps > 0 paces playback to that many SOURCE frames per second (corpus is 5 fps), so the
    wheels move at the trail's real tempo when --fc is attached."""
    import cv2
    cap = cv2.VideoCapture(str(path)); fi = 0
    if start: cap.set(cv2.CAP_PROP_POS_FRAMES, start); fi = start
    t_end = time.monotonic() + secs if secs else None; t_start = time.monotonic()
    while t_end is None or time.monotonic() < t_end:
        ok, fr = cap.read()
        if not ok: return
        if (fi - start) % step == 0:
            if fps > 0:
                due = t_start + (fi - start) / fps
                if due > time.monotonic(): time.sleep(due - time.monotonic())
            yield fr
        fi += 1

class RelayLink:
    """Stage 4 (2026-09-30): the latest datagram from a control_cmd relay (not shipped), read on its own thread.
    No ROS in this process. Holds ONE value; the loop reads it with age so a dead relay is visible as staleness,
    never as a held command."""
    def __init__(self, port, sign):
        import socket, threading
        self.sign = sign; self.n = 0; self.bad = 0; self._lock = threading.Lock(); self._last = None; self._t = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port)); self.sock.settimeout(0.5)
        threading.Thread(target=self._reader, daemon=True, name="relay-udp").start()
        print(f"relay link: udp 127.0.0.1:{port}, sign {sign:+g} (truck = sign x planner)")

    def _reader(self):
        import socket as _s
        while True:
            try: data, _ = self.sock.recvfrom(4096)
            except _s.timeout: continue
            except OSError: return
            try:
                d = json.loads(data.decode()); k = float(d["k"])
                if not math.isfinite(k): raise ValueError("k not finite")
                rec = {"seq": int(d.get("seq", 0)), "k": self.sign * k, "ls": bool(d.get("ls", False)),
                       "v": float(d.get("v", 0.0))}
            except Exception:
                self.bad += 1; continue
            with self._lock:
                self._last = rec; self._t = time.monotonic(); self.n += 1

    def latest(self, now, max_age_ms):
        """-> (record, age_ms, fresh) or (None, None, False)."""
        with self._lock:
            rec, t = self._last, self._t
        if rec is None: return None, None, False
        age = (now - t) * 1000.0
        return rec, age, age <= max_age_ms


# ---------------------------------------------------------------------------------------------
# 5. Main loop
# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--head", required=True, help="385-param trail-centre head (.npy)")
    ap.add_argument("--danger-head", help="optional 385-param DangerNet head, logged only")
    ap.add_argument("--trail-head", help="optional 385-param trail/not-trail head (trail-head experiment 2026-09-07); "
                                         "logs 'trail' = mean over band rows of the row-max patch prob, display only")
    ap.add_argument("--r0", type=int, default=8, help="first grid row of the head's band (r8c = 8)")
    ap.add_argument("--pitch", required=True, help="up | normal | down | <degrees below horizon> | live (v12: the phone's own "
                    "ARKit pitch from the 1 Hz status, per frame; --pitch-fallback until it arrives and whenever it is > 3 s stale)")
    ap.add_argument("--pitch-fallback", default="up", help="preset or degrees used by --pitch live before the phone reports (default up)")
    ap.add_argument("--cam-h", type=float, default=CAM_H)
    ap.add_argument("--lookahead", type=float, default=2.0, help="pure-pursuit Ld, m (operator's pick: 2)")
    ap.add_argument("--decoder", choices=("hw", "sw"), default="sw",
                    help="sw (default since 2026-09-13 02:25): software decode, -threads 2, ZERO content lag. hw = NVIDIA nvv4l2dec through "
                         "ffmpeg -- WITHDRAWN: it emits a picture per access unit on time but from an output pool holding 1-13 frames of OLDER "
                         "content, invisible to every count-based stamp (FINDINGS 58 erratum; an offline decoder test, not shipped). Kept for A/B only.")
    ap.add_argument("--au-delim", choices=("on", "off"), default="on",
                    help="on (2026-09-13, review round 3): append a boundary NAL after every AU so ffmpeg's parser releases the picture "
                         "at once instead of after the NEXT AU arrives; hardware fed->decoded 93 -> 27 ms. off = old bytes, for A/B.")
    ap.add_argument("--ingest", choices=("async", "sync"), default="async",
                    help="async (2026-09-13): receive+decode continuously, the loop takes the newest decoded image and logs per-stage "
                         "timestamps (stage_ms). sync: the old one-packet-per-iteration reader, for A/B.")
    ap.add_argument("--cam-fwd", type=float, default=0.0,
                    help="m the LENS sits ahead of the REAR AXLE; pure pursuit's curvature is computed from the axle (2026-09-12: "
                         "measured 0.24 -- wheelbase 0.28, lens 0.04 behind the front axle). 0 = the pre-09-12 behaviour. The "
                         "look-ahead and --fit-max-y stay measured from the lens, so the aimed-at row does not move.")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF, help="per-row confidence gate")
    ap.add_argument("--blue-gate", default=None, metavar="SPEC",
                    help="2026-10-09: keep the head's probability only on patches of the SAME feed pixels that contain Blue #2 "
                         "tape colour (blue_gate.py: HSV 88..128/S>=25/V>=45, B-R>=10, patch fraction >= min_frac, dilated); "
                         "elsewhere cap it at 0.5 (zero line mass). One gated observation feeds the line fit, cam_state and Path "
                         "Recovery. 'default' or 'key=value,...' (h_lo,h_hi,s_min,v_min,b_minus_r,min_frac,dilate). "
                         "Omitted = OFF = byte-identical behaviour; probs chunks always stay the raw head output.")
    ap.add_argument("--cap", type=float, default=DEFAULT_CAP, help="cubic curvature cap; 0 = none")
    ap.add_argument("--cap-kappa", type=float, default=0.0,
                    help="v9: make the curvature cap PHYSICAL. 0 (default) = off, --cap applies in "
                         "patches/row^2 exactly as validated. Above 0, the fallback test instead "
                         "asks whether the fit implies a ground curvature above this many 1/m near "
                         f"the goal point, and --cap is IGNORED. Suggested {DEFAULT_CAP_KAPPA:.2f} "
                         f"(= 2 x full lock). Tight courses need this: --cap 0.05 can never let the "
                         "line exceed 0.6 1/m on the wedge-up mount and the kitchen oval's bends "
                         "are 1.2-1.5 (FINDINGS 37/39). NOT YET DRIVEN.")
    ap.add_argument("--fit-max-y", type=float, default=0.0,
                    help="v11 (2026-09-09): the farthest band row allowed to VOTE in the line fit, as a "
                         "ground distance in metres through --pitch. 0 (default) = off, every kept row "
                         "votes, the validated rule exactly. The line is still drawn and logged on every "
                         "kept row, --min-rows still counts kept rows. Why: on entry frames the cubic "
                         "sits 2.7 patches OUTSIDE the coming bend while the head's own far centroids "
                         "are 1.2 off -- band rows 0-2 (1.5-3 m, looking at the far side of the loop) "
                         "drag the far end straight. Rows <= 1.1 m voting: entry goal-row error 1.11 -> "
                         "0.82 patches, 6.6 -> 3.8 cm at 1 m, straight also better (line_fit_sweep_v3 "
                         "H/I). Suggested 1.12 = KFF_FAR_FRAC x Ld at Ld 0.8. NOT YET DRIVEN.")
    ap.add_argument("--ema", type=float, default=0.0, help="EMA alpha on kappa (0 = off, 0.5 = SYNTHESIS B)")
    ap.add_argument("--kappa-full-lock", type=float, default=KAPPA_FULL_LOCK,
                    help="v10 (2026-09-09): |kappa| at full servo lock, 1/m -- the PLANT constant every "
                         "command is divided by. Default 1.43 (R 0.70, one hand-measured circle). "
                         "FINDINGS 38 MEASURED 1.734/(1+0.22 v^2): 1.66 at 0.45 m/s (kitchen oval), "
                         "1.42 at ~1 m/s (trail). GAIN MULTIPLIES WITH IT: to keep a measured gain's "
                         "behaviour when raising this, multiply --gain by new/old (1.43->1.66: x1.16). "
                         "--kappa-max and the --cap-kappa recommendation follow it automatically.")
    ap.add_argument("--kappa-max", type=float, default=None,
                    help="clip |kappa| (default = --kappa-full-lock)")
    ap.add_argument("--gain", type=float, default=1.0,
                    help="multiplier on kappa before the clip. 1.0 = pure geometry. Replay of bf6c against "
                         "pose labels fitted slope 0.39 (r +0.52), i.e. the line under-steers tight bends; "
                         "raise only if the truck runs wide on bends in the field")
    ap.add_argument("--hold-ms", type=int, default=500, help="no lane: hold last command this long, then release")
    ap.add_argument("--min-rows", type=int, default=0,
                    help="v7: refuse to steer on a line fitted through fewer than N of the 17 band rows; "
                         "hold the last confident command instead (operator's 2026-09-07 rule). 0 = off (pre-v7 behaviour). "
                         "Field 2026-09-09: the sign reversal that lost the oval corner happened on 6-9 rows.")
    ap.add_argument("--lowrow-hold-ms", type=int, default=1500,
                    help="how long to hold the last confident command while rows are below --min-rows, before releasing")
    # v13 (2026-09-11, offline replay of the 2026-09-10 oval matrix).
    # Three gates on the SAME hold path as --min-rows; all default OFF = v12 byte for byte.
    ap.add_argument("--min-vote", type=int, default=0,
                    help="v13: refuse to steer on a line whose polyfit had fewer than N VOTING rows (--fit-max-y makes vote < kept). "
                         "G2 drive 8 +10.5 s: kept 11 but 6 voting rows fitted a straight line, goal_x -0.02 m with the tape at -0.50, "
                         "and the command halved at full lock mid-bend. Replayed on G2/G1/G3/F1 (2381 corner frames): 9 cuts the "
                         "mid-corner command error 13 %% and the mid-turn authority drops 39 -> 19 with the straight unchanged. 0 = off.")
    ap.add_argument("--lowrow-lock", type=float, default=0.0,
                    help="v13: while held for low rows/votes AND the last confident command was a turn (|kappa_ema| > this x full "
                         "lock), command FULL LOCK in that direction instead of the last command (operator's rule 'hold full lock in the "
                         "turn'). Replay: 0.5 -> corner error 0.645 -> 0.609 1/m on top of --min-vote 9. 0 = off (hold last command).")
    ap.add_argument("--manual-knob", type=int, default=0,
                    help="v13e: with --override, the steering-source dial at or BELOW this PWM is a MANUAL position -- the steering head's share "
                         "is forced to zero and the wheel owns the servo outright, so there is nothing to fight. Above it the "
                         "override is unchanged (the steering head steers until the wheel moves, hands back --override-hold-ms after release). "
                         "Operator 2026-09-12: knob fully counter-clockwise = full control, fully clockwise = today's behaviour. "
                         "0 = off, byte for byte the v13d override.")
    # v16 (2026-10-03 01:2x, operator: "vehicles are not perfect mechanical devices; the controller should handle it"): an INTEGRAL term on the
    # lateral offset of the goal point, the standard addition to pure pursuit for a steady-state offset (Urmson 2006; ETRI 2015; FINDINGS 114).
    # Pure pursuit has no memory: a constant push (steering play after a left lock, camber, a tilted mount) settles the truck BESIDE the line
    # (tonight: +4-5 cm on CCW straights with the path on, 0 CW, -2 cm camera-only). steer_i (1/m) grows by --steer-i * goal_x per second while
    # the steering head steers a straight with the tape well in view, is capped at --steer-i-max * KFL, frozen in bends, on thin rows, on held/lowrow
    # frames and while the wheel is touched, and is added to the camera's curvature before the clamp. 0 = off, byte-identical.
    # v16.1 (2026-10-03 01:5x): a STATIC steering offset in curvature (F1TENTH's steering_angle_to_servo_offset). MEASURED tonight from the pose:
    # the truck turned 0.07-0.15 1/m further LEFT than commanded in every drive, both directions, straights and bends (FINDINGS 114). +0.10 cancels it.
    ap.add_argument("--steer-offset", type=float, default=0.0, help="v16.1: constant added to the commanded curvature, 1/m (+ = right). 0 = off.")
    ap.add_argument("--steer-i", type=float, default=0.0, help="v16: integral gain, 1/m per metre-second of goal offset. 0 = off.")
    ap.add_argument("--steer-i-max", type=float, default=0.10, help="v16: cap on the integral, as a fraction of full lock (default 0.10)")
    ap.add_argument("--steer-i-straight", type=float, default=0.25, help="v16: the integral only learns when |gain*kappa_raw| is below this (1/m): straights")
    ap.add_argument("--steer-i-rows", type=int, default=12, help="v16: and when at least this many tape rows are kept")
    ap.add_argument("--goal-jump", type=float, default=0.0,
                    help="v13: on a frame with fewer than 13 kept rows, refuse a goal whose x moved more than this (m) from the last "
                         "goal steered on within 1.5 s; hold instead. Replay: 0.25 -> a further 0.01-0.02 1/m; the smallest of the three. 0 = off.")
    # v13b (2026-09-11 03:20, NOT DRIVEN, REPLAY VERDICT: NOT READY): GAIN SCHEDULE on the fitted line's own curvature. Default OFF
    # = v13 byte for byte. Replayed on last night's G2/G1/F1 logs (gain-schedule replay v1/v2 logs + the EMA pass):
    # |fit_kappa| does not separate straight from bend cleanly (p50 0.1-0.3 vs 0.4-0.7), so the switch chatters -- 13-64 flips per
    # drive on one threshold, 9-36 with hysteresis, median 5-6 with a 0.3 EMA. Do not drive it until the signal is fixed (a
    # progress-based or goal-based schedule, or a much slower filter). Left in as a flag so the replay can be repeated.
    # The night's finding (the night's synthesis notes, items 3, 16): gain 1.5 helps the exit end and kept the straight calm at 0.8 m/s,
    # but the straight needs less stiffness than the bend. Schedule: --gain-low on the straight (|fit_kappa| < --gain-kappa),
    # the full --gain once the line bends, and HOLD the full gain after the bend until the goal is nearly centred
    # (|goal_x| < --gain-hold-x) so the exit correction is not softened half way. `gain` is already logged per frame.
    ap.add_argument("--gain-low", type=float, default=0.0,
                    help="v13b: gain on the straight; 0 = off (one gain everywhere, v13 behaviour)")
    ap.add_argument("--gain-kappa", type=float, default=0.5,
                    help="v13b: |fit_kappa| (1/m, the capped line's ground curvature) at or above which the full --gain applies")
    ap.add_argument("--gain-kappa-low", type=float, default=0.0,
                    help="v13b: hysteresis -- once at the full gain, drop to --gain-low only when |fit_kappa| < this AND the goal is "
                         "centred (0 = half of --gain-kappa). Replay 03:20 on G2/G1/F1: a single threshold chattered 13-64 times per drive.")
    ap.add_argument("--gain-hold-x", type=float, default=0.15,
                    help="v13b: after the bend, stay at the full --gain until |goal_x| < this (m)")
    # v13c (2026-09-11 10:35, gain-schedule replay v3): the v13b schedule switched
    # 16 times per drive (median, 36 max) because |fit_kappa| flickers across one threshold frame to frame. A LATCH with time fixes it:
    # HIGH only after the line has been bent for --gain-on-s, LOW only after the goal has been centred AND the line straight for
    # --gain-off-s. On G2/G1/F1 (24 drives): on 0.2 s / off 1.0 s at --gain-kappa 0.4 / --gain-kappa-low 0.2 = exactly one switch per drive
    # on 24/24, high at 1.4 s (the turn-in preview), low on 93 % of the launch straight. Both 0 = v13b byte for byte.
    ap.add_argument("--gain-on-s", type=float, default=0.0,
                    help="v13c: |fit_kappa| must be >= --gain-kappa for this long before the full --gain latches on (0 = at once, v13b)")
    ap.add_argument("--gain-off-s", type=float, default=0.0,
                    help="v13c: the goal must be centred and |fit_kappa| < --gain-kappa-low for this long before the gain drops to --gain-low (0 = at once, v13b)")
    ap.add_argument("--kff", type=float, default=0.0,
                    help="v8 (2026-09-09): CURVATURE FEED-FORWARD, off by default. Adds "
                         "kff * kappa_path to the pure-pursuit command, where kappa_path is the "
                         "ground-frame curvature of the trail near the goal point (see "
                         "path_curvature). Pure pursuit steers only on EXISTING lateral error, so "
                         "it enters a bend late and runs wide -- structural, not a gain problem "
                         "(FINDINGS 36, ANALYSIS-2026-09-08 9: Snider CMU-RI-TR-09-08 4.3). "
                         "0.0 = the pre-v8 command path, exactly. UNTESTED ON THE VEHICLE.")
    ap.add_argument("--kff-max", type=float, default=0.5,
                    help="hard clamp on the feed-forward's OWN magnitude, 1/m, applied before it "
                         "is added to the pursuit term (0.5 = 35 %% of full lock). The pursuit "
                         "term keeps the rest of --kappa-max, so a bad curvature estimate can "
                         "never take the wheel on its own.")
    ap.add_argument("--kff-min-rows", type=int, default=12,
                    help="the feed-forward needs MORE rows than the pursuit term: no curvature is "
                         "fed forward on a line fitted through fewer than N of the 17 band rows, "
                         "whatever --min-rows says. A curvature estimated from a few noisy rows is "
                         "exactly the garbage --min-rows exists to reject, and the second "
                         "derivative is far more sensitive to it than the goal point is.")
    ap.add_argument("--look-trim", action="store_true",
                    help="FIELD 2026-09-07 (operator): read the lookahead from the radio's look-ahead trim channel every frame instead of --lookahead. "
                         "Radio: a mix puts the look-ahead trim on that channel, weight 400. 1000 us = 1.0 m, 1500 = 2.0 m, 2000 = 3.0 m (band top on "
                         "wedge-up is 3.07 m). If that channel is absent --lookahead applies. Logged per frame as 'look'.")
    ap.add_argument("--gain-trim", action="store_true",
                    help="FIELD 2026-09-07 (operator): read the gain from the radio's gain trim channel every frame instead of --gain. "
                         "Radio: a mix puts the gain trim on that channel, weight 400. 1000 us = 0.5, 1500 = 1.5, 2000 = 2.5. "
                         "If that channel is absent (0/None) --gain applies. The gain used is logged per frame.")
    ap.add_argument("--cruise", action="store_true",
                    help="v6: hold a fixed throttle on the RC throttle channel while the steering head steers, so a tuning pass is one "
                         "cell (lookahead, gain) and not also a hand-throttle (ANALYSIS-2026-09-08 6B/9). OFF by "
                         "default. Engages ONLY when the real trigger (the radio's trigger-copy mixer channel) is in its neutral "
                         "band, the switch is out of HOLD, the FC reports MANUAL and ARMED, and RC telemetry is "
                         "fresh. Any trigger movement -- forward or brake -- hands throttle back to the receiver "
                         "on the independent sender tick. Startup and every cancellation require HOLD + bottom dial, "
                         "pose standstill, then raise the dial in HOLD and leave HOLD with a fresh intent. "
                         "Requires RCMAP_THROTTLE = 2; the tool refuses to start otherwise.")
    ap.add_argument("--cruise-trim", action="store_true",
                    help="read the cruise level live from the radio's speed dial instead of --cruise-pwm. Radio: a mix puts the speed-dial "
                         "trim on it, weight 400 (same pattern as the look-ahead and gain trims). 1000 us = STOPPED (throttle "
                         "neutral), 2000 us = neutral + 250 us; ~16 us of throttle per trim click. Winding the speed dial all "
                         "the way down always stops the truck. If the speed dial is absent cruise does NOT engage.")
    ap.add_argument("--cruise-band", type=int, nargs=2, metavar=("LO", "HI"),
                    help="v15.1: with --cruise-trim, map the speed dial 1100..2000 us onto LO..HI us of throttle (e.g. 1625 1725); at or below 1100 the receiver "
                         "owns throttle. Absent = the v6 mapping (neutral .. neutral + 250 us).")
    ap.add_argument("--cruise-mps", type=float, nargs=2, metavar=("LO", "HI"),
                    help="v15.2 (2026-09-13): make the speed dial command a SPEED, not a PWM -- the dial's 1100..2000 us maps onto "
                         "LO..HI m/s and a PI trim on the throttle holds it from the phone's pose (at or below 1100 us = STOPPED, "
                         "the trigger owns throttle). Needs --cruise-trim. Fixes the pack's sag changing the speed inside a session: "
                         "on 2026-09-13 one trim click was the difference between 0.3 and 0.8 m/s. Absent = the PWM dial, unchanged.")
    ap.add_argument("--cruise-kp", type=float, default=25.0,
                    help="v15.2 speed loop: microseconds of throttle per m/s of error (default 25; the measured plant is ~30 us per m/s)")
    ap.add_argument("--cruise-ki", type=float, default=50.0,
                    help="v15.2 speed loop: microseconds per m/s per second of integral (default 50 = ~0.6 s to correct a 1 m/s error)")
    ap.add_argument("--cruise-i-max", type=float, default=60.0,
                    help="v15.2 speed loop: hard clamp on the integral term in microseconds (default 60)")
    ap.add_argument("--cruise-launch-reset", action="store_true",
                    help="EXPERIMENT, default off: once per cruise engagement, discard positive startup integral "
                         "when fresh pose speed first reaches min(0.10 m/s, half the target). Ordinary PI control "
                         "then continues, with the same throttle ceiling. Needs --cruise-mps; physical benefit unproven.")
    ap.add_argument("--cruise-pwm", type=int, default=1650,
                    help="throttle PWM held when --cruise-trim is not used. Default 1650: the BOTTOM of the band "
                         "measured in the field (1650-1700 us gave 0.4-1.4 m/s, ANALYSIS-2026-09-08 1), i.e. the "
                         "slowest setting that reliably MOVES. Slower than this is the crawl regime where the "
                         "shared BEC sags and the steering servo drops out (FINDINGS 34).")
    ap.add_argument("--cruise-max", type=int, default=1750,
                    help="hard ceiling on the cruise throttle, never exceeded whatever the trim says (default 1750 "
                         "= the top of the measured 0.4-1.4 m/s band). The tool also refuses any value above "
                         f"{CRUISE_ABS_MAX}.")
    ap.add_argument("--fc", help="flight controller serial port; omit = compute only")
    ap.add_argument("--sysid", type=int, default=255)
    ap.add_argument("--steer-sign", type=int, default=-1, choices=(1, -1),
                    help="MEASURED 2026-09-06 04:50 on the truck: steering-channel PWM 1744 (above trim) turned the wheels "
                         "LEFT, 1236 turned them RIGHT. So positive kappa (goal to the right) needs a LOWER "
                         "PWM: sign -1. Re-verify after any radio recalibration or SERVO1_REVERSED change.")
    ap.add_argument("--dry-run", action="store_true", help="connect to the FC and check it, but never send")
    ap.add_argument("--port", type=int, default=7790, help="iproxy port for Moblin")
    ap.add_argument("--video", help="run on a corpus rgb.mp4 instead of the phone")
    ap.add_argument("--start", type=int, default=0); ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--video-fps", type=float, default=0.0, help="pace a --video replay to real time (corpus: 5)")
    ap.add_argument("--secs", type=float, default=0, help="0 = until Ctrl-C / end of video")
    ap.add_argument("--log", help="directory for frames.jsonl, probs.npz, jpgs")
    ap.add_argument("--jpg-every", type=int, default=15, help="960x720 viewing jpg cadence")
    ap.add_argument("--no-feed", action="store_true", help="do not save the 476x350 feed frame per frame (default: save when --log)")
    ap.add_argument("--note", default="", help="what changed for this run -- written into frames.jsonl's start event")
    ap.add_argument("--wheel-hz", type=float, default=50.0,
                    help="rate of the sole steering/throttle output owner, independent of camera rate in every switch position")
    ap.add_argument("--override", action="store_true",
                    help="v13d: in the blend band the steering head steers until the wheel leaves its deadband, then the wheel steers "
                         "entirely; the steering head gets the servo back --override-hold-ms after release. The steering-source dial is ignored.")
    ap.add_argument("--override-dz", type=float, default=0.08, help="v13d: wheel deadband (normalised steer) that counts as a touch")
    ap.add_argument("--override-hold-ms", type=int, default=500, help="v13d: how long the wheel keeps the servo after it returns to centre")
    ap.add_argument("--path", help="v15: taught path npz (the path recorder, not shipped; --ref-point axle) in the phone's CURRENT ARKit session frame; "
                                   "turns the taught-path feed-forward ON (STANDING section 1, FINDINGS 65). Absent = byte-identical to v14.")
    ap.add_argument("--ff-w", type=float, default=0.5, help="v15: camera weight on a FRESH frame, u = (1-w) kappa_ref + w kappa_cam (1.0 = today's truck; 0 = path only)")
    ap.add_argument("--path-sign", type=int, default=1, choices=(1, -1), help="v15: multiplies the path's curvature into the truck's convention (+ = RIGHT); verified +1 for handed -1 in replay (FINDINGS 65), checked again on the fake-pose bench")
    ap.add_argument("--pose-max-age-ms", type=int, default=200, help="v15: a pose older than this (phone clock) carries no path term (drive p99 was 151 ms)")
    ap.add_argument("--path-max-blind-m", type=float, default=0.5, help="v15: max travel steered by the reference alone without an accepted camera frame; beyond it the v13 hold/release stands")
    ap.add_argument("--path-max-dist-m", type=float, default=0.5, help="v15: farther than this from the path = no path term this frame")
    ap.add_argument("--path-jump-m", type=float, default=0.4, help="v15: a pose displacement this far beyond 1.5 x v x dt (+0.1 m), or a heading jump > 90 deg inside 0.5 s, or a recording-id change, or pose_seq going backwards = RESET: the alignment is invalid for the rest of the run")
    ap.add_argument("--path-max-pose-gap-s", type=float, default=1.0, help="v15: a gap longer than this between usable poses = RESET (a displacement rule cannot judge a pose that arrives after an outage)")
    ap.add_argument("--ff-act-s", type=float, default=0.0, help="v15: actuator delay added to the pose's own age when predicting the axle pose forward (unmeasured; 0 until the step test)")
    ap.add_argument("--return-to-path", action="store_true",
                    help="v15.3 (2026-09-13): when the CAMERA has lost the tape and the taught reference has run out (past "
                         "--path-max-blind-m, or further than --path-max-dist-m from the line), steer BACK to the taught path by pure "
                         "pursuit on the reference instead of holding the last command and straightening. This is the design's 'no trail' "
                         "state: the trail is reacquired, not guessed. Hands back the instant the camera sees tape again. Off = v15.2 byte for byte.")
    ap.add_argument("--route-end-m", type=float, default=0.3,
                    help="v15.6 (2026-09-19, out-and-back): on an OPEN route (record_route.py, closed=False) the reference stops this many "
                         "metres before its last point: no path term, no return-to-path search, and a `route_end` stop is published so "
                         "cruise disengages and the trigger owns throttle -- the human turns the truck round. Clears once the truck is "
                         "heading back along the route. Ignored on a closed path (byte-identical).")
    cell_completion = ap.add_mutually_exclusive_group()
    cell_completion.add_argument("--stop-after-laps", type=int, default=0,
                    help="v15.4 (2026-09-13): after N whole laps of the TAUGHT PATH, disengage cruise so the human's trigger owns "
                         "throttle again and the truck coasts to a stop -- so a parameter cell can be run hands-off and every cell "
                         "gets the same number of laps. Laps are counted by unwrapped progress along --path, so it needs --path. "
                         "It can ONLY take throttle away, never add it; the trigger, HOLD and the kill are untouched. 0 = off.")
    cell_completion.add_argument("--cell-seconds", type=float, default=None,
                    help="finish each batch cell after this many seconds from its driving start; "
                         "then require the existing measured rest/neutral transition. Needs --batch-cells, "
                         "must be below --cell-max-s, and does not require a taught path.")
    ap.add_argument("--return-lookahead", type=float, default=0.8,
                    help="v15.3: look-ahead along the taught path for the return goal, metres (default 0.8)")
    ap.add_argument("--return-gain", type=float, default=1.0,
                    help="v15.3: gain on the return curvature (default 1.0 = the pure-pursuit geometry unmodified)")
    ap.add_argument("--return-max-dist-m", type=float, default=3.0,
                    help="give up returning beyond this distance from the taught path (default 3.0 m), latch autonomous throttle off")
    ap.add_argument("--return-max-s", type=float, default=20.0,
                    help="give up after this many seconds of continuous returning (default 20), latch autonomous throttle off")
    ap.add_argument("--log-authority", action="store_true", help="v15: write rec['authority'] (model|wheel|handback|hold_sw|human|manual|model_sa_out|release) and rec['throttle_src'] per frame -- the log had no single field for who has the plant (round 4)")
    ap.add_argument("--cruise-relatch-fwd", action="store_true",
                    help="retain the brake-neutral-reverse latch. A forward pull clears this brake latch; the independent "
                         "startup/cancellation latch additionally requires the HOLD-and-dial start gesture")
    ap.add_argument("--reverse-scale", type=float, default=1.0,
                    help="v13d: fraction of full reverse commanded at a full backward trigger pull (1.0 = receiver pass-through, as before)")
    ap.add_argument("--stall-recovery", action="store_true",
                    help="one bounded stop/brake/neutral/reverse/stop attempt on no motion; requires --cruise, --fc, "
                         "and either --path or --cell-seconds with explicit --mount-yaw-deg; "
                         "fresh pose and servo feedback, and RC_OVERRIDE_TIME <= 0.5 s. Defaults await physical validation.")
    ap.add_argument("--batch-cells", type=int, default=0,
                    help="repeat 2..12 lap-count or timed cells after one explicit start; off by default")
    ap.add_argument("--cell-max-s", type=float, default=90., help="hard cell deadline; exceeding it terminates the batch")
    ap.add_argument("--batch-max-s", type=float, default=900., help="hard whole-batch deadline including stops")
    ap.add_argument("--t3-throttle", action="store_true",
                    help="2026-09-27 (operator): the speed dial IS the throttle. Mode switch middle + kill released + trigger neutral -> throttle channel = "
                         "the speed dial mapped 1100..2000 onto neutral..--cruise-max; speed dial at the bottom = neutral; mode switch out = release. "
                         "No pose, no speed loop, no start gesture. Use WITHOUT --cruise. Wheel/trigger still override.")
    ap.add_argument("--relay-udp", type=int, default=0,
                    help="2026-09-30 stage 4: read an external planner's /control_cmd as JSON datagrams on 127.0.0.1:PORT from "
                         "a control_cmd relay, not shipped (0 = off). Every frame logs rec['relay'] = {seq, k, age_ms, ls, v, used}. "
                         "k is already in the TRUCK's sign (relay value x --relay-sign).")
    ap.add_argument("--relay-mode", choices=("log", "steer"), default="log",
                    help="log (default): record its curvature next to the camera's, steer on the camera as before. "
                         "steer: when its command is fresh (<= --relay-max-age-ms) and listen_to_steering, it REPLACES "
                         "kappa_raw before the gain/cap/EMA chain and the row/vote/jump gates stand down for that frame; "
                         "stale or not listening -> the camera path, unchanged. Its speed is NEVER used: the speed dial and trigger own throttle.")
    ap.add_argument("--relay-max-age-ms", type=float, default=400.0, help="its command counts as fresh for this long after receipt")
    ap.add_argument("--relay-sign", type=float, default=-1.0,
                    help="truck kappa = this x the planner's kappa. FINDINGS 105: its positive = LEFT, STANDING's positive = RIGHT, so -1.")
    ap.add_argument("--feed-keep", type=int, default=50,
                    help="2026-10-01: keep only the newest N feed JPEGs per run (the feed page needs one). 0 = keep every frame "
                         "(the old behaviour: 88 GB of feed pictures in 370 runs on the Orin by 09-30). Full-size stills "
                         "(--jpg-every) are untouched by this.")
    ap.add_argument("--owner", choices=("legacy", "simple"), default="legacy",
                    help="2026-10-01 (operator's simple-truck ruling): simple = owner_simple.SimpleOwner. the mode switch is the mode "
                         "(far end HUMAN, middle ASSIST, near end STOP or --sa-near auto), the speed dial the speed, the kill dial the kill, wheel/trigger "
                         "override while touched. No gesture, no --start-armed, no epochs, no cruise loop, no recovery, no steering-source dial. "
                         "legacy = output_owner.OutputOwner, byte-identical to the 09-28 truck.")
    ap.add_argument("--sa-near", choices=("stop", "auto"), default="stop",
                    help="simple owner only: what the mode switch's near end means. stop (default, matches the FC's HOLD band today) or auto "
                         "(same outputs as ASSIST, reported as AUTO; only after the FC's MODE1..6 are all MANUAL).")
    ap.add_argument("--frames-udp", type=int, default=0,
                    help="2026-10-01: publish every decoded frame, block-mean downscaled to 160x120 BGR, as one UDP datagram "
                         "(>QHH pts_us,w,h + 57600 bytes) to 127.0.0.1:PORT for a frame bridge (not shipped) -> /image_raw. "
                         "One phone client: live_trail owns the stream, the external planner's node gets these frames. 0 = off.")
    ap.add_argument("--start-armed", action="store_true",
                    help="v15.5: grant ONE start without the radio start gesture. The launch itself is the "
                         "authorization, so the operator does not have to take the speed dial to the bottom and cycle the mode switch "
                         "through HOLD. Every other condition still holds: fresh radio, armed, trigger neutral, "
                         "truck at rest, kill released, and the mode switch already out of HOLD. Spent the instant it grants a "
                         "start, so no later veto can silently restart the vehicle. "
                         "DANGER: a process launched with this while the mode switch is out of HOLD and the speed dial is up WILL DRIVE AS "
                         "SOON AS IT IS READY. That is the 2026-09-13 treadmill scenario, made deliberate and "
                         "logged rather than accidental. Pass it only when you mean to start.")
    ap.add_argument("--cell-plan", help="v15.5: JSON list of per-cell knob overrides, one object per --batch-cells "
                                        "cell, applied at each CONFIRMED cell boundary inside this process (no "
                                        "restart, so no restart hazard). Only knobs the loop re-reads every frame "
                                        "may appear; startup-baked ones (--fit-max-y above all, whose vote mask is "
                                        "precomputed) are refused BY NAME. The whole plan is range-checked at "
                                        "startup, before the vehicle can move. A plan may retune a behaviour but "
                                        "never switch one on. See cell_plan.py.")
    ap.add_argument("--reverse-trial", action="store_true",
                    help="v15.6 Phase 2: SUPERVISED REVERSE CHARACTERISATION. No forward start is "
                         "granted by any gesture in this mode -- not the radio gesture, not "
                         "--start-armed -- and the only thing the owner will ever command is ONE "
                         "bounded centred reverse waveform per explicit request, admitted from "
                         "confirmed rest with every hard stop clear. Needs --fc and a mount yaw; "
                         "needs NO taught path, because a local pulse is measured against the pose "
                         "at its own start. Drive it with reverse_trial.py.")
    ap.add_argument("--trial-max-fraction", type=float, default=0.30,
                    help="v15.6: the reverse amplitude ceiling for THIS trial run, chosen before "
                         "any trial and recorded in every trial record. A trial may ask for any "
                         "fraction up to it and is REFUSED above it -- a failed test cannot raise "
                         "its own acceptance limit. Absolutely bounded by recovery.ABS_FRACTION_CEILING.")
    ap.add_argument("--trial-dir", default=str(Path.home() / "live" / "trials"),
                    help="v15.6: where trial requests are read and results and the append-only "
                         "ledger are written.")
    ap.add_argument("--reverse-profile",
                    help="v15.6 Phase 3: the ACCEPTED reverse profile JSON, built from a trial "
                         "ledger by `reverse_trial.py accept`. It carries the "
                         "characterised waveform AND the envelope it was measured in -- surface, "
                         "contact, battery. --reverse-ready requires it; there is no way to turn "
                         "autonomous reverse on with a flag alone.")
    ap.add_argument("--resume-after-recovery", action="store_true",
                    help="v15.6 Phase 3: after a COMPLETED recovery, hand back to forward driving "
                         "automatically. DEFAULTS OFF: freeing the truck and continuing the "
                         "experiment are two different claims, and first movement is not "
                         "clearance. Off, one bounded attempt ends the run in a stopped state.")
    ap.add_argument("--reverse-ready", action="store_true",
                    help="v15.6: accept the reverse waveform and its limits for AUTONOMOUS stall recovery. "
                         "DEFAULTS FALSE and must stay false until Phase 2 has characterised the waveform "
                         "physically: `max_fraction .25`, `ramp_fraction_s .25` and above all "
                         "`max_speed_mps .35` are provisional, and the speed limit in particular was raised "
                         "because a SIMULATED retreat tripped the old bound. Without this flag a detected "
                         "stall stops the run with `recovery_unaccepted` instead of reversing. A comment is "
                         "not a gate.")
    ap.add_argument("--forward-block-max-s", type=float, default=4.0,
                    help="v15.6: how long autonomous permission is retained, at explicit neutral throttle, "
                         "while the camera reports no usable line. Before this existed the first blind frame "
                         "withdrew permission outright, which is why the stall recovery could never fire in "
                         "the case it exists for (run corner-1807: 600/600 frames vetoed no_steering). Set 0 "
                         "to restore the old withdraw-immediately behaviour. Line loss alone still never "
                         "reverses the truck: stall evidence may only START while forward is commanded.")
    ap.add_argument("--status-file", default=str(Path.home() / "live" / "status.json"),
                    help="v15.6: where the output owner's snapshot is published, atomically, off the sender "
                         "thread. Read it with truck_status.py. '' disables publication.")
    ap.add_argument("--startup-check", action="store_true",
                    help="v15.6: perform the ENTIRE startup -- args, plan, model, run spec, FC connect, owner, "
                         "one owner tick, one status publication -- with sending forced OFF, print the result and "
                         "exit. NOTHING is ever sent. This is the deployment gate: unit tests passing did not catch "
                         "a non-serialisable object that crashed startup on 2026-09-14. Exit 0 means the tool can "
                         "actually come up on this machine with these arguments.")
    ap.add_argument("--mount-yaw-deg", type=float, default=None,
                    help="v15.6: measured PHONE-TO-TRUCK heading correction, in degrees, as calibration metadata "
                         "rather than as a property of a taught path. Normally this rides in the path npz's "
                         "mount_yaw_motion_deg and is read from there; supply it explicitly for a PATH-FREE run "
                         "(a local reverse calibration trial needs the mount correction but not an oval). If a "
                         "path is loaded, this must agree with it or the run is refused. There is no default: "
                         "assuming camera heading equals truck heading is a measurement claim nobody made.")
    ap.add_argument("--cpu", action="store_true", help="no CUDA (Mac testing)")
    ap.add_argument("--continuous-sweep", help="JSON timed four-knob sweep; simple owner, continuous speed-dial driving")
    # SUPPRESS: absent, the start row's args stay exactly as before this flag existed
    ap.add_argument("--sweep-inbox", default=argparse.SUPPRESS, metavar="DIR",
                    help="2026-10-10: live sweep commands, one *.json per command (append | next | now, "
                         "continuous_sweep.SweepInbox; written by a request tool, not shipped). Default <log>/sweep-inbox "
                         "whenever --continuous-sweep is on; refused without it.")
    ap.add_argument("--path-recovery", metavar="CONFIG.json",
                    help="Path Recovery and turnarounds (path_recovery.py). The JSON holds PathRecoveryConfig "
                         "fields ({} = proposed defaults). Needs --owner simple, --fc and --log; requests arrive in "
                         "--recovery-inbox. Off = byte-identical control.")
    ap.add_argument("--recovery-inbox", help="request directory (default <log>/recovery-inbox)")
    ap.add_argument("--recovery-direction", default="unknown",
                    help="travel-direction reference for automatic trials: oval_path:cw|ccw, trail:outbound|return, unknown")
    ap.add_argument("--experiment-id", help="recovery events: experiment spanning continuations")
    ap.add_argument("--continuation-of", help="recovery events: run_id this run continues")
    ap.add_argument("--recovery-history", nargs="*", default=None,
                    help="earlier frames.jsonl files; trial attempts continue their numbering")
    ap.add_argument("--log-heading", action="store_true",
                    help="request FC ATTITUDE and log a gyro heading per frame (implied by --path-recovery)")
    ap.add_argument("--log-motion", action="store_true",
                    help="2026-10-09 SHADOW LOGGING ONLY: rec['motion'] = ground-crop image MAD and probs change vs "
                         "earlier frames, plus whether the offline stall rule WOULD fire (motion_signal.py). "
                         "Nothing reads it back; off = byte-identical frame rows.")
    a = ap.parse_args()
    blue_cfg = None
    if a.blue_gate is not None:
        import blue_gate
        try:
            blue_cfg = blue_gate.BlueGateConfig.from_spec(a.blue_gate)
        except ValueError as exc:
            ap.error(str(exc))
        a.blue_gate_digest = blue_cfg.digest()          # captured with the args in the start row
    sweep = None
    if a.continuous_sweep:
        from continuous_sweep import Sweep, eligible as sweep_eligible
        import durable_run  # include recorder dependency in the startup capture
        try:
            sweep = Sweep.load(a.continuous_sweep, a)
        except (ValueError, OSError) as exc:
            ap.error(str(exc))
    elif getattr(a, "sweep_inbox", None) is not None:
        ap.error("--sweep-inbox only works with --continuous-sweep")
    recov_cfg = recov_dir = None
    if not a.path_recovery:
        # Path Recovery's own flags do nothing without it: refuse rather than drop requests into an unread inbox.
        stray = [flag for flag, given in (("--recovery-inbox", a.recovery_inbox is not None),
                                          ("--recovery-direction", a.recovery_direction != "unknown"),
                                          ("--experiment-id", a.experiment_id is not None),
                                          ("--continuation-of", a.continuation_of is not None),
                                          ("--recovery-history", a.recovery_history is not None)) if given]
        if stray:
            ap.error(", ".join(stray) + " only work with --path-recovery")
    if a.log_heading and not a.fc:
        ap.error("--log-heading requires --fc (the heading is the FC's ATTITUDE; without it every frame logs none)")
    if a.path_recovery:
        from path_recovery import PathRecoveryConfig, parse_direction
        if a.owner != "simple" or not a.fc or not a.log:
            ap.error("--path-recovery requires --owner simple, --fc and --log")
        if a.dry_run:
            ap.error("--path-recovery refuses --dry-run: trials would start and log outcomes that were never actuated")
        for k in ("path", "cell_plan", "batch_cells", "stall_recovery", "reverse_trial", "steer_i"):
            if getattr(a, k, None):
                ap.error("--path-recovery conflicts with --" + k.replace("_", "-") + " (both replace the steering command)")
        if getattr(a, "relay_mode", "log") == "steer":
            ap.error("--path-recovery conflicts with --relay-mode steer")
        try:
            recov_cfg = PathRecoveryConfig.load(a.path_recovery)
            recov_dir = parse_direction(a.recovery_direction)
        except (ValueError, OSError, TypeError) as exc:
            ap.error(str(exc))
    heading_src = None
    if a.path_recovery or a.log_heading:
        from path_recovery import GyroHeading
        heading_src = GyroHeading()
    a.recovery_config = RecoveryConfig() if a.stall_recovery else None
    if a.cell_seconds is not None and not a.batch_cells:
        sys.exit("REFUSING: --cell-seconds needs --batch-cells.")
    if a.batch_cells:
        from batch import Batch
        try:
            Batch(a.batch_cells, a.cell_max_s, a.batch_max_s, cell_seconds=a.cell_seconds)
        except ValueError as exc:
            sys.exit(f"REFUSING: {exc}")
        if not (a.stall_recovery and (a.stop_after_laps > 0 or a.cell_seconds is not None)
                and a.cruise_mps and a.log):
            sys.exit("REFUSING: batch needs --stall-recovery, --stop-after-laps or --cell-seconds, "
                     "--cruise-mps and --log.")
    # NOT on the args namespace: the run record JSON-serialises every setting, and a
    # CellPlan object is not serialisable. That crashed run ladder-1818 at startup.
    cell_plan_obj = None
    if a.cell_plan:
        if not a.batch_cells:
            sys.exit("REFUSING: --cell-plan needs --batch-cells (a plan changes knobs AT a cell boundary).")
        import cell_plan as CELLPLAN
        try:
            cell_plan_obj = CELLPLAN.load(a.cell_plan, a.batch_cells, a,
                                            CRUISE_FF_US, CRUISE_US_PER_MPS, CRUISE_MPS_ABS_MAX,
                                            CRUISE_THR_NEUTRAL + CRUISE_MIN_BAND,
                                            min(a.cruise_max, CRUISE_ABS_MAX))
        except (OSError, ValueError) as exc:
            sys.exit(f"REFUSING: --cell-plan {a.cell_plan}: {exc}")
        # Cell 0 is applied HERE, before every other startup check, so the checks below and the
        # printed banner describe the configuration the first cell will actually drive on.
        first = cell_plan_obj.apply(a, 0)
        print(f"cell plan: {len(cell_plan_obj)} cells from {a.cell_plan}")
        for i, line in enumerate(cell_plan_obj.describe()):
            print(f"  cell {i}: {line}")
        if first:
            print("  cell 0 applied at startup: "
                  + ", ".join(f"{k} {v['from']} -> {v['to']}" for k, v in sorted(first.items())))
    if a.reverse_trial:
        # A LOCAL calibration pulse is measured against the pose captured at its own start
        # (recovery.py holds that origin), so no taught oval is involved and none is required.
        # What IS required is the physical mount correction, which normally rides in the path
        # file: separating it is the whole point. Forward cruise is refused outright here so
        # this mode cannot drive the truck forwards even by accident.
        if not a.stall_recovery:
            sys.exit("REFUSING: --reverse-trial needs --stall-recovery (it uses that sequence).")
        if not a.fc:
            sys.exit("REFUSING: --reverse-trial needs --fc.")
        if a.cruise or a.batch_cells or a.start_armed or a.stop_after_laps:
            sys.exit("REFUSING: --reverse-trial is a standalone supervised mode. Do not combine it "
                     "with --cruise, --batch-cells, --start-armed or --stop-after-laps.")
        if a.mount_yaw_deg is None and not a.path:
            sys.exit("REFUSING: --reverse-trial needs --mount-yaw-deg (the MEASURED phone-to-truck "
                     "heading). It normally rides in a taught path; supply it explicitly for a "
                     "path-free trial. Assuming camera heading equals truck heading is a "
                     "measurement claim nobody has made.")
        if not (0 < a.trial_max_fraction <= RecoveryConfig().fraction_ceiling
                or 0 < a.trial_max_fraction <= __import__("recovery").ABS_FRACTION_CEILING):
            sys.exit(f"REFUSING: --trial-max-fraction must be in "
                     f"(0, {__import__('recovery').ABS_FRACTION_CEILING}] -- the validated range of "
                     "the RC-input mapping. Asking for more does not fail at admission, it fails on "
                     "the first tick and kills the sender.")
        a.recovery_config = RecoveryConfig(fraction_ceiling=a.trial_max_fraction,
                                           max_fraction=min(a.trial_max_fraction, RecoveryConfig().max_fraction))
    a.reverse_profile_id = None
    if a.reverse_ready:
        if not a.reverse_profile:
            sys.exit("REFUSING: --reverse-ready needs --reverse-profile. The waveform's limits are "
                     "provisional until a characterisation accepts them; a flag on its own is the "
                     "comment-instead-of-a-gate this was built to stop.")
        import reverse_trial as REVERSETRIAL
        _profile, _refusal = REVERSETRIAL.load_profile(a.reverse_profile)
        if _refusal is not None:
            sys.exit(f"REFUSING: {_refusal}")
        a.reverse_profile_id = _profile["profile_id"]
        try:
            a.recovery_config = RecoveryConfig(**_profile["config"])
        except (TypeError, ValueError) as exc:
            sys.exit(f"REFUSING: the accepted profile's configuration is not valid: {exc}")
        print(f"reverse profile {a.reverse_profile_id} ACCEPTED by {_profile['accepted_by']} "
              f"on {_profile['accepted_utc']}")
        print(f"  amplitude {_profile['amplitude']}; envelope {_profile['envelope']}")
        print(f"  stopping tail: {_profile['stopping_tail_status']}")
        print("  Outside that envelope this profile is not accepted. Recharacterise, do not extrapolate.")
    elif a.reverse_profile:
        sys.exit("REFUSING: --reverse-profile without --reverse-ready. Loading a profile is not "
                 "accepting it; pass both deliberately or neither.")
    if a.reverse_trial and a.reverse_ready:
        sys.exit("REFUSING: --reverse-trial characterises the waveform and --reverse-ready drives "
                 "on an already-characterised one. They are different sessions.")
    # Recovery consumes local pose and the mount correction, not a taught-path coordinate.
    # A timed batch can supply that measured correction explicitly; lap cells still need the
    # path. A supervised trial measures its local pulse against the pose at its own start.
    if not a.reverse_trial and a.stall_recovery:
        timed_mount = (a.cell_seconds is not None and a.mount_yaw_deg is not None
                       and finite(a.mount_yaw_deg) and -180 <= a.mount_yaw_deg <= 180)
        if not (a.cruise and a.fc and (a.path or timed_mount)):
            sys.exit("REFUSING: --stall-recovery needs --cruise, --fc and either --path "
                     "or --cell-seconds with a finite measured --mount-yaw-deg.")
    if not (finite(a.wheel_hz) and a.wheel_hz > 0 and a.hold_ms > 0 and a.pose_max_age_ms > 0):
        sys.exit("REFUSING: sender rate, intent lifetime and pose lifetime must be positive and finite.")

    pitch_live = a.pitch == "live"
    pitch = (PITCH_PRESETS.get(a.pitch_fallback) or float(a.pitch_fallback)) if pitch_live else (PITCH_PRESETS.get(a.pitch) or float(a.pitch))
    if pitch_live: print(f"pitch LIVE from the phone; fallback {pitch:.2f} deg. The --fit-max-y vote mask is built once from the fallback.")
    cap = a.cap if a.cap > 0 else None
    print(f"pitch {pitch:.2f} deg ({a.pitch}); expect the horizon ~{horizon_row_px(pitch):.0f} px "
          f"({100 * horizon_row_px(pitch) / SRC_H:.0f}%) from the top of a level frame")
    for name, r in (("bottom", 24), ("row 12", 12), ("band top", a.r0)):
        g = ground_point(r, 17.0, pitch, a.cam_h)
        print(f"  {name:9s} row {r:2d} -> {g[1]:.2f} m ahead" if g else f"  {name:9s} row {r:2d} -> above horizon")
    # v11: which band rows may VOTE in the line fit. Ground distance depends on the row and the
    # pitch only (flat ground), not on the column, so the mask is built once per run and the
    # boundary moves with --pitch as it must (sweep v3 I: 1.1 m = band rows 4-16 at 14.55 deg,
    # 3-16 at 16.3). None = every kept row votes (the validated rule).
    vote_rows = None
    if a.fit_max_y < 0: sys.exit("REFUSING: --fit-max-y must be >= 0 (0 = off).")
    if a.fit_max_y:
        vote_rows = np.zeros(GH - a.r0, bool)
        for q in range(GH - a.r0):
            g = ground_point(q + a.r0, 17.0, pitch, a.cam_h)
            vote_rows[q] = g is not None and g[1] <= a.fit_max_y
        if vote_rows.sum() < 4:
            sys.exit(f"REFUSING: --fit-max-y {a.fit_max_y:g} m leaves {int(vote_rows.sum())} of "
                     f"{GH - a.r0} band rows voting at pitch {pitch:.2f}; the fit needs >= 4.")
        first = int(np.argmax(vote_rows))
        print(f"fit vote (v11): rows <= {a.fit_max_y:g} m ahead vote in the line fit = band rows "
              f"{first}-{GH - a.r0 - 1} ({int(vote_rows.sum())} of {GH - a.r0}) at pitch {pitch:.2f}; "
              f"the line is still drawn on every kept row and --min-rows counts kept rows")
    top = ground_point(a.r0, 17.0, pitch, a.cam_h)
    if top and top[1] < a.lookahead:
        print(f"WARNING: lookahead {a.lookahead} m is beyond the band top ({top[1]:.2f} m); the goal "
              f"point will sit on the least-confident row. Use wedge-up or a shorter lookahead.")

    # v10 (2026-09-09): the plant constant is a flag (FINDINGS 38). Everything below reads KFL.
    if a.kappa_full_lock <= 0: sys.exit("REFUSING: --kappa-full-lock must be > 0.")
    KFL = a.kappa_full_lock
    if a.kappa_max is None: a.kappa_max = KFL
    if recov_cfg is not None:
        # The manoeuvre is clamped to --kappa-max below; a clamped away/main radius pair collapses the turnaround
        # geometry silently, so refuse a config the run cannot actually steer.
        k_over = {k: getattr(recov_cfg, k) for k in ("search_kappa", "turn_kappa", "turn_away_kappa")
                  if getattr(recov_cfg, k) > a.kappa_max}
        if k_over:
            sys.exit(f"REFUSING: path recovery curvature above --kappa-max {a.kappa_max:g}: "
                     + ", ".join(f"{k} {v:g}" for k, v in k_over.items()))
    cap_rec = 2.0 * KFL
    print(f"full lock: {KFL:.3f} 1/m (R {1 / KFL:.2f} m)"
          + (" -- the OLD hand-measured constant; FINDINGS 38 measured 1.66 at oval speed, 1.42 on the trail"
             if abs(KFL - KAPPA_FULL_LOCK) < 1e-6 else " (--kappa-full-lock)")
          + f"; --kappa-max {a.kappa_max:g}; --gain {a.gain:g}. Gain and full lock are ONE number in the "
          f"command (steer = gain*kappa/full_lock): rescale --gain by new/old whenever this changes.")

    # v9 (2026-09-09): the curvature cap, optionally in the GROUND frame. Off = the validated rule.
    if a.cap_kappa < 0: sys.exit("REFUSING: --cap-kappa must be >= 0.")
    if a.cap_kappa:
        print(f"line curvature cap: GROUND FRAME, |kappa| <= {a.cap_kappa:g} 1/m "
              f"({a.cap_kappa / KFL:.1f} x full lock) measured over "
              f"{CAP_WIN_NEAR:g}-{CAP_WIN_FAR:g} x lookahead ({CAP_WIN_NEAR * a.lookahead:.2f}-"
              f"{CAP_WIN_FAR * a.lookahead:.2f} m at --lookahead {a.lookahead:g}); "
              f"--cap {a.cap:g} patches/row^2 is IGNORED. Fallback chain cubic -> quadratic -> "
              f"line is unchanged.  NOT YET DRIVEN -- FINDINGS 39.")
        # The recommendation is 2 x full lock (DEFAULT_CAP_KAPPA); warn only ABOVE it, with a
        # little slack so the recommended value written as 2.86 does not warn about itself.
        if a.cap_kappa > 1.05 * cap_rec:
            print(f"  WARNING: {a.cap_kappa:g} 1/m is above the recommended "
                  f"{cap_rec:.2f} (= 2 x full lock {KFL:.2f}). For scale, the "
                  f"measured ceiling of the shipped patch cap is 0.6 1/m on the oval mount and 2.6 "
                  f"on the trail mounts; nothing above 2 x full lock has been measured.")
    else:
        print(f"line curvature cap: --cap {a.cap:g} patches/row^2, the validated rule "
              f"(results/line_rules_v1.md). On this mount that is a HARD CEILING on how tight a "
              f"bend the line can represent -- see FINDINGS 39 and --cap-kappa.")

    if a.stop_after_laps:
        if not a.path: sys.exit("REFUSING: --stop-after-laps needs --path (laps are counted along the taught path).")
        if a.stop_after_laps < 1: sys.exit("REFUSING: --stop-after-laps must be 1 or more.")
        print(f"stop after laps (v15.4): cruise disengages after {a.stop_after_laps} whole lap(s) of the taught path; "
              f"the trigger owns throttle again and the truck coasts. It never adds throttle. HOLD and the kill are unchanged.")
    if a.cell_seconds is not None:
        print(f"timed batch: {a.batch_cells} cells of {a.cell_seconds:g} s from each driving start; "
              f"then measured rest and neutral before advancing. Hard cell deadline {a.cell_max_s:g} s.")
    if a.return_to_path:
        if not a.path: sys.exit("REFUSING: --return-to-path needs --path (there is nothing to return to).")
        if not (a.path_max_dist_m < a.return_max_dist_m): sys.exit(
            f"REFUSING: --return-max-dist-m {a.return_max_dist_m} must be above --path-max-dist-m {a.path_max_dist_m}.")
        if a.return_lookahead <= 0: sys.exit("REFUSING: --return-lookahead must be positive.")
        print(f"return to path (v15.3): when the camera has lost the tape AND the reference has run out (blind > {a.path_max_blind_m} m, "
              f"or off the line by > {a.path_max_dist_m} m), steer back by pure pursuit at Ld {a.return_lookahead} m, gain {a.return_gain}; "
              f"give up beyond {a.return_max_dist_m} m or after {a.return_max_s} s. The camera and the human always win.")
    if a.cruise_launch_reset and not a.cruise_mps:
        sys.exit("REFUSING: --cruise-launch-reset needs --cruise-mps.")
    if a.cruise_mps:
        lo_v, hi_v = float(a.cruise_mps[0]), float(a.cruise_mps[1])
        ceil_pwm = min(a.cruise_max, CRUISE_ABS_MAX)
        ff_lo = CRUISE_FF_US + CRUISE_US_PER_MPS * lo_v; ff_hi = CRUISE_FF_US + CRUISE_US_PER_MPS * hi_v
        if not a.cruise: sys.exit("REFUSING: --cruise-mps needs --cruise.")
        if not a.cruise_trim: sys.exit("REFUSING: --cruise-mps needs --cruise-trim (it maps the speed dial).")
        if a.cruise_band: sys.exit("REFUSING: --cruise-mps and --cruise-band both map the dial. Use one.")
        if not (0 < lo_v < hi_v): sys.exit(f"REFUSING: --cruise-mps LO {lo_v} must be above 0 and below HI {hi_v}.")
        if hi_v > CRUISE_MPS_ABS_MAX: sys.exit(f"REFUSING: --cruise-mps HI {hi_v} is above the tool's ceiling {CRUISE_MPS_ABS_MAX} m/s.")
        if ff_hi > ceil_pwm: sys.exit(f"REFUSING: --cruise-mps HI {hi_v} m/s feeds forward to {ff_hi:.0f} us, above the ceiling {ceil_pwm}.")
        if ff_lo <= CRUISE_THR_NEUTRAL + CRUISE_MIN_BAND:
            sys.exit(f"REFUSING: --cruise-mps LO {lo_v} m/s feeds forward to {ff_lo:.0f} us, which reads as STOPPED.")
        print(f"cruise SPEED dial (v15.2): speed dial <= 1100 us = STOPPED (the trigger owns throttle); 1100..2000 us -> {lo_v}..{hi_v} m/s "
              f"held by a PI trim (Kp {a.cruise_kp} Ki {a.cruise_ki} Imax {a.cruise_i_max} us) on {ff_lo:.0f}..{ff_hi:.0f} us of feed-forward, "
              f"clamped to the ceiling {ceil_pwm}. No pose = HOLD the last throttle, never ramp.")
        if a.cruise_launch_reset:
            print("cruise launch reset EXPERIMENT: clear positive startup I once at fresh speed "
                  ">= min(0.10 m/s, half the target); physical benefit unproven.")
    if a.cruise and a.cruise_band:
        lo, hi = a.cruise_band
        if not a.cruise_trim: sys.exit("REFUSING: --cruise-band needs --cruise-trim (it maps the speed dial).")
        if not (lo < hi): sys.exit(f"REFUSING: --cruise-band LO {lo} must be below HI {hi}.")
        if hi > min(a.cruise_max, CRUISE_ABS_MAX): sys.exit(f"REFUSING: --cruise-band HI {hi} is above --cruise-max {a.cruise_max} / the ceiling {CRUISE_ABS_MAX}.")
        if lo <= CRUISE_THR_NEUTRAL + CRUISE_MIN_BAND: sys.exit(f"REFUSING: --cruise-band LO {lo} would read as STOPPED (<= neutral + {CRUISE_MIN_BAND}).")
        print(f"cruise dial (v15.1): speed dial <= 1100 us = STOPPED (the trigger owns throttle); 1100..2000 us -> {lo}..{hi} us of throttle "
              f"({(hi - lo) / 900.0 * 65:.1f} us per ~65 us trim click)")
    if a.cruise:
        if a.cruise_max > CRUISE_ABS_MAX:
            sys.exit(f"REFUSING: --cruise-max {a.cruise_max} is above the tool's absolute ceiling {CRUISE_ABS_MAX}.")
        if not a.cruise_trim and a.cruise_pwm > a.cruise_max:
            sys.exit(f"REFUSING: --cruise-pwm {a.cruise_pwm} is above --cruise-max {a.cruise_max}.")

    fc = None
    if a.fc:
        fc = FC(a.fc, a.sysid, a.steer_sign, cruise=a.cruise, recovery=a.stall_recovery, heading=heading_src,
                heading_receipt_path=(Path(a.log) / "heading-startup.json" if a.log else Path(f"heading-startup-{time.time_ns()}-{os.getpid()}.json")))
        if a.dry_run: print("DRY RUN: FC checked, nothing will be sent")
    else:
        print("no --fc: computing only")
    thr_neutral = fc.thr_neutral if fc is not None else CRUISE_THR_NEUTRAL
    thr_dz = fc.thr_dz if fc is not None else CRUISE_THR_DZ
    if (recov_cfg is not None and recov_cfg.throttle_cap_us is not None
            and recov_cfg.throttle_cap_us <= thr_neutral + max(thr_dz, CRUISE_MIN_BAND)):      # the owner's own band
        sys.exit(f"REFUSING: path recovery throttle_cap_us {recov_cfg.throttle_cap_us} is not above neutral "
                 f"{thr_neutral} + band {max(thr_dz, CRUISE_MIN_BAND)}; the truck would sit still while the trial counts as driving.")
    if recov_cfg is not None and not fc.heading_startup["recovery_ready"]:
        fc.m.close()
        sys.exit("REFUSING Path Recovery: heading startup not ready; see heading-startup.json")
    # v15.2 speed dial: the trim may only move the throttle between the "stopped" margin and the ceiling -- the same
    # limits a dialled PWM obeys, so the loop can neither reverse nor exceed --cruise-max.
    spdloop = CruiseSpeedLoop(a, thr_neutral + max(thr_dz, CRUISE_MIN_BAND) + 1,
                              min(a.cruise_max, CRUISE_ABS_MAX)) if getattr(a, "cruise_mps", None) else None
    ret_t0 = None; ret_gave_up = False; ret_stop_reason = None
    if a.cruise:
        band = max(thr_dz, CRUISE_MIN_BAND)
        ceiling = min(a.cruise_max, CRUISE_ABS_MAX)
        lvl = ("speed dial: %d us = STOPPED, %d us = %d us (~%.1f us of throttle per click)"
               % (1000, 2000, min(ceiling, int(thr_neutral + CRUISE_SPAN_US)), 65 / 1000.0 * CRUISE_SPAN_US)
               if a.cruise_trim else f"fixed {a.cruise_pwm} us")
        print(f"cruise: ENABLED on the throttle channel, {lvl}; ceiling {ceiling} us; stopped at or below "
              f"{thr_neutral + band} us; trigger neutral band {thr_neutral}+/-{band} us read from the trigger copy; "
              f"drops on trigger/HOLD/mode/disarm/RC older than {CRUISE_RC_MAX_AGE_MS} ms"
              + ("" if fc is not None else "  -- but there is no --fc, so nothing will ever engage"))
    else:
        print("cruise: off (throttle stays entirely on the trigger)")

    # v8 curvature feed-forward: refuse a configuration that could hand the wheel to the term
    if a.kff < 0: sys.exit("REFUSING: --kff must be >= 0 (a negative feed-forward steers OUT of bends).")
    if a.kff_max < 0: sys.exit("REFUSING: --kff-max must be >= 0.")
    if a.kff and a.kff_max > a.kappa_max:
        sys.exit(f"REFUSING: --kff-max {a.kff_max} exceeds --kappa-max {a.kappa_max}; the "
                 "feed-forward may never be allowed more authority than the total command.")
    if a.kff:
        print(f"curvature feed-forward: ON, kff {a.kff:g} x kappa_path, clamped to +/-{a.kff_max:g} "
              f"1/m ({100 * a.kff_max / KFL:.0f}% of full lock); mass-weighted quadratic "
              f"over ground points {KFF_NEAR_FRAC:g}-{KFF_FAR_FRAC:g} x lookahead "
              f"({KFF_NEAR_FRAC * a.lookahead:.2f}-{KFF_FAR_FRAC * a.lookahead:.2f} m at "
              f"--lookahead {a.lookahead:g}), >={KFF_MIN_PTS} points, fit residual <={KFF_MAX_RESID:g} m, "
              f">={max(a.min_rows, a.kff_min_rows)} of 17 rows; total still clamped to "
              f"+/-{a.kappa_max:g} and EMA'd.  NOT YET BENCH-TESTED OR DRIVEN.")
        # The window is in metres, the band rows are not evenly spaced in metres, and at a long
        # lookahead only the top two or three rows land in it -- fewer than KFF_MIN_PTS, so the
        # term would silently never fire. Count them here instead of letting the field find out.
        # Measured wedge-up: Ld 0.8 -> 7 rows in window (fires on 65 % of oval frames);
        # Ld 2.0 -> 3 rows, and a Ruby Hill straight replay logged "few_pts" on 48 of 48 frames.
        inwin = sum(1 for r in range(a.r0, GH)
                    for g in [ground_point(r, 17.0, pitch, a.cam_h)]
                    if g and KFF_NEAR_FRAC * a.lookahead <= g[1] <= KFF_FAR_FRAC * a.lookahead)
        print(f"  feed-forward geometry: {inwin} of {GH - a.r0} band rows land inside the window at "
              f"--lookahead {a.lookahead:g} on this mount"
              + ("" if inwin >= KFF_MIN_PTS else
                 f"  <-- FEWER THAN {KFF_MIN_PTS}: the feed-forward can NEVER fire at this "
                 f"lookahead/pitch (it will log kff_why=few_pts every frame). Shorten --lookahead "
                 f"or lower the mount."))
    else:
        print("curvature feed-forward: off (--kff 0; pure pursuit alone, pre-v8 behaviour exactly)")
    # v15: the taught path, loaded once; every number about it printed here so the first record's args and this line agree.
    tpath = None
    if a.path:
        tpath = TaughtPath(a.path)
        if not (0.0 <= a.ff_w <= 1.0): sys.exit("REFUSING: --ff-w must be in [0, 1].")
        if a.path_max_blind_m < 0 or a.path_max_dist_m <= 0: sys.exit("REFUSING: --path-max-blind-m >= 0 and --path-max-dist-m > 0.")
        print(f"taught path (v15): ON, {a.path}: {'CLOSED loop' if tpath.closed else 'OPEN route (v15.6 out-and-back, stops ' + format(a.route_end_m, 'g') + ' m before the last point)'}, {tpath.n} pts, L {tpath.length:.2f} m, |kappa| max {np.abs(tpath.kappa).max():.2f} 1/m, ref_point {tpath.ref_point}, "
              f"mount yaw {tpath.mount_yaw:+.1f} deg, heading {'REBUILT from xy' if tpath.heading_rebuilt else 'as stored'}; fresh u = {1-a.ff_w:.2f} ref + {a.ff_w:.2f} cam; "
              f"gated = ref alone for <= {a.path_max_blind_m:g} m; pose <= {a.pose_max_age_ms} ms, <= {a.path_max_dist_m:g} m off path; path sign {a.path_sign:+d}; act {a.ff_act_s:g} s.  "
              f"A reset/jump invalidates the alignment for the run.")
        if tpath.ref_point != "axle": print("  WARNING: the path was taught at the PHONE, not the rear axle (record_path.py --ref-point axle); it differs by direction of travel (FINDINGS 65).")
    # v15.6: the phone-to-truck heading correction is a MEASUREMENT OF THE MOUNT, not a property of
    # any particular taught oval. It normally rides in the path npz because that is where it was
    # first recorded. Separating it is what lets a local reverse calibration run with no path at
    # all. Two rules: a path and an explicit value must agree, and there is NO DEFAULT -- assuming
    # the camera's heading is the truck's heading is a measurement claim nobody has made.
    MOUNT_YAW_TOL_DEG = 0.5
    mount_yaw_deg = a.mount_yaw_deg
    mount_yaw_source = "--mount-yaw-deg"
    if tpath is not None:
        if mount_yaw_deg is None:
            mount_yaw_deg, mount_yaw_source = tpath.mount_yaw, f"taught path {Path(a.path).name}"
        elif abs(mount_yaw_deg - tpath.mount_yaw) > MOUNT_YAW_TOL_DEG:
            sys.exit(f"REFUSING: --mount-yaw-deg {mount_yaw_deg:+.2f} disagrees with the taught path's "
                     f"{tpath.mount_yaw:+.2f} by more than {MOUNT_YAW_TOL_DEG} deg. One of them is wrong; "
                     "do not average them.")
        else:
            mount_yaw_source = f"--mount-yaw-deg (agrees with {Path(a.path).name} to {MOUNT_YAW_TOL_DEG} deg)"
    if mount_yaw_deg is not None:
        if not (finite(mount_yaw_deg) and -180.0 <= mount_yaw_deg <= 180.0):
            sys.exit(f"REFUSING: mount yaw {mount_yaw_deg!r} is not a finite heading in [-180, 180] deg.")
        print(f"mount yaw (phone -> truck): {mount_yaw_deg:+.2f} deg, from {mount_yaw_source}")
    sending = fc is not None and not a.dry_run
    if a.startup_check:
        sending = False
        print("STARTUP CHECK: sending is forced OFF for this process; nothing will ever be sent.")

    import torch, cv2
    dev = "cpu" if a.cpu else "cuda"
    w = np.load(a.head); coef, bias = w[:-1].astype(np.float32), float(w[-1])
    NR = GH - a.r0
    print(f"head {a.head}  {w.size} params  rows {a.r0}..24 ({NR})")
    tw = None
    if a.trail_head:
        th = np.load(a.trail_head); tw = (torch.from_numpy(th[:-1].astype(np.float32)).to(dev), float(th[-1]))
    dw = None
    if a.danger_head:
        d = np.load(a.danger_head); dw = (torch.from_numpy(d[:-1].astype(np.float32)).to(dev), float(d[-1]))
        print(f"danger head {a.danger_head} (display/log only)")
    model = torch.hub.load(str(Path(torch.hub.get_dir()) / "facebookresearch_dinov2_main"), "dinov2_vits14", source="local", verbose=False).eval().to(dev)
    if dev == "cuda": model = model.half()
    dt = torch.float16 if dev == "cuda" else torch.float32
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1).to(dev).to(dt)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1).to(dev).to(dt)
    coef_t = torch.from_numpy(coef).to(dev)

    # Save recoverable bytes and the actual launch recipe before any output owner
    # can send. A staged bundle's own setup-tools/ (byte-identical to the deployed
    # ~/live/setup-tools when built from deployed-closure.json) wins, then the shared copy.
    import importlib
    setup_candidates = [Path(__file__).resolve().parent / "setup-tools", Path.home() / "live/setup-tools", Path(__file__).resolve().parents[2] / "setups"]
    for setup_path in setup_candidates:
        if (setup_path / "setup_capture.py").is_file():
            sys.path.insert(0, str(setup_path)); break
    setup_capture = importlib.import_module("setup_capture")
    setup_snapshot = setup_capture.capture_controller_start(
        a, __file__, torch.hub.get_dir(), fc, PHONE, pitch, pitch_live)
    setup_last_observation = 0.0
    print(f"setup captured: {setup_snapshot['id']}")

    log = None; probs_all = []; danger_all = []; writer = None; chunk_k = 0
    if a.log:
        Path(a.log).mkdir(parents=True, exist_ok=True)
        log = open(Path(a.log) / "frames.jsonl", "a")
        if not a.no_feed: (Path(a.log) / "feed").mkdir(exist_ok=True)
        json.dump({"event": "start", "t": time.time(), "args": {**vars(a), "recovery_config": asdict(a.recovery_config) if a.recovery_config else None}, "pitch": pitch, "note": a.note,
                   "heading_startup": fc.heading_startup if fc is not None else None,
                   "recovery_parameters": fc.recovery_parameters if fc is not None else None,
                   "setup_snapshot_id": setup_snapshot["id"],
                   "setup_parameters": fc.setup_parameters if fc is not None else None,
                   "version": 12, "output_owner_version": 1, "pitch_live": pitch_live, "kappa_full_lock": KFL, "fc_tele": fc is not None, "wheel_hz": a.wheel_hz,
                   "cap": a.cap, "cap_kappa": a.cap_kappa,
                   "cap_win": [CAP_WIN_NEAR, CAP_WIN_FAR],
                   "fit_max_y": a.fit_max_y, "fit_vote_rows": None if vote_rows is None else int(vote_rows.sum()),
                   "kff": a.kff, "kff_max": a.kff_max, "kff_min_rows": a.kff_min_rows,
                   "kff_near_frac": KFF_NEAR_FRAC, "kff_far_frac": KFF_FAR_FRAC,
                   "kff_min_pts": KFF_MIN_PTS, "kff_max_resid": KFF_MAX_RESID,
                   "cruise": bool(a.cruise), "cruise_trim": bool(a.cruise_trim),
                   "cruise_pwm_set": a.cruise_pwm, "cruise_max": min(a.cruise_max, CRUISE_ABS_MAX), "cruise_band": a.cruise_band,
                   "cruise_thr_neutral": thr_neutral, "cruise_thr_dz": thr_dz,
                   "head_sha_prefix": __import__("hashlib").sha256(w.astype(np.float64).tobytes()).hexdigest()[:12],
                   **({"path": a.path, "path_pts": tpath.n, "path_len_m": round(tpath.length, 3), "path_ref_point": tpath.ref_point, "path_mount_yaw_deg": round(tpath.mount_yaw, 2),
                       "path_closed": tpath.closed, "route_end_m": a.route_end_m,
                       "path_heading_rebuilt": tpath.heading_rebuilt, "ff_w": a.ff_w, "path_sign": a.path_sign, "pose_max_age_ms": a.pose_max_age_ms,
                       "path_max_blind_m": a.path_max_blind_m, "path_max_dist_m": a.path_max_dist_m, "path_jump_m": a.path_jump_m, "path_max_pose_gap_s": a.path_max_pose_gap_s, "ff_act_s": a.ff_act_s} if tpath is not None else {})}, log); log.write("\n")
        log.flush(); writer = LogWriter(a.log, log); writer.start()
        if sweep is not None:
            writer.put("line", json.dumps(dict(event="sweep_plan", t=time.time(), stages=sweep.stages)) + "\n")
    sweep_inbox = None
    if sweep is not None:
        from continuous_sweep import SweepInbox
        sweep_inbox = SweepInbox(getattr(a, "sweep_inbox", None) or str(Path(a.log) / "sweep-inbox"))
        print(f"SWEEP INBOX on: {sweep_inbox.dir} -- append | next | now; each decision is a sweep_request row")
    recov = None
    if recov_cfg is not None:
        from path_recovery import RecoveryHook
        recov = RecoveryHook(recov_cfg, lambda row: writer.put("line", json.dumps(row) + "\n"), Path(a.log).name,
                             inbox_dir=a.recovery_inbox or str(Path(a.log) / "recovery-inbox"),
                             direction_reference=recov_dir, experiment_id=a.experiment_id,
                             continuation_of=a.continuation_of, history_paths=a.recovery_history or [], heading=heading_src)
        print(f"PATH RECOVERY on: config {recov_cfg.digest()}, inbox {recov.inbox.dir}, direction {recov_dir}, auto {recov_cfg.auto}")
    if blue_cfg is not None:
        print(f"BLUE GATE on: config {blue_cfg.digest()} {blue_cfg} -- probs off-blue capped at 0.5 for line, cam_state and recovery; probs chunks stay raw")
    motion = motion_owner_view = None
    if a.log_motion:                                     # shadow logging only: no control path reads `motion`
        from motion_signal import MotionSignal
        from path_recovery import OwnerView as motion_owner_view
        motion = MotionSignal()
        print("MOTION SHADOW LOG on: rec['motion'] (img_mad, prob_dp1, would_stall); no control effect")

    # The physical wheel-copy channel must be a live copy of the wheel (legacy variable rc8). Before any override,
    # sample the steering channel vs the wheel copy for ~1.5 s; if they do not agree the radio mixer is not set and the blend
    # stays OFF for this run (middle band = plain hand-back). A pinned 999 read as "hard left"
    # and was blended in on 2026-09-07 -- this is the gate that stops that.
    wheel8_ok = False
    if fc is not None:
        t0c = time.monotonic(); agree = 0; total = 0
        while time.monotonic() - t0c < 1.5:
            t_ = fc.poll(); time.sleep(0.05)
            if t_.get("rc1") and t_.get("rc8"):
                total += 1; agree += abs(t_["rc1"] - t_["rc8"]) <= 60
        wheel8_ok = total >= 5 and agree >= 0.9 * total
        print(f"blend gate: physical wheel copy {'TRACKS' if wheel8_ok else 'does NOT track'} the wheel "
              f"({agree}/{total} samples) -> blend {'ENABLED' if wheel8_ok else 'OFF, middle = hand-back'}")
    fwd = None
    if fc is not None:
        receipt = (lambda row: writer.put("line", json.dumps(row) + "\n")) if writer else None
        def owner_pose():
            pose = OUTPUT_POSE
            # v15.6: the correction comes from the mount calibration, which a taught path
            # merely happens to carry. With no path and no --mount-yaw-deg there is no
            # correction to apply and none is invented; the heading is passed through and
            # anything that needs a truck heading must refuse it for itself.
            if pose is not None and finite(pose.heading) and mount_yaw_deg is not None:
                return replace(pose, heading=pose.heading - math.radians(mount_yaw_deg))
            return pose
        if a.owner == "simple":
            from owner_simple import SimpleOwner
            fwd = SimpleOwner(fc, a, sending, override_weight, receipt, CRUISE_RC_MAX_AGE_MS, CRUISE_MIN_BAND, CRUISE_ABS_MAX)
            print(f"SIMPLE OWNER (2026-10-01): mode switch far end = HUMAN, middle = ASSIST, near end = {a.sa_near.upper()}; speed dial = speed "
                  f"(band {a.cruise_band}); kill dial = kill; wheel/trigger override while touched. No gesture, no epochs.")
        else:
            fwd = OutputOwner(fc, a, sending, cruise_decide, brake_latch_update, override_weight,
                              owner_pose, receipt, CRUISE_RC_MAX_AGE_MS, CRUISE_MIN_BAND, CRUISE_ABS_MAX)
        fwd.enabled = wheel8_ok; fwd.start()
        if a.override: print(f"OVERRIDE (v13d): the steering head steers until |wheel| > {a.override_dz:g}, then the wheel entirely; the steering head back {a.override_hold_ms} ms after release; steering-source dial ignored")
        print(f"output owner: {a.wheel_hz:g} Hz in EVERY switch position; startup inhibited. "
              "Start: neutral trigger, HOLD + bottom speed dial, wait for stopped_pose, raise the speed dial in HOLD, then leave HOLD. "
              "Every cruise cancellation requires this gesture again. Pose standstill thresholds await truck validation.")

    # v15.6 Phase 1A: the status snapshot. Published off the sender thread, atomically, so a
    # status read can never delay a command. LIVE_STATUS is the frame loop's half; everything
    # else is read at publication time from the owner and the FC.
    LIVE_STATUS = dict(path_valid=tpath is not None, path_reason=None, settings_revision=0,
                       applied_cell=None, applied_changes=None, frames=0, recording=None)
    status_pub = None
    if fwd is not None and a.status_file:
        import package_id as PKGID
        import truck_status as TRUCKSTATUS
        # TWO identities, because they answer different questions. `running` is the closure this
        # process actually imported; `package` is the digest of the verified deployment the files
        # came from, read from the MANIFEST.json the deploy gate wrote beside them. They differ
        # legitimately (the package also carries tools this process never imports), so a status
        # reader must be able to see both rather than be told one number and left to guess.
        run_digest, _pkg_files = PKGID.running(__file__)
        pkg_digest = pkg_dir = None
        pkg_verified = None
        try:
            _m = json.loads((Path(__file__).resolve().parent / "MANIFEST.json").read_text())
            pkg_digest, pkg_dir = _m.get("digest"), _m.get("directory")
            if not _m.get("gates_passed"):
                print("WARNING: this package's MANIFEST.json records that its deployment gates did NOT pass.")
            # The manifest's digest is a CLAIM about these files. Check it once, here, against
            # the bytes on disk -- a manifest copied beside code it does not describe would
            # otherwise report a gated identity for an ungated directory.
            _root = Path(__file__).resolve().parent
            _drift = [n for n, h in (_m.get("files") or {}).items()
                      if not (_root / n).is_file() or PKGID.sha256_file(_root / n) != h]
            pkg_verified = not _drift
            if _drift:
                print(f"🔴 MANIFEST MISMATCH: {len(_drift)} file(s) beside this script do not match "
                      f"the manifest that claims to describe them: {', '.join(sorted(_drift)[:6])}"
                      + (" ..." if len(_drift) > 6 else ""))
                print("   The running code is NOT the package that passed its gates.")
        except (OSError, ValueError):
            pass
        print(f"package: running closure {run_digest[:12] if run_digest else '?'}"
              + (f"; deployed package {pkg_digest[:12]} ({pkg_dir})" if pkg_digest else
                 "; NO MANIFEST.json beside this file -- this is not a gated deployment"))
        def collect_status():
            tele = fc.tele if fc is not None else {}
            pose = OUTPUT_POSE
            out = fwd.snapshot()
            # Applied, not requested: these are read back off the live args namespace, which is
            # what the loop and the owner actually consume. A plan file on disk proves nothing.
            effective = {k: getattr(a, k, None) for k in
                         ("lookahead", "gain", "min_rows", "cap_kappa", "kff", "ff_w",
                          "cruise_mps", "cruise_max", "path_max_blind_m", "stop_after_laps", "cell_seconds")}
            servo_t = fc.tele_t.get("servo") if fc is not None else None
            return dict(
                package_digest=pkg_digest, running_digest=run_digest, package_dir=pkg_dir,
                package_verified=pkg_verified,
                run_log=a.log, note=a.note,
                sweep=LIVE_STATUS.get("sweep"),
                settings_revision=LIVE_STATUS["settings_revision"],
                applied_cell=LIVE_STATUS["applied_cell"], applied_changes=LIVE_STATUS["applied_changes"],
                effective=effective, frames=LIVE_STATUS["frames"],
                recording=LIVE_STATUS["recording"] or (pose.recording if pose else None),
                path_name=Path(a.path).name if a.path else None,
                path_valid=bool(LIVE_STATUS["path_valid"]), path_reason=LIVE_STATUS["path_reason"],
                mount_yaw_deg=mount_yaw_deg, mount_yaw_source=mount_yaw_source if mount_yaw_deg is not None else None,
                # CURRENT age, not age-on-receipt: a frozen pose looked young while the owner was
                # correctly vetoing it. `age_on_receipt_ms` is kept because it is what the owner's
                # own freshness arithmetic uses.
                pose=(dict(seq=pose.seq,
                           age_ms=round((max(0.0, pose.age_s) + time.monotonic() - pose.received) * 1000, 1),
                           age_on_receipt_ms=round(pose.age_s * 1000, 1), tracking=pose.tracking,
                           speed=round(pose.speed, 3) if finite(pose.speed) else None,
                           recording=pose.recording) if pose is not None else None),
                # Reported SERVO3 verifies the FIRMWARE'S OUTPUT REPORT. It is not the electrical
                # pin, the ESC state or wheel torque, and its SOURCE age is not established here --
                # a recent dequeue does not make an old packet fresh, so 'unknown' is the honest word.
                servo=dict(value=tele.get("servo3"), seq=tele.get("servo3_seq"),
                           recv_age_ms=(round((time.monotonic() - servo_t) * 1000) if servo_t else None),
                           source_age_ms=None,
                           source_age_note="dequeue age only; packet source time not validated"),
                batch=(dict(cell=out.get("batch_cell"), cells=a.batch_cells, phase=out.get("batch_phase"))
                       if a.batch_cells else None),
                reverse=dict(profile="provisional-2026-09-14" if a.stall_recovery else None,
                             acceptance=("ACCEPTED BY --reverse-ready" if a.reverse_ready
                                         else "UNACCEPTED"),
                             reason=(None if a.reverse_ready else
                                     "no physical reverse characterisation has been accepted (Phase 2 owed)"),
                             trial_mode=bool(a.reverse_trial),
                             ceiling=a.trial_max_fraction if a.reverse_trial else None,
                             trials_done=getattr(fwd, "trials_done", 0),
                             last_trial=({k: v for k, v in (fwd.trial_state() or {}).items()
                                          if k in ("trial_id", "outcome", "reason", "fraction",
                                                   "net_displacement_m", "duration_s")}
                                         or None) if a.reverse_trial else None),
                output=out)
        status_pub = TRUCKSTATUS.Publisher(a.status_file, collect_status, hz=5.0,
                                           static=dict(tool="live_trail.py", started_wall=time.time()))
        status_pub.start()
        print(f"status: publishing to {a.status_file} at 5 Hz; read it with "
              f"`python3 ~/live/truck_status.py` (--json for the machine view)")

    trial_server = None
    if fwd is not None and a.reverse_trial:
        import reverse_trial as REVERSETRIAL
        trial_server = REVERSETRIAL.TrialServer(
            fwd, a.trial_dir, a.recovery_config, a.trial_max_fraction,
            identity=dict(package_digest=pkg_digest if a.status_file else None,
                          run_log=a.log, mount_yaw_deg=mount_yaw_deg,
                          mount_yaw_source=mount_yaw_source if mount_yaw_deg is not None else None))
        trial_server.start()
        print(f"\nSUPERVISED REVERSE TRIAL MODE. No forward start is granted in this process by "
              f"any gesture.\n  amplitude ceiling {a.trial_max_fraction:g} (absolute bound "
              f"{__import__('recovery').ABS_FRACTION_CEILING:g})\n  requests: {a.trial_dir}/trial_request.json"
              f"\n  results + append-only ledger: {a.trial_dir}/\n"
              f"  drive it from the Mac: reverse_trial.py --orin <user@host> --fraction <f> ...\n"
              f"  Each trial is admitted only from CONFIRMED REST with every hard stop clear.\n"
              f"  The wheel observation is yours: pose measures the BODY and cannot see wheelspin.")

    if a.startup_check:
        # The whole startup has now run: args, plan validation, model load, run-spec serialisation,
        # FC connect, owner construction and start, status publication. Give the owner a couple of
        # ticks so a decision-path crash surfaces here rather than at the truck, then report.
        # ABSENCE OF BAD NEWS IS NOT GOOD NEWS. The first version of this check passed on three
        # booleans that an owner which never ticked -- or which died on its first tick -- leaves
        # in exactly their passing states. It now requires a LIVE thread, ADVANCING ticks and no
        # recorded thread exception before it will look at anything else.
        before = fwd.snapshot().get("ticks", 0) if fwd is not None else 0
        time.sleep(1.0)
        snap = fwd.snapshot() if fwd is not None else {}
        published = status_pub.write_once() if status_pub is not None else None
        ok = True
        print("\n--- STARTUP CHECK ---")
        print(f"sending: {sending} (forced off)")
        if fwd is not None:
            ticks = snap.get("ticks", 0)
            print(f"owner: alive={fwd.is_alive()} ticks {before} -> {ticks} "
                  f"(expected ~{a.wheel_hz:g}/s), thread_error={snap.get('thread_error')}")
            if not fwd.is_alive():
                print("FAIL: the output owner thread is not alive.")
                ok = False
            if ticks <= before:
                print("FAIL: the owner did not advance a single tick in one second.")
                ok = False
            if snap.get("thread_error"):
                print(f"FAIL: the owner thread raised: {snap['thread_error']}")
                ok = False
        else:
            print("no --fc: the owner was not constructed, so this check covers startup only.")
        print(f"owner state: {snap.get('sends', 0)} sends, veto {snap.get('veto')}, "
              f"cruise_why {snap.get('cruise_why')}, permission {snap.get('autonomous_permission')}")
        if snap.get("autonomous_permission"):
            print("FAIL: the owner granted autonomous permission during a startup check.")
            ok = False
        if snap.get("sends", 0):
            print("FAIL: the owner reported sends with sending disabled.")
            ok = False
        if trial_server is not None:
            print(f"trial server: alive={trial_server.is_alive()}, last_error={trial_server.last_error}")
            if not trial_server.is_alive():
                print("FAIL: the trial server thread is not alive.")
                ok = False
        if status_pub is not None:
            print(f"status published: {a.status_file} ({status_pub.writes} writes, {status_pub.errors} errors)")
            if published is not None and published.get("collect_error"):
                print(f"FAIL: status collect raised {published['collect_error']}")
                ok = False
            if status_pub.errors:
                print("FAIL: the status publisher could not write.")
                ok = False
        if writer is not None:
            writer.put("setup-observation", setup_capture.observe_dino_run, setup_snapshot, None, True)
            writer.close()
        if status_pub is not None:
            status_pub.stop()
        if trial_server is not None:
            trial_server.stop()
        if fwd is not None:
            fwd.stop()
        print("STARTUP CHECK: PASS" if ok else "STARTUP CHECK: FAIL")
        sys.exit(0 if ok else 1)

    # The background publishers are daemons, so an exit would abandon them mid-write rather than
    # letting them publish a final honest snapshot. Register them for the normal cleanup path.
    def _stop_publishers():
        for thread in (trial_server, status_pub):
            if thread is not None:
                try:
                    thread.stop(); thread.join(timeout=2.0)
                except Exception:
                    pass
    import atexit as _atexit; _atexit.register(_stop_publishers)

    DECODER[0] = a.decoder; AU_DELIM[0] = (a.au_delim == "on")
    src = video_frames(a.video, a.start, a.step, a.secs, a.video_fps) if a.video else moblin_frames(a.port, a.secs, a.ingest)
    relay = RelayLink(a.relay_udp, a.relay_sign) if a.relay_udp else None     # stage 4: the external planner's curvature, log or steer
    frames_udp = None                                                          # 2026-10-01: frames out for the external planner's node
    if a.frames_udp:
        # TCP on loopback, length-prefixed, NON-BLOCKING: a slow or absent bridge drops frames, never the control loop.
        # (UDP was the first draft; a 57 KB datagram is over macOS's 9 KB limit and fragments on Linux.)
        import socket as _socket, struct as _struct
        frames_sent = 0; frames_dropped = 0
        class _FrameLink:
            def __init__(self, port): self.port = port; self.sock = None; self.next_try = 0.0
            def send(self, payload):
                now = time.monotonic()
                if self.sock is None:
                    if now < self.next_try: return False
                    self.next_try = now + 2.0
                    try:
                        s_ = _socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                        s_.setblocking(False); s_.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1); self.sock = s_
                    except OSError: return False
                try:
                    self.sock.sendall(_struct.pack(">I", len(payload)) + payload); return True
                except BlockingIOError:
                    return False                              # bridge behind: drop this frame, keep the connection
                except OSError:
                    try: self.sock.close()
                    except OSError: pass
                    self.sock = None; return False
        frames_udp = _FrameLink(a.frames_udp)
        print(f"frames out: 160x120 bgr8, length-prefixed TCP to 127.0.0.1:{a.frames_udp} (frame_bridge.py publishes /image_raw)")
    hello_logged = False
    sweep_checkpoint_t = 0.
    n = 0; lat = []; kappa_ema = None; last_cmd = None; last_cmd_t = 0.0; state = "init"; brake_latched = False
    steer_i = 0.0; steer_i_t = None; steer_i_why = "off"     # v16 integral: value (1/m), last update time, why it did not learn this frame
    # v15 taught-path state: alignment validity + epoch, last localisation, blind distance, the previous pose for the reset detector
    path_valid = tpath is not None; path_epoch = 0; path_idx = None; path_dir = 1; blind_m = 0.0; path_prev = None; path_reason = None; path_t_prev = None
    lap_prog = 0.0; lap_prev_idx = None; laps_done = False; laps_n = 0.0   # v15.4 --stop-after-laps: unwrapped progress along the path
    route_end = False; route_rem_m = None                                     # v15.6 open route: latched at the last point until the truck heads back
    motion_epoch = 0
    batch_cell = 0
    cell_plan_applied = None                  # v15.5: set on the frame a --cell-plan change lands
    last_gx = None; last_gx_t = None          # v13 --goal-jump: the goal_x last steered on, and when
    sched_hi = False                          # v13b --gain-low: True while the schedule holds the full gain
    sched_on_t = sched_off_t = None           # v13c latch timers: when the on / off condition first became true (None = not true now)
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    loop_error = None                          # an exception escaping the loop (SIGINT is a flag, never this)
    try:
        host_state_t = 0.0                                    # robot-eyes ticket 12: last 0x0D to the phone
        for bgr in src:
            if stop["flag"]: break
            t0 = time.monotonic()
            if frames_udp is not None:                       # one phone client: hand this frame to the external planner's node, downscaled
                try:
                    # 2026-10-02 (kitchen drive): the numpy block mean over 1920x1440x3 cost ~110 ms PER FRAME on the Orin
                    # (138 ms loop vs 26-37 on every 09-27..09-30 run). cv2 INTER_AREA is the same area mean in a few ms.
                    small = cv2.resize(bgr, (160, 120), interpolation=cv2.INTER_AREA)
                    if frames_udp.send(_struct.pack(">QHH", int(PHONE["pts_us"] or 0), 160, 120) + np.ascontiguousarray(small).tobytes()):
                        frames_sent += 1
                    else:
                        frames_dropped += 1
                except Exception as _e:
                    frames_dropped += 1
                    if frames_dropped == 1: print(f"frames out: FAILED ({_e})")
            cell_plan_applied = None          # one frame only; the boundary below may set it
            owner_frame = fwd.snapshot() if fwd is not None else {"epoch": 0, "cruise_on": False}
            if sweep is not None:
                # inbox commands first ([] when none arrived); a `now` then lands in this tick if eligible
                sweep_events = sweep_inbox.poll(t0, sweep)
                sweep_events += sweep.tick(t0, sweep_eligible(owner_frame, OUTPUT_POSE, t0)
                                           and not writer.err and not (recov is not None and recov.active), a)
                sweep_state = sweep.snapshot()
                LIVE_STATUS["sweep"] = sweep_state
                LIVE_STATUS["settings_revision"] = sweep.revision
                LIVE_STATUS["applied_cell"] = sweep.index
                LIVE_STATUS["applied_changes"] = dict(sweep_state["values"])
                for event in sweep_events:
                    event.update(t=time.time(), monotonic=t0, first_frame=n+1)
                    writer.put("line", json.dumps(event) + "\n")
                    print(json.dumps(event), flush=True)
                if sweep_events or t0 - sweep_checkpoint_t >= 1.:
                    writer.put("sweep-checkpoint", dict(sweep_state, t=time.time(), frame=n+1))
                    sweep_checkpoint_t = t0
            cell_now = owner_frame.get("batch_cell") or 0
            if cell_now != batch_cell:
                batch_cell = cell_now
                lap_prog = laps_n = 0.0
                lap_prev_idx = None
                laps_done = False
                kappa_ema = last_cmd = last_gx = last_gx_t = None
                # v15.5 --cell-plan. The owner only advances `batch_cell` from `stopping` to
                # `await_frame`, which it does ONLY after measured rest plus fresh neutral servo
                # feedback (batch.py). So autonomous throttle is already withdrawn here, and this
                # is the one moment where writing the shared args namespace cannot reach a moving
                # vehicle -- the owner reads it without a lock from its own thread.
                if cell_plan_obj is not None:
                    moved, note = cell_plan_obj.apply_at_boundary(
                        a, cell_now, owner_frame.get("batch_phase"))
                    print(f"cell {cell_now}: {note}")
                    if moved is not None:
                        cell_plan_applied = {"cell": cell_now, "changed": moved}
                        # v15.6: the settings revision advances ONLY where the plan actually wrote
                        # to the namespace. A refused boundary leaves it where it was, so a status
                        # reader can tell "applied" from "asked for".
                        LIVE_STATUS["settings_revision"] += 1
                        LIVE_STATUS["applied_cell"] = cell_now
                        LIVE_STATUS["applied_changes"] = {k: v["to"] for k, v in moved.items()}
            if spdloop is not None: spdloop.reset_for_epoch((owner_frame["epoch"], owner_frame.get("motion_epoch", 0)))
            if owner_frame.get("motion_epoch", 0) != motion_epoch:
                motion_epoch = owner_frame["motion_epoch"]
                if motion is not None: motion.reset()
                lap_prev_idx = None
                kappa_ema = last_cmd = None
                last_gx = last_gx_t = None
            small = cv2.resize(bgr, (FEED_W, FEED_H))
            img = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)                              # == feats_v1.py
            x = torch.from_numpy(img).to(dev).permute(2, 0, 1)[None].to(dt) / 255.0
            x = (x - mean) / std
            with torch.no_grad():
                tok = model.forward_features(x)["x_norm_patchtokens"]
            tok = tok.reshape(GH, GW, 384).float()
            logits = (tok[a.r0:].reshape(-1, 384) @ coef_t + bias).reshape(NR, GW)
            probs = torch.sigmoid(logits).cpu().numpy()
            probs_raw, blue_rec = probs, None
            if blue_cfg is not None:                     # same feed pixels as the perception model (img), same patch grid
                probs, blue_rec = blue_gate.apply(img, probs, blue_cfg, a.r0)
            trail_score = None
            if tw is not None:
                tp = torch.sigmoid(tok[a.r0:].reshape(-1, 384) @ tw[0] + tw[1]).reshape(NR, GW)
                trail_score = round(float(tp.max(dim=1).values.mean()), 3)
            danger = None
            if dw is not None:
                danger = torch.sigmoid(tok.reshape(-1, 384) @ dw[0] + dw[1]).reshape(GH, GW).cpu().numpy()

            # v9: the lookahead is read BEFORE the fit, because --cap-kappa's window is expressed
            # in multiples of it. Nothing else moved: `look` is a knob position off the cached
            # telemetry dict, not a measurement of this frame.
            look = a.lookahead
            if a.look_trim and fc is not None and fc.tele.get("look_pwm") and 900 <= fc.tele["look_pwm"] <= 2100:
                look = round(max(1.0, min(3.0, 1.0 + (fc.tele["look_pwm"] - 1000) / 1000.0 * 2.0)), 2)
            # v12: the pitch this frame is projected with. With --pitch live it is the phone's own ARKit pitch (status <= 3 s old);
            # the suspension moves it through 22 deg and kff flipped sign at the wrong constant (FINDINGS 40).
            pitch_f = pitch
            if pitch_live and PHONE["status"] and time.monotonic() - PHONE["status_t"] < 3.0 and isinstance(PHONE["status"].get("pitch_deg"), (int, float)):
                pitch_f = float(PHONE["status"]["pitch_deg"])
            curve = ((a.cap_kappa, a.r0, pitch_f, a.cam_h, CAP_WIN_NEAR * look, CAP_WIN_FAR * look)
                     if a.cap_kappa else None)
            fit = {}
            line, conf, mass, cent = line_from_probs(probs, a.conf, cap, curve, fit, vote_rows)
            kept = int((~np.isnan(line)).sum())
            pts = [g for g in (ground_point(q + a.r0, line[q], pitch_f, a.cam_h)
                               for q in range(NR) if not np.isnan(line[q])) if g]
            # v8: the feed-forward's own ground points -- the RAW per-row centroids (gated by the
            # same --conf), NOT the curvature-capped line, which is straight on 84-97 % of bend
            # frames and so carries no curvature at all. Built only when --kff is on.
            kff_rows = []
            if a.kff:
                for q in range(NR):
                    if np.isnan(cent[q]) or conf[q] < a.conf: continue
                    g = ground_point(q + a.r0, cent[q], pitch_f, a.cam_h)
                    if g: kff_rows.append((g[0], g[1], float(mass[q])))
            kappa_raw, goal = pure_pursuit_kappa(pts, look, a.cam_fwd)

            now = time.monotonic()                    # every timer below; the record's "t" is wall time, set where it is written
            gain = a.gain
            if a.gain_trim and fc is not None and fc.tele.get("gain_pwm") and 900 <= fc.tele["gain_pwm"] <= 2100:
                gain = round(max(0.5, min(2.5, 0.5 + (fc.tele["gain_pwm"] - 1000) / 1000.0 * 2.0)), 2)
            if a.gain_low > 0:                                    # v13b gain schedule (see the flag's help); v13c adds the latch timers
                fk = fit.get("kappa")
                bent = fk is not None and abs(fk) >= a.gain_kappa
                if not sched_hi:
                    # v13c: the line must stay bent for --gain-on-s before the full gain latches (0 s = the v13b instant switch)
                    sched_on_t = (sched_on_t if sched_on_t is not None else now) if bent else None
                    if sched_on_t is not None and now - sched_on_t >= a.gain_on_s: sched_hi = True; sched_off_t = None
                else:
                    centred = (not bent) and goal is not None and abs(goal[0]) < a.gain_hold_x \
                        and (fk is None or abs(fk) < (a.gain_kappa_low or 0.5 * a.gain_kappa))
                    sched_off_t = (sched_off_t if sched_off_t is not None else now) if centred else None
                    if sched_off_t is not None and now - sched_off_t >= a.gain_off_s: sched_hi = False; sched_on_t = None
                gain = gain if sched_hi else a.gain_low
            # v7 (2026-09-09, kitchen oval): DO NOT STEER ON A LINE FITTED THROUGH TOO FEW ROWS.
            # Measured on run auto-boot-002229: the truck held the straight on 17/17 rows, entered
            # the corner, ran out of visible tape mid-turn (rows 17 -> 11 -> 7), and the fit through
            # the survivors REVERSED SIGN -- full left lock at 6.9 s became full right at 7.5 s, all
            # on 6-9 rows. Sheet C (validation-visuals) shows the same shape: when the tape leaves
            # the frame the head does not go quiet, it commands full lock at a cardboard bag.
            # This is the operator's field rule of 2026-09-07 ("off the trail -> steer toward where the
            # trail was last seen") in its cheapest form: hold the last CONFIDENT command instead of
            # inventing a new one. --min-rows 0 keeps the old behaviour exactly.
            lowrow = a.min_rows > 0 and kept < a.min_rows
            # v13: the same hold path, three more reasons to take it. `gate` is logged so a held frame says WHY.
            gate = "rows" if lowrow else None
            if not lowrow and a.min_vote > 0 and fit.get("vote", 0) < a.min_vote:
                lowrow = True; gate = "vote"
            if not lowrow and a.goal_jump > 0 and goal is not None and kept < 13 and last_gx is not None \
                    and now - last_gx_t <= 1.5 and abs(goal[0] - last_gx) > a.goal_jump:
                lowrow = True; gate = "jump"
            hold_ms = a.lowrow_hold_ms if lowrow else a.hold_ms
            # v8 (2026-09-09): CURVATURE FEED-FORWARD, kappa_total = kff*kappa_path + gain*kappa_pp.
            # It lives INSIDE the lane branch on purpose, so it inherits every gate the pursuit
            # term already passes -- a lane exists, the fit succeeded, and --min-rows is satisfied
            # -- and then adds two of its own: --kff-min-rows (>= the pursuit gate, never less) and
            # path_curvature's window/points/residual test. On a frame the tool does not steer on
            # (lowrow or no lane) NOTHING is fed forward: the hold path re-sends `last_cmd`, which
            # is the last TOTAL command including whatever feed-forward was in it when the line was
            # last trusted. Order: the feed-forward is clamped on its own (--kff-max) BEFORE the
            # sum, so a wild curvature estimate can never spend more than its own budget; the sum
            # is then clamped to --kappa-max and only then EMA'd -- the same order as v7, and the
            # right one, because the EMA of a clamped sequence stays inside the clamp while a clamp
            # after the EMA would leave the filter holding a value it can never command.
            # Stage 4 (2026-09-30): the external planner's curvature. Logged every frame; in steer mode a FRESH, listening command
            # replaces the camera's kappa_raw here -- after the gates were computed, so lowrow/gate are stood down for
            # this frame only (its node has its own road_present gate; `ls` false falls straight back to the camera).
            # Speed is never read from it. Everything downstream (gain, kff, --kappa-max, EMA, override, owner) is untouched.
            relay_rec = None; kappa_src = "cam"
            if relay is not None:
                rz, rz_age, rz_fresh = relay.latest(now, a.relay_max_age_ms)
                rz_used = bool(a.relay_mode == "steer" and rz_fresh and rz is not None and rz["ls"])
                relay_rec = {"seq": rz["seq"] if rz else None, "k": None if rz is None else round(rz["k"], 4),
                             "age_ms": None if rz_age is None else round(rz_age, 1), "ls": bool(rz and rz["ls"]),
                             "v": None if rz is None else round(rz["v"], 3), "used": rz_used, "n": relay.n}
                if rz_used:
                    kappa_raw = rz["k"]; lowrow = False; gate = None; kappa_src = "relay"
            kappa_pp = kappa_path = None; kappa_ff = 0.0
            kff_why = "off" if not a.kff else "no_lane"    # overwritten below on a steering frame
            if kappa_raw is not None and not lowrow:
                kappa_pp = gain * kappa_raw
                if a.steer_i > 0:                                     # v16: learn only while the STEERING HEAD has the plant, on a straight, tape well in view, small offset
                    ost = owner_frame.get("state"); model_has_it = ost in ("assist", "blend") and not owner_frame.get("override_on")
                    if not model_has_it:                              # 01:3x: the first drive learned its whole cap while the operator drove it back by hand -> reset, never learn
                        steer_i = 0.0; steer_i_why = "not_model"
                    elif goal is None: steer_i_why = "no_goal"
                    elif abs(kappa_pp) >= a.steer_i_straight: steer_i_why = "bend"
                    elif kept < a.steer_i_rows: steer_i_why = "rows"
                    elif abs(goal[0]) > 0.08: steer_i_why = "far"      # a convergence from a displaced start is not a bias
                    else:
                        dt_i = 0.0 if steer_i_t is None else min(0.5, now - steer_i_t)
                        cap = a.steer_i_max * KFL
                        steer_i = max(-cap, min(cap, steer_i + a.steer_i * goal[0] * dt_i))
                        steer_i_why = "learn"
                    steer_i_t = now
                kappa_total = kappa_pp + steer_i
                if a.kff:
                    if kept < max(a.min_rows, a.kff_min_rows):
                        kff_why = "rows"
                    else:
                        kappa_path, kff_why = path_curvature(kff_rows, look)
                    if kappa_path is not None:
                        kappa_ff = max(-a.kff_max, min(a.kff_max, a.kff * kappa_path))
                    kappa_total = kappa_pp + steer_i + kappa_ff
                kappa_raw = max(-a.kappa_max, min(a.kappa_max, kappa_total))
                kappa_ema = kappa_raw if (kappa_ema is None or a.ema <= 0) else a.ema * kappa_raw + (1 - a.ema) * kappa_ema
                steer = (kappa_ema + a.steer_offset) / KFL
                last_cmd, last_cmd_t, state = steer, now, "lane"
                if goal is not None: last_gx, last_gx_t = goal[0], now      # v13 --goal-jump reference
            elif last_cmd is not None and (now - last_cmd_t) * 1000 < hold_ms:
                steer = last_cmd; state = "lowrow" if lowrow else "hold"
                # v13 --lowrow-lock: mid-turn with the rows gone, hold FULL lock in the turn direction. kappa_ema is
                # left alone so the EMA resumes from the last confident value when the tape comes back.
                if lowrow and a.lowrow_lock > 0 and kappa_ema is not None and abs(kappa_ema) > a.lowrow_lock * KFL:
                    steer = math.copysign(1.0, kappa_ema); state = "lowrow_lock"
            else:
                steer = None; state = "release"; kappa_ema = None
            # ---- v15 taught-path feed-forward (only with --path). Decided AFTER the camera branch so the fresh/held decision is the
            # v13 one; it composes the command from the reference and the camera's share, or withdraws the camera on a gate.
            path_rec = None; kappa_tot = None
            if tpath is not None:
                fresh = (state == "lane")                                   # the camera steered THIS frame (gated frames are lowrow/hold/release)
                dt_p = 0.0 if path_t_prev is None else (now - path_t_prev); path_t_prev = now
                pz = POSES[-1] if POSES else None; why = None; ref_k = None; pose_age_ms = None; dist = None; pred = None
                if pz is None: why = "no_pose"
                elif PHONE["clock_off"] is None: why = "no_clock"
                else:
                    seq, t_utc, px, pzz, hdg, vv, trk, recid = pz
                    pose_age_ms = ((time.time() + PHONE["clock_off"]) - t_utc) * 1000.0
                    if hdg is None or vv is None: why = "no_heading"
                    elif trk != "normal": why = "tracking_" + str(trk)
                    elif pose_age_ms > a.pose_max_age_ms: why = "pose_stale"
                    if why is None and path_valid and path_prev is not None and path_prev[0] != seq:
                        # RESET / JUMP / RE-POSITION detector against the previous pose used
                        _, t0p, x0p, z0p, h0p, v0p, _, rec0 = path_prev; ddt = max(1e-3, t_utc - t0p)
                        jump = math.hypot(px - x0p, pzz - z0p) - (1.5 * max(v0p or 0.0, vv) * ddt + 0.10)
                        dh = abs((hdg - h0p + 180) % 360 - 180)
                        if ddt > a.path_max_pose_gap_s: path_valid = False; path_reason = f"pose gap {ddt:.2f} s"
                        elif jump > a.path_jump_m: path_valid = False; path_reason = f"jump {jump + 1.5*max(v0p or 0.0, vv)*ddt + 0.10:.2f} m in {ddt:.2f} s"
                        elif dh > 90 and ddt < 0.5: path_valid = False; path_reason = f"heading jump {dh:.0f} deg in {ddt:.2f} s"
                        elif recid != rec0 and rec0 is not None: path_valid = False; path_reason = f"recording {rec0} -> {recid}"
                        elif seq is not None and path_prev[0] is not None and seq < path_prev[0]: path_valid = False; path_reason = f"pose_seq {path_prev[0]} -> {seq}"
                        if not path_valid: path_epoch += 1; print(f"[{n:5d}] PATH ALIGNMENT INVALID (epoch {path_epoch}): {path_reason} -- reference off for the rest of the run; re-teach and restart")
                    if why is None: path_prev = pz
                    if why is None and not path_valid: why = "invalid"
                    if why is None:
                        hr = math.radians(hdg); ax, az = tpath.axle(px, pzz, hr)
                        # predict the axle pose to now (+ actuator delay) with the last commanded curvature: translation AND rotation
                        tau = max(0.0, pose_age_ms / 1000.0) + a.ff_act_s; k_use = (last_cmd or 0.0) * KFL
                        # v_mps is unsigned. It cannot predict forward travel
                        # while the independent owner is retreating or settling.
                        in_recovery = owner_frame.get("autonomous_permission", False) and not owner_frame["cruise_on"]
                        arc = 0.0 if in_recovery else vv * tau
                        h2 = hr + k_use * arc; hm = 0.5 * (hr + h2); ax += arc * math.cos(hm); az += arc * math.sin(hm); pred = (ax, az, h2)
                        idx, dist = tpath.nearest(ax, az, path_idx, path_dir, heading=h2); path_dir = tpath.direction(idx, h2); path_idx = idx
                        if not tpath.closed:                                   # v15.6 open route: the reference has a last point
                            route_rem_m = tpath.remaining_m(idx, path_dir)
                            if route_rem_m <= a.route_end_m: route_end = True
                            elif route_end and route_rem_m > a.route_end_m + 0.5: route_end = False   # turned round and heading back
                        if a.stop_after_laps and owner_frame["cruise_on"] and dist <= a.path_max_dist_m:
                            M = len(tpath.kappa)
                            if lap_prev_idx is not None:
                                d_idx = idx - lap_prev_idx
                                if tpath.closed and d_idx > M / 2: d_idx -= M
                                elif tpath.closed and d_idx < -M / 2: d_idx += M
                                if abs(d_idx) < M / 4: lap_prog += d_idx     # a bigger jump is a relocalisation, not travel
                            lap_prev_idx = idx
                            laps_n = abs(lap_prog) / float(M)
                            if laps_n >= a.stop_after_laps and not laps_done:
                                laps_done = True
                                print(f"\n*** --stop-after-laps: {laps_n:.2f} laps of the taught path -- cruise DISENGAGED, "
                                      f"the trigger owns throttle. ***", flush=True)
                        else: lap_prev_idx = None         # manual/pre-start/off-path motion cannot bridge progress
                        if route_end: why = "route_end"                        # v15.6: no path term at or beyond the last point
                        elif dist > a.path_max_dist_m: why = "off_path"
                        else: ref_k = max(-KFL, min(KFL, a.path_sign * path_dir * float(tpath.kappa[idx])))
                if fresh: blind_m = 0.0
                elif ref_k is not None: blind_m += max(0.0, (vv or 0.0)) * dt_p
                ret_rec = None
                if ref_k is not None and fresh:
                    kappa_tot = max(-a.kappa_max, min(a.kappa_max, (1.0 - a.ff_w) * ref_k + a.ff_w * (kappa_ema if kappa_ema is not None else 0.0)))
                    steer = (kappa_tot + a.steer_offset) / KFL; last_cmd, last_cmd_t, state = steer, now, "lane"; mode = "blend"
                elif ref_k is not None and blind_m <= a.path_max_blind_m:
                    kappa_tot = ref_k; steer = (kappa_tot + a.steer_offset) / KFL; last_cmd, last_cmd_t, state = steer, now, "path"; mode = "path"
                elif ref_k is not None: mode = "blind_exceeded"          # v13 hold/release stands
                else: mode = why
                # ---- v15.3 RETURN TO THE PATH: the design's "no trail" state. The camera has lost the tape and the reference has
                # run out -- either it carried us the full --path-max-blind-m, or we are further off the line than the reference is
                # trusted for. Instead of holding the last command and straightening (which is how a lost corner became a crash),
                # steer BACK to the taught path by pure pursuit on the reference itself. The camera wins the instant it sees tape
                # again, and the human wins always (the knob and the kill are untouched). Bounded in distance and in time.
                if a.return_to_path and not fresh and pred is not None and path_valid and why in (None, "off_path") and not route_end:
                    far = (dist is not None and dist > a.path_max_dist_m)
                    spent = (ref_k is not None and blind_m > a.path_max_blind_m)
                    if (far or spent) and dist is not None and dist <= a.return_max_dist_m and not ret_gave_up:
                        if ret_t0 is None: ret_t0 = now
                        if now - ret_t0 > a.return_max_s:
                            ret_gave_up = True; ret_stop_reason = "return_timeout"; ret_rec = {"why": "timeout"}
                        else:
                            k_ret, ret_rec = return_kappa(tpath, pred, path_idx, path_dir, a.return_lookahead, a.return_gain, a.kappa_max)
                            if k_ret is not None:
                                kappa_tot = k_ret; steer = (k_ret + a.steer_offset) / KFL
                                last_cmd, last_cmd_t, state = steer, now, "return"; mode = "return"
                                ret_rec["t_s"] = round(now - ret_t0, 2); ret_rec["dist"] = round(dist, 3)
                    elif dist is not None and dist > a.return_max_dist_m:
                        ret_gave_up = True; ret_stop_reason = "return_too_far"; ret_rec = {"why": "too_far"}
                    elif not ret_gave_up:
                        ret_t0 = None
                elif a.return_to_path and fresh and not ret_gave_up:
                    ret_t0 = None
                path_rec = {"mode": mode, "epoch": path_epoch, "idx": path_idx, "dir": path_dir, "dist": None if dist is None else round(dist, 3),
                            "ref": None if ref_k is None else round(ref_k, 4), "cam": None if kappa_ema is None else round(kappa_ema, 4),
                            "blind_m": round(blind_m, 2), "pose_age_ms": None if pose_age_ms is None else round(pose_age_ms, 1),
                            "sec": None if path_idx is None else tpath.sections[path_idx]}
                if not tpath.closed: path_rec["end"] = route_end; path_rec["rem_m"] = None if route_rem_m is None else round(route_rem_m, 2)
                if pred is not None: path_rec["pred"] = [round(pred[0], 3), round(pred[1], 3), round(math.degrees(pred[2]), 1)]
                if ret_rec is not None: path_rec["ret"] = ret_rec
                if path_reason and mode == "invalid": path_rec["why"] = path_reason
            # Path Recovery: decided after the camera command, before publication. A manoeuvre curvature replaces the
            # camera steer (TURN/SEARCH/ACQUIRE); ALIGN/FOLLOW leave the lane controller in charge. Off: nothing here runs.
            cam_state = state; rdec = None
            if recov is not None:
                rdec = recov.frame(now, owner_frame, state, kept, goal, pts,
                                   vote=fit.get("vote") if a.fit_max_y else None,
                                   sweep=sweep.snapshot() if sweep is not None else None,
                                   context=lambda: dict(head=Path(a.head).name, head_sha_prefix=__import__("hashlib").sha256(w.astype(np.float64).tobytes()).hexdigest()[:12],
                                                        camera_kappa=kappa_ema,
                                                        gain=a.gain, lookahead=a.lookahead, ema=a.ema, steer_offset=a.steer_offset,
                                                        pitch_deg=pitch_f, throttle_us=owner_frame.get("throttle"),
                                                        arkit_v_mps=getattr(OUTPUT_POSE, "speed", None)))
                if rdec.kappa is not None:
                    k_rec = max(-a.kappa_max, min(a.kappa_max, rdec.kappa))      # --kappa-max bounds every steering path
                    steer = max(-1.0, min(1.0, (k_rec + a.steer_offset) / KFL)); state = "recovery"
                    kappa_ema = last_cmd = None          # the lane controller restarts clean at ALIGN
            # Perception publishes intent only. The sender owns all final vetoes,
            # manual reverse scaling and actuator writes, including mode-switch model-out.
            tele = fc.poll() if fc is not None else None
            if time.monotonic() - host_state_t >= 1.0:        # robot-eyes ticket 12: the phone's HUD reads ARMED / DISARMED from this
                host_state_t = time.monotonic()
                send_host_state(None if tele is None else tele.get("armed"), a.owner, None if tele is None else tele.get("mode"))
                send_record_flag()
            hold_sw = bool(tele and tele.get("rc3") is not None and tele["rc3"] >= 1750)
            middle = bool(tele and tele.get("rc3") is not None and 1361 <= tele["rc3"] <= 1620)
            cruise_cmd, _candidate_why = cruise_decide(a, tele, hold_sw, thr_neutral, thr_dz)
            spd_rec = None
            if spdloop is not None:
                sp_mps = cruise_setpoint_mps(a, tele.get("cruise_pwm") if tele else None)
                pzs = OUTPUT_POSE
                v_now = pzs.speed if pzs is not None and pzs.tracking == "normal" and finite(pzs.speed) else None
                v_age = (max(0.0, pzs.age_s) + now - pzs.received) * 1000 if pzs is not None else None
                cruise_cmd, spd_rec = spdloop.update(cruise_cmd, sp_mps, v_now, v_age, now,
                                                     owner_frame["cruise_on"] and _candidate_why is None)
            terminal_reason = ("laps_done" if laps_done and not a.batch_cells else ret_stop_reason)
            if route_end and not a.batch_cells: terminal_reason = "route_end"          # v15.6: cruise off at the end of an open route
            if tpath is not None and not path_valid: terminal_reason = "path_invalid"
            t_pub = time.monotonic()
            output = None
            if fwd is not None:
                fwd.publish(Intent(owner_frame["epoch"], t0, steer, cruise_cmd,
                                   tele.get("cruise_pwm") if tele else None,
                                   terminal_reason or (rdec.stop_reason if rdec is not None else None),
                                   batch_cell if a.batch_cells else None, bool(laps_done and a.batch_cells),
                                   steer_source="path_recovery" if rdec is not None and rdec.kappa is not None else "camera",
                                   throttle_cap=rdec.throttle_cap_us if rdec is not None else None))
                output = fwd.snapshot()  # last ACTUAL send; may precede the above publication
            cruise_on = bool(output and output["cruise_on"])
            cruise_why = output["cruise_why"] if output else "no_fc"
            brake_latched = bool(output and output.get("brake_latched"))
            rev_cmd = output["throttle"] if output and 0 < output["throttle"] < thr_neutral else 0
            blend_w = output.get("blend_w") if output else None
            wheel = output.get("wheel") if output else None
            pwm = output["pwm"] if output else None
            if output:
                steer = output.get("steer")
                state = output["state"]

            lat.append((time.monotonic() - t0) * 1000); n += 1
            rec = {"n": n, "t": time.time(), "kept": kept, "state": state,
                   **({"sweep": sweep.snapshot(), "ema": a.ema, "steer_offset": a.steer_offset} if sweep else {}),
                   "goal": [round(goal[0], 3), round(goal[1], 3)] if goal else None,
                   "kappa_raw": None if kappa_raw is None else round(kappa_raw, 4),
                   "kappa_cmd": None if kappa_ema is None else round(kappa_ema, 4),
                   "steer_i": round(steer_i, 4) if a.steer_i > 0 else None, "steer_i_why": steer_i_why if a.steer_i > 0 else None,
                   "steer_norm": None if steer is None else round(steer, 4), "pwm": pwm, "gain": gain, "look": look,
                   "cruise_cmd": cruise_cmd, "cruise_on": cruise_on, "cruise_why": cruise_why, "laps_n": round(laps_n, 3) if a.stop_after_laps else None,
                   **({"spd": spd_rec} if spd_rec is not None else {}),
                   # v15.5: written on the ONE frame a --cell-plan change lands, so the log says
                   # which knob moved and on which frame, rather than leaving a reader to infer it.
                   **({"cell_plan": cell_plan_applied} if cell_plan_applied is not None else {}),
                   "ms": round(lat[-1], 1), "danger_max_band": None if danger is None else round(float(danger[a.r0:].max()), 3), "trail": trail_score,
                   # v9: the DEGREE the line was actually fitted at. 3 = cubic, 2 = the cap knocked
                   # it down to a quadratic, 1 = it knocked it all the way to a STRAIGHT LINE, 0 =
                   # no line. Always logged, on every run, because the defect FINDINGS 39 records
                   # was invisible for three field days: a straight line through a bend looks like
                   # a confident answer in every other field of this record.
                   "fit_deg": fit.get("deg", 0)}
            if a.cap_kappa:
                rec["fit_kappa"] = None if fit.get("kappa") is None else round(float(fit["kappa"]), 3)
            if a.fit_max_y:                       # v11: how many rows actually voted (<= kept)
                rec["fit_vote"] = fit.get("vote", 0)
            if a.min_vote or a.goal_jump or a.lowrow_lock:   # v13: why a frame was held (None = it steered or v7 rows)
                rec["gate"] = gate
            if tpath is not None:                             # v15: written only with --path
                rec["path"] = path_rec
                if kappa_tot is not None: rec["kappa_cmd"] = round(kappa_tot, 4); rec["kappa_cam"] = None if kappa_ema is None else round(kappa_ema, 4)
            if relay is not None:                             # stage 4: its command as seen THIS frame, and whether it steered
                rec["relay"] = relay_rec; rec["kappa_src"] = kappa_src
            if recov is not None or heading_src is not None:  # the camera's own state survives the owner's overwrite of `state`
                rec["cam_state"] = cam_state
                if recov is not None: rec.update(recov.frame_record())
                else: rec["heading"] = heading_src.observe(now).log()
            if blue_rec is not None: rec["blue_gate"] = blue_rec       # independent of recovery/heading logging
            if motion is not None:                            # --log-motion: shadow only; computed after control, never read back
                try:
                    rec["motion"] = motion.update(t0, small, probs, PHONE["pts_us"],
                                                  motion_owner_view.from_snapshot(output).driving() if output else None)
                except Exception as exc:
                    rec["motion"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            if a.owner == "simple":                           # 2026-10-01: the one-switch owner's mode, for the feed page
                rec["owner"] = "simple"; rec["sa_mode"] = output.get("mode") if output else None
                if output: rec["rc3"] = tele.get("rc3") if tele else None; rec["rc5"] = tele.get("rc5") if tele else None
            if frames_udp is not None: rec["frames_out"] = frames_sent; rec["frames_dropped"] = frames_dropped
            if a.log_authority:                               # v15: who has the plant, in ONE field (round 4: five fields, one of them null outside the blend band)
                if fc is None: au = "no_fc"
                elif recov is not None and output:
                    au = ("recovery_stop" if output.get("throttle_source") == "recovery_stop_neutral" else
                          "path_recovery" if output.get("steer_source") == "path_recovery" else output["state"])
                elif output and output["state"] == "blend":
                    if output.get("override_on"): au = "wheel" if (wheel is not None and abs(wheel) > a.override_dz) else "handback"
                    else: au = "model"
                else: au = output["state"] if output else "release"
                rec["authority"] = au
                # Path Recovery steers but never owns the throttle (the speed dial or the trigger does); only its neutral latch is its own.
                rec["throttle_src"] = ("recovery_stop" if output and output.get("throttle_source") == "recovery_stop_neutral" else
                                       state if state == "cell_stop" else ("cruise" if cruise_on else ("reverse_scaled" if rev_cmd else "receiver")))
            # v8: the two shares of the command, logged separately so a pass can be attributed
            # afterwards. Written only when the feed-forward is enabled, so a --kff 0 run produces
            # a frame record byte for byte identical to v7's.
            if a.kff:
                rec.update(kappa_path=None if kappa_path is None else round(kappa_path, 4),
                           kappa_ff=round(kappa_ff, 4),
                           kappa_pp=None if kappa_pp is None else round(kappa_pp, 4),
                           kff_why=kff_why)
            if tele is not None: rec.update(tele)
            if blend_w is not None: rec.update(blend_w=round(blend_w, 3), wheel_norm=round(wheel, 4))
            if a.override and fwd is not None and state == "blend": rec["override"] = output.get("override_on")
            if a.cruise_relatch_fwd: rec["cruise_latch"] = brake_latched
            if a.reverse_scale < 1.0 and rev_cmd: rec["rev_cmd"] = rev_cmd
            if output is not None: rec.update(output=output, fwd_sends=output.get("sends", 0))
            # v12: the phone's clock and state, only when a phone is on the wire (a --video replay stays byte-identical to v11).
            if PHONE["pts_us"] is not None:
                rec["pts_us"] = PHONE["pts_us"]; rec["pts_rx_us"] = PHONE["pts_rx_us"]; rec["dec_depth"] = PHONE["dec_depth"]
                if PHONE["t_take"] is not None:          # async ingest: where the milliseconds go, per frame, one clock
                    tk = PHONE["t_take"]
                    rec["stage_ms"] = {"rx_dec": None if PHONE["t_rx"] is None else round((PHONE["t_dec"] - PHONE["t_rx"]) * 1000, 1),
                                       "dec_take": round((tk - PHONE["t_dec"]) * 1000, 1),
                                       "take_pub": round((t_pub - tk) * 1000, 1),
                                       "take_rec": round((time.monotonic() - tk) * 1000, 1)}
                    if PHONE["skipped"]: rec["skipped"] = PHONE["skipped"]
                if PHONE["dec_desync"]: rec["dec_desync"] = PHONE["dec_desync"]
                # capture -> this record, on the phone's clock: needs one (t_utc, t_us) pair from the pose and the clock offset
                pz = (PHONE["status"] or {}).get("pose") if PHONE["status"] else None
                if PHONE["clock_off"] is not None and isinstance(pz, dict) and isinstance(pz.get("t_utc"), (int, float)) and isinstance(pz.get("t_us"), (int, float)):
                    cap_utc = pz["t_utc"] + (PHONE["pts_us"] - pz["t_us"]) / 1e6
                    rec["age_ms"] = round(((time.time() + PHONE["clock_off"]) - cap_utc) * 1000.0, 1)
            if pitch_live: rec["pitch"] = round(pitch_f, 2)
            if PHONE["status"] and time.monotonic() - PHONE["status_t"] < 3.0:
                st = PHONE["status"]; rec["phone"] = {k: st.get(k) for k in PHONE_LOG_KEYS if k in st}
            if PHONE.get("video_config"):
                rec.setdefault("phone", {})["video_config"] = PHONE["video_config"]
            if PHONE["hello"] and not hello_logged:
                rec["hello"] = PHONE["hello"]; rec["clock_off"] = PHONE["clock_off"]; hello_logged = True
            LIVE_STATUS.update(frames=n, path_valid=bool(path_valid), path_reason=path_reason,
                               recording=rec.get("recording") or LIVE_STATUS["recording"])
            if log:
                # nothing below touches the disk: it all goes through the bounded writer (see LogWriter)
                if writer.dropped: rec["rec_dropped"] = writer.dropped
                if writer.qsize() > 32: rec["rec_q"] = writer.qsize()
                if writer.err: rec["rec_err"] = writer.err
                writer.put("line", json.dumps(rec) + "\n"); probs_all.append(probs_raw.astype(np.float16))
                if time.monotonic() - setup_last_observation >= 1.0:
                    writer.put("setup-observation", setup_capture.observe_dino_run, setup_snapshot, dict(rec), False)
                    setup_last_observation = time.monotonic()
                if not a.no_feed:
                    writer.put("jpg", Path(a.log) / "feed" / f"f{n:06d}.jpg", small, 92)
                    if a.feed_keep > 0 and n > a.feed_keep:          # rolling feed: the page needs the newest, not all
                        writer.put("rm", Path(a.log) / "feed" / f"f{n - a.feed_keep:06d}.jpg")
                if danger is not None: danger_all.append(danger.astype(np.float16))
                if n % a.jpg_every == 1: writer.put("jpg", Path(a.log) / f"f{n:06d}.jpg", cv2.resize(bgr, (960, 720)), 85)
                if len(probs_all) >= (15 if sweep else PROBS_CHUNK):      # field: a hard power-off must not lose the probs -- chunks survive it
                    writer.put("chunk", chunk_k, probs_all, danger_all); chunk_k += 1; probs_all = []; danger_all = []
            if n % 5 == 0 or state != "lane":
                g = f"goal ({goal[0]:+.2f},{goal[1]:.2f})m" if goal else "no goal"
                k = f"kappa {kappa_raw:+.2f}" if kappa_raw is not None else "kappa  ----"
                s = f"steer {steer:+.2f}" if steer is not None else "steer  ----"
                ff = ""
                if a.kff:
                    ff = ("  kpath %s ff %+.2f pp %s" %
                          ("----" if kappa_path is None else f"{kappa_path:+.2f}", kappa_ff,
                           "----" if kappa_pp is None else f"{kappa_pp:+.2f}")
                          + ("" if kff_why is None else f" ({kff_why})"))
                cru = ""
                if a.cruise:
                    cru = " thr %s %s" % (cruise_cmd if cruise_cmd is not None else "----",
                                          "ON" if cruise_on else cruise_why)
                    if spd_rec is not None:
                        cru += " sp %.2f v %s i %+.0f" % (spd_rec["sp"], "----" if spd_rec["v"] is None else "%.2f" % spd_rec["v"], spd_rec["i"])
                vb = "" if tele is None or tele.get("vbat") is None else " vbat %.2f" % tele["vbat"]     # v12.1
                rc = "" if tele is None or tele["rc1"] is None else f"  rc1 {tele['rc1']} wheel8 {tele.get('rc8')} sw3 {tele['rc3']} knob {tele.get('rc6')}{'' if blend_w is None else f' w={blend_w:.2f}'} srv1 {tele['servo1']}{vb}{f' gain {gain:.2f}' if a.gain_trim else ''}{f' look {look:.2f}' if a.look_trim else ''}{cru} {tele['mode']}{' ARMED' if tele['armed'] else ''}"
                fd = {0: "----", 1: "LINE", 2: "quad", 3: "cub"}[rec["fit_deg"]]     # v9
                if path_rec is not None:
                    ff += "  path %s ref %s d %s blind %.2f" % (path_rec["mode"], "----" if path_rec["ref"] is None else f"{path_rec['ref']:+.2f}",
                                                                 "----" if path_rec["dist"] is None else f"{path_rec['dist']:.2f}", path_rec["blind_m"])
                print(f"[{n:5d}] {state:7s} rows {kept:2d}/{NR} {fd:4s} {g:22s} {k}  {s}  pwm {pwm}"
                      + ff + f"  {np.mean(lat[-20:]):.0f} ms" + (f"  danger {rec['danger_max_band']:.2f}" if danger is not None else "") + (f"  trail {trail_score:.2f}" if trail_score is not None else "") + rc)
    except BaseException as exc:
        loop_error = exc
        raise
    finally:
        if fwd is not None:
            fwd.stop(); fwd.join(timeout=1.0)
            print("output owner stopped" if not fwd.is_alive() else "output owner did not exit within 1 s; inspect FC timeout")
        if log:
            from collections import Counter
            if recov is not None:                        # an active trial ends `interrupted`; a hard kill leaves it unterminated
                if loop_error is None:
                    recov.close()                        # detail controller_exit: a clean stop
                else:                                    # a crash is not a clean stop: name it in the trial_end detail
                    try:
                        recov.recovery.close(time.monotonic(), reason="controller_error:" + type(loop_error).__name__)
                    except Exception:
                        recov.recovery.trial = None
            writer.put("line", json.dumps({"event": "end", "t": time.time(), "n": n,
                       "wheel_sends": fwd.sends if fwd is not None else None, "wheel_hz_meas": None,
                       "ms_mean": round(float(np.mean(lat)), 1) if lat else None,
                       "ms_p99": round(float(np.percentile(lat, 99)), 1) if lat else None,
                       "rec_dropped": writer.dropped, "rec_chunks": chunk_k + (1 if probs_all else 0)}) + "\n")
            if probs_all: writer.put("chunk", chunk_k, probs_all, danger_all); chunk_k += 1
            writer.put("setup-observation", setup_capture.observe_dino_run, setup_snapshot, None, True)
            writer.close()
            if writer.dropped: print(f"WARNING: the recorder dropped {writer.dropped} items (queue full) -- the log has holes; see rec_dropped")
            nf, nc = merge_probs_chunks(a.log, NR, GW)
            print(f"logged {n} frames to {a.log} ({nf} probability frames from {nc} chunks)")
        if lat:
            print(f"\n{n} frames, {np.mean(lat):.1f} ms/frame mean, {np.percentile(lat, 95):.1f} p95")

if __name__ == "__main__":
    with launch_phase.managed_main():
        main()

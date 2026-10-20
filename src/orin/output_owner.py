# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""One actuator writer, independently ticked; perception publishes expiring intent.

No vehicle connections on import. `tick` is the same decision/send path used by
the thread and the command-level bench. A new object ALWAYS starts inhibited.
Start gesture: neutral trigger, HOLD + bottom dial, then raise dial in HOLD and
leave HOLD. Every terminal withdrawal requires that gesture again; an explicitly
configured finite batch may advance normal cells after confirmed rest. Human driving
remains available while inhibited. This is a software guard, not a physical brake.
"""
from dataclasses import dataclass
import math
import threading
import time
from recovery import Recovery, NoMotionDetector
from batch import Batch


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def channel(value):
    return finite(value) and 900 <= value <= 2100


@dataclass(frozen=True)
class Intent:
    epoch: int
    created: float                 # frame START, not publication after inference
    steer: object
    throttle: object
    dial: object
    stop_reason: object = None
    cell: object = None
    cell_done: bool = False
    steer_source: object = None  # observational source label, not external-planner authority
    throttle_cap: object = None  # recovery cap; never raises the speed dial's throttle


@dataclass(frozen=True)
class Pose:
    seq: object
    stamp: float                   # source timestamp, for advancing-sample checks
    received: float                # monotonic receive time
    age_s: float                   # source age measured ON RECEIPT
    x: float
    z: float
    speed: object
    tracking: object
    recording: object
    heading: object = None         # radians, BODY forward in the X/Z floor plane


class Standstill:
    """Pose evidence only, never an assertion of independently measured rest.

    Proposed bench thresholds: >=0.5 s of advancing poses, speed <=0.03 m/s,
    positions within 0.01 m of the first sample. These need truck validation.
    A repeated/frozen sample cannot complete the dwell; a gap resets evidence.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.first = self.last = None
        self.confirmed = False

    def update(self, pose, valid, max_gap):
        if not valid:
            self.reset()
            return False
        if self.last is not None:
            if pose == self.last:
                return self.confirmed
            if (pose.recording != self.last.recording or pose.stamp <= self.last.stamp
                    or pose.seq == self.last.seq or pose.stamp - self.last.stamp > max_gap):
                self.reset()
        if abs(pose.speed) > 0.03:
            self.reset()
            self.last = pose
            return False
        if self.first is None or math.hypot(pose.x - self.first.x, pose.z - self.first.z) > 0.01:
            self.first = pose
            self.confirmed = False
        self.last = pose
        self.confirmed = pose.stamp - self.first.stamp >= 0.5
        return self.confirmed


class OutputOwner(threading.Thread):
    # How long every condition must hold before an ARMED start is granted, so the start
    # cannot fire into a still-warming frame loop (look-1854, look-1856).
    ARMED_STEADY_S = 2.0
    # What counts as "the truck moved" in a calibration trial. 3 cm is comfortably above the
    # measured stationary pose jitter on this vehicle (0.4-7.4 mm median path jitter while
    # driving, FINDINGS 2026-09-12) and far above the 0.7 mm and 6.0 mm the two loaded attempts
    # of 2026-09-14 produced. It is an ONSET threshold, not a clearance: first movement alone
    # does not demonstrate that the truck is free.
    MOVED_M = .03
    # Terminal reasons that mean THE INSTRUMENTS FAILED, not that the truck did not move.
    # Kept in step with recovery.py's `_terminal(...)` calls; test_reverse_trial.py asserts
    # that every reason recovery can emit is classified here or is a genuine bound.
    EVIDENCE_FAULTS = frozenset((
        "clock_invalid", "recording_changed",
        "pose_missing", "pose_stale", "pose_not_advancing", "pose_gap", "pose_jump",
        "pose_duplicate_changed", "pose_invalid",
        "servo_stale", "servo_not_advancing", "servo_sequence_regressed",
        "servo_out_of_range"))

    def __init__(self, fc, args, sending, cruise_decide, brake_update,
                 override_weight, pose_source, receipt=None, rc_max_age_ms=250,
                 min_band=20, absolute_max=1800):
        super().__init__(daemon=True)
        self.fc, self.a, self.sending = fc, args, sending
        self.decide, self.brake_update, self.weight = cruise_decide, brake_update, override_weight
        self.pose_source, self.receipt = pose_source, receipt
        self.rc_max_age_ms, self.min_band, self.absolute_max = rc_max_age_ms, min_band, absolute_max
        self.period = 1.0 / max(1.0, args.wheel_hz)
        self.enabled = False             # physical wheel copy verified before startup
        self._lock = threading.Lock()
        self._intent = None
        self._terminal_request = None
        self._cell_done = None
        self._quit = threading.Event()
        self.epoch = 0
        self.active = self.ready = self.previous_hold = False
        self.pending_start = None       # bounded wait for the FC's mode heartbeat after the mode switch
        self.brake_latched = False
        self.reason = "startup"
        self.recording = None
        self.last_touch = -1e9
        self.rest = Standstill()
        self.sends = 0
        self.ticks = 0                   # advancing ticks: a stopped sender is not a quiet one
        self.thread_error = None         # the sender died HERE, and status must say so
        self._last_event = None
        config = getattr(args, "recovery_config", None)
        self.recovery = Recovery(config, fc.recovery_profile) if config is not None else None
        self.no_motion = (NoMotionDetector(window_s=config.no_motion_window_s)
                          if config is not None else None)
        # --start-armed (operator's ruling at the truck, 2026-09-14 18:26: "we are implementing a
        # system where you can start the vehicle yourself ... the killswitch and the mode switch are
        # good enough for safety"). ONE SHOT PER PROCESS: spent the instant a start is granted, so
        # a later veto can never silently re-start the vehicle. The anti-inherit property of
        # FINDINGS 70 survives because arming is now an explicit per-launch decision, recorded in
        # the run spec, instead of something a left-up dial can confer by accident.
        self.start_armed = bool(getattr(args, "start_armed", False))
        # An armed start must not fire into a cold frame loop. Runs look-1854 and look-1856 both
        # died at t+2.4 s: the owner granted the start on the first valid intent, the still-warming
        # vision loop then missed its next publish, and `intent_expired` aborted the whole batch
        # before it had driven a metre. The radio gesture never hit this because performing it took
        # long enough for the loop to settle.
        self._armed_steady = None
        self.recovering = False
        self.recovery_attempts = 0
        # v15.6 Phase 1B. A forward-only refusal holds the throttle at neutral and KEEPS
        # permission, so an already-standing stall can still admit a recovery. It is bounded:
        # after this many seconds the run is withdrawn like any other refusal. Default 4.0 s,
        # just over the 3.5 s no-motion window, so a stall that began before the line was lost
        # can still complete its evidence. THIS CHANGES FORWARD BEHAVIOUR: losing the line used
        # to withdraw permission on the same tick; it now holds at neutral for up to 4 s and
        # resumes if the line comes back. Not driven.
        self.forward_block_max_s = float(getattr(args, "forward_block_max_s", 4.0))
        if not finite(self.forward_block_max_s) or self.forward_block_max_s < 0:
            raise ValueError("forward_block_max_s must be finite and >= 0")
        self.forward_blocked_since = None
        # QUALIFICATION (external review 2026-09-14, finding 2 — it broke the claim this was
        # built to make). Permission survives a line loss ONLY while stall evidence is qualified
        # by DEMONSTRATED FORWARD MOTION in this segment. Without it, `no_motion.first` is already
        # set at the forward start -- it is seeded from a pose taken BEFORE the first command --
        # so a truck that never moved at all entered recovery after 3.5 s of missing steering.
        # Reproduced at tick 69 of the project's own bench. Before demonstrated motion, a line
        # loss is an ambiguous FAILED START, not a collision, and stays terminal.
        self._motion_demonstrated = False
        self._segment_origin = None
        # 🔴 Defect 3: the recovery amplitude and speed limits are PROVISIONAL and were never
        # justified by a physical measurement -- max_speed_mps was raised because a SIMULATED
        # retreat tripped the old bound. A comment is not a gate. Autonomous recovery is not
        # admitted unless this is explicitly true, and nothing sets it true except a human who
        # has read a characterisation. Deliberate supervised calibration has its own admission
        # and does not need this.
        self.reverse_ready = bool(getattr(args, "reverse_ready", False))
        self.reverse_profile_id = getattr(args, "reverse_profile_id", None)
        # Phase 3: whether a COMPLETED recovery may hand back to forward driving on its own.
        # The review is explicit that "any automatic return to forward driving has a separately
        # accepted transition", so the default is that it does not: a completed recovery ends
        # the run in a stopped state and a human decides what happens next. Freeing the truck
        # and continuing the experiment are two different claims.
        self.resume_after_recovery = bool(getattr(args, "resume_after_recovery", False))
        # v15.6 Phase 2: DELIBERATE SUPERVISED CALIBRATION, admitted separately from forward
        # driving. The external review's point is that a calibration trial "does not need to
        # pretend its experimental profile is already accepted": it has its own admission, its
        # own bounds, and it never fabricates a forward intent. In this mode the owner grants NO
        # forward start at all -- the only thing it will ever command is one bounded, centred
        # reverse waveform, once, on an explicit request, from confirmed rest.
        self.calibration_only = bool(getattr(args, "reverse_trial", False))
        self._trial_request = None      # mailbox: one pending request, consumed on admission
        self.calibrating = False
        self.trial = None               # the admitted trial's record
        self.trials_done = 0
        # The row is written only after the OUTPUT IS SELECTED AND SENT, because the recovery's
        # proposed throttle is not always what leaves the process. `_end_trial` withdraws
        # permission before the send, so a neutral decision on the terminal tick was recorded as
        # requested 1500 while FC.send() actually got 0 -- an override RELEASE, not a neutral
        # command. A ledger that reports a command nobody sent is worse than no ledger.
        self._pending_sample = None
        self.motion_epoch = 0          # forward segment; PI/laps cannot bridge a recovery
        self._source_pose = None       # original receive age survives duplicate packets
        self._cell_stop_steer = None   # last sent steering, retained only through normal coasting
        self.batch = (Batch(args.batch_cells, args.cell_max_s, args.batch_max_s,
                            cell_seconds=getattr(args, "cell_seconds", None))
                      if getattr(args, "batch_cells", 0) else None)
        if self.batch is not None and self.recovery is None:
            raise ValueError("batch requires validated stall recovery configuration")
        self._status = dict(epoch=0, cruise_on=False, cruise_why="startup",
                            stop_state="unknown", pwm=0, throttle=0, state="init")

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def request_trial(self, trial):
        """Queue ONE supervised calibration trial. -> (accepted, why).

        Queuing is not admission: the owner admits it only from confirmed rest with every hard
        stop clear, and refuses outright if a trial is already running. Nothing here can move
        the vehicle; the sender decides, on its own thread, as it does for everything else.
        """
        with self._lock:
            if not self.calibration_only:
                return False, "this process was not launched with --reverse-trial"
            if self.calibrating or self.active:
                return False, "a trial is already running"
            if self._trial_request is not None:
                return False, "a request is already queued"
            self._trial_request = dict(trial)
            return True, "queued"

    def trial_state(self):
        with self._lock:
            return None if self.trial is None else dict(self.trial)

    def publish(self, intent):
        with self._lock:
            if intent.epoch != self.epoch:
                return False
            # A stop request cannot disappear just because another frame lands
            # before the next sender tick. Only the sender consumes this mailbox.
            if intent.stop_reason and self._terminal_request is None:
                self._terminal_request = str(intent.stop_reason)
            if self.batch is not None and intent.cell != self.batch.index:
                return False
            if self.batch is not None and intent.cell_done and self.active and self.batch.phase == "driving":
                self._cell_done = intent.cell
            if self._intent is not None and intent.created <= self._intent.created:
                return bool(intent.stop_reason)
            self._intent = intent       # one immutable snapshot, no torn steer/time pair
            return True

    def stop(self):
        self._quit.set()

    def _withdraw(self, reason):
        self._cell_stop_steer = None
        if self.active:
            with self._lock:
                self.epoch += 1
                self._intent = None
                self._terminal_request = None
                self._cell_done = None
            self.rest.reset()
            self.active = False
            self.ready = False
            self.pending_start = None
            self._motion_demonstrated = False
            self._segment_origin = None
            self.reason = reason
            self.recovering = False
            if self.no_motion is not None:
                self.no_motion.reset()
            if self.batch is not None:
                self.batch.abort(reason)

    def _stall_qualified(self):
        """May standing stall evidence survive a line loss? Only after demonstrated motion.

        `no_motion.first is not None` alone is NOT qualification: it is set on the first
        stationary sample of a forward segment, including the one taken before any command was
        sent. Qualification additionally requires that the truck actually moved in this segment.
        """
        return (self.no_motion is not None and self.no_motion.first is not None
                and self._motion_demonstrated)

    def _record_trial_sample(self, now, decision, pose, tele,
                             sent_throttle=None, sent=None, released=None):
        """One row per tick of a running trial. REQUESTED and REPORTED are separate columns.

        `servo3` is the FIRMWARE'S OUTPUT REPORT and its age here is a DEQUEUE age: a recent
        dequeue does not make an old packet fresh, and this rig has no validated mapping from
        the packet's source clock to ours. So the source age is recorded as unknown rather than
        guessed, and any claim about delivery timing must say so.
        """
        if self.trial is None or not self.trial.get("admitted"):
            return
        rows = self.trial["commands"]
        if len(rows) < 4000:                       # bounded: a trial is seconds, not minutes
            rows.append(dict(t=round(now - self.trial["at"], 4), phase=decision.phase,
                             # THREE different facts, kept apart:
                             #   proposed_rc2   what the recovery sequence asked for
                             #   requested_rc2  what this process actually sent on the channel
                             #   released       the override was released (0), NOT a neutral command
                             proposed_rc2=decision.throttle,
                             requested_rc2=sent_throttle if sent_throttle is not None else decision.throttle,
                             sent=sent, released=released,
                             reported_servo3=tele.get("servo3"),
                             servo3_seq=tele.get("servo3_seq"),
                             servo3_recv_age_ms=tele.get("servo3_age_ms"),
                             servo3_source_age_ms=None,
                             x=pose.x if pose is not None else None,
                             z=pose.z if pose is not None else None,
                             speed=pose.speed if pose is not None else None,
                             pose_seq=pose.seq if pose is not None else None,
                             pose_stamp=pose.stamp if pose is not None else None))
        if not self.trial["phases"] or self.trial["phases"][-1]["phase"] != decision.phase:
            self.trial["phases"].append(dict(phase=decision.phase, t=round(now - self.trial["at"], 4)))

    def _end_trial(self, kind, reason, now, pose, tele):
        """Close the trial, withdraw permission, and classify the outcome HONESTLY.

        The vocabulary is fixed (external review): moved / wheelspin_observed /
        no_motion_within_bounds / vetoed / indeterminate. `wheelspin_observed` is NEVER produced
        here -- the phone's pose cannot distinguish a stalled drivetrain from spinning wheels, so
        only the human observer can supply it, afterwards. Missing or unusable evidence gives
        `indeterminate`, not a guess, and never a reason to try a larger pulse.
        """
        trial = self.trial
        self._withdraw(reason)
        self.calibrating = False
        self.trials_done += 1
        if trial is None:
            return
        m = dict(self.recovery.metrics) if self.recovery is not None else {}
        origin = trial.get("origin") or {}
        net = None
        if pose is not None and finite(origin.get("x")) and finite(origin.get("z")):
            net = math.hypot(pose.x - origin["x"], pose.z - origin["z"])
        # DIRECTION MATTERS. `net` is the straight-line distance from the origin and counts
        # lateral slew or a forward lurch as a successful reverse; the external review flagged
        # that a limit-terminated attempt could be classified `moved` on it. Only measured
        # BACKWARD travel qualifies, and the direction and terminal reason ride with the result
        # so acceptance can see both.
        moved = m.get("backwards_m", 0.0) >= self.MOVED_M
        # EVIDENCE FAULTS, enumerated against recovery.py's actual terminal vocabulary rather
        # than guessed at. A trial that ended because we could not SEE what happened did not
        # measure "no motion"; it measured nothing, and must say so.
        evidence_bad = kind == "terminal" and str(reason).split(":")[0] in self.EVIDENCE_FAULTS
        if kind == "vetoed":
            outcome = "vetoed"
        elif evidence_bad:
            outcome = "indeterminate"
        elif moved:
            outcome = "moved"
        elif kind in ("completed", "terminal"):
            outcome = "no_motion_within_bounds"
        else:
            outcome = "indeterminate"
        trial.update(outcome=outcome, reason=reason, kind=kind, ended_at=now,
                     moved_backwards_m=m.get("backwards_m"),
                     moved_criterion=f"backwards_m >= {self.MOVED_M} (direction-checked; net "
                                     f"displacement is recorded but NEVER used to classify)",
                     ended_wall=time.time(), duration_s=round(now - trial["at"], 3),
                     metrics=m, net_displacement_m=net,
                     final=dict(x=pose.x if pose is not None else None,
                                z=pose.z if pose is not None else None,
                                speed=pose.speed if pose is not None else None,
                                servo3=tele.get("servo3")),
                     wheel_observation=None)     # the human supplies this; never inferred here
        self.trial = trial
        if self.receipt is not None:
            self.receipt(dict(event="trial_end", t=time.time(),
                              **{k: v for k, v in trial.items() if k != "config"}))

    def tick(self, now=None):
        tele = self.fc.poll()
        pose = self.pose_source()
        with self._lock:
            intent = self._intent
            terminal = self._terminal_request
            cell_done = self._cell_done
        # Inputs can arrive while poll() runs. Read the clock AFTER the inputs;
        # otherwise a perfectly fresh pose can look as though it is in the future.
        now = time.monotonic() if now is None else now
        a = self.a
        neutral, dz = self.fc.thr_neutral, max(self.fc.thr_dz, self.min_band)
        rc_age, hb_age = tele.get("rc_age_ms"), tele.get("hb_age_ms")
        rc_ok = finite(rc_age) and 0 <= rc_age <= self.rc_max_age_ms
        hb_ok = finite(hb_age) and 0 <= hb_age <= 2000
        sw, stick, dial, kill = (tele.get(k) for k in ("rc3", "rc2_stick", "cruise_pwm", "rc5"))
        hold = channel(sw) and sw >= 1750
        middle = channel(sw) and 1361 <= sw <= 1620
        model_end = channel(sw) and sw < 1361
        switches_ok = hold or middle or model_end
        neutral_stick = channel(stick) and abs(stick - neutral) <= dz
        radio_ok = rc_ok and hb_ok and switches_ok and channel(kill) and kill < 1800
        drive_ok = radio_ok and tele.get("mode") == "MANUAL" and bool(tele.get("armed"))
        if rc_ok and channel(stick) and a.cruise_relatch_fwd:
            self.brake_latched = self.brake_update(self.brake_latched, stick, hold, neutral, dz)

        # Preserve the exact source presented to this sender tick. Frame-rate
        # logs can show a newer valid sample after a terminal pose veto; they
        # cannot explain that earlier decision without this evidence.
        prior_source = self._source_pose
        pose_evidence = dict(
            seq=getattr(pose, "seq", None), stamp=getattr(pose, "stamp", None),
            received=getattr(pose, "received", None), age_s=getattr(pose, "age_s", None),
            tracking=getattr(pose, "tracking", None), recording=getattr(pose, "recording", None),
            previous_seq=getattr(prior_source, "seq", None),
            previous_stamp=getattr(prior_source, "stamp", None),
            effective_age_s=(max(0.0, pose.age_s) + now - pose.received
                             if pose is not None and finite(pose.age_s) and finite(pose.received) else None),
            limit_s=a.pose_max_age_ms / 1000.0,
            recovery_limit_s=self.recovery.c.pose_max_age_s if self.recovery is not None else None,
            source_gap_s=(pose.stamp - prior_source.stamp
                          if pose is not None and prior_source is not None
                          and finite(pose.stamp) and finite(prior_source.stamp) else None),
            duplicate_fields_match=(all(getattr(pose, k) == getattr(prior_source, k)
                                        for k in ("x", "z", "speed", "heading"))
                                    if pose is not None and prior_source is not None
                                    and pose.seq == prior_source.seq and pose.stamp == prior_source.stamp else None))
        pose_ok = (pose is not None and pose.tracking == "normal" and pose.seq is not None
                   and all(finite(v) for v in (pose.stamp, pose.received, pose.age_s, pose.x, pose.z, pose.speed))
                   and pose.age_s >= -0.02 and now >= pose.received
                   and max(0.0, pose.age_s) + now - pose.received <= a.pose_max_age_ms / 1000.0)
        if self.recovery is not None:
            pose_ok = (pose_ok and finite(pose.seq) and finite(pose.heading) and bool(pose.recording)
                       and max(0.0, pose.age_s) + now - pose.received <= self.recovery.c.pose_max_age_s)
            if pose_ok:
                previous = self._source_pose
                if previous is not None and previous.recording == pose.recording:
                    if pose.seq == previous.seq and pose.stamp == previous.stamp:
                        pose_ok = (all(getattr(pose, k) == getattr(previous, k) for k in ("x", "z", "speed", "heading"))
                                   and max(0.0, previous.age_s) + now - previous.received <= self.recovery.c.pose_max_age_s)
                    elif pose.seq <= previous.seq or pose.stamp <= previous.stamp:
                        pose_ok = False
                    else:
                        if self.active and pose.stamp - previous.stamp > self.recovery.c.pose_max_age_s:
                            pose_ok = False
                        self._source_pose = pose
                else:
                    self._source_pose = pose
        stopped = self.rest.update(pose, pose_ok, a.pose_max_age_ms / 1000.0)
        # Demonstrated motion for this forward segment. DISPLACEMENT, not one speed sample:
        # a single noisy reading must never be able to qualify a reverse. The origin is reset
        # with the motion epoch, so a recovery cannot inherit the previous segment's proof.
        if not self.active or not pose_ok:
            self._segment_origin = None
        elif self._segment_origin is None:
            self._segment_origin = pose
        elif (not self._motion_demonstrated
              and math.hypot(pose.x - self._segment_origin.x, pose.z - self._segment_origin.z)
              >= self.MOVED_M):
            self._motion_demonstrated = True
        fresh = (intent is not None and intent.epoch == self.epoch
                 and finite(intent.created) and 0 <= now - intent.created <= a.hold_ms / 1000.0)
        steer_ok = fresh and (intent.steer is None or finite(intent.steer))
        # ------------------------------------------------------------------ admission, v15.6
        # TWO CLASSES OF REFUSAL, evaluated independently (external review 2026-09-14, Phase 1B;
        # the operator found the defect from the driver's seat first: "If you could see the blue tape you
        # wouldn't need to reverse").
        #
        # HARD STOPS end the run whatever it was doing: the radio, the human, the mode, the
        # arming, a dead or invalid producer, a lost recording, a terminal request. A `hard` stop
        # is a hard stop for FORWARD AND FOR RECOVERY ALIKE.
        #
        # FORWARD-ONLY refusals say the vehicle must not be driven FORWARD on this tick. They are
        # NOT reasons a bounded, centred, already-admitted recovery pulse cannot finish. Recovery
        # supplies its own centred steering, so forward-steering eligibility was never the right
        # prerequisite for it. Run corner-1807 vetoed `no_steering` on 600 of 600 frames and
        # cruise never engaged, so the stall recovery could not fire in the one case it exists for.
        #
        # The old single chain also let `no_steering` MASK an earlier refusal from cruise_decide,
        # and folded two different facts together: a healthy producer reporting no usable line
        # (forward-only) and a nonfinite number (a fault, and hard).
        level, decide_veto = self.decide(a, tele, hold, neutral, self.fc.thr_dz, self.brake_latched)
        steer_nonfinite = fresh and intent.steer is not None and not finite(intent.steer)
        hard = None
        if not rc_ok: hard = "rc_stale"
        elif not hb_ok: hard = "heartbeat_stale"
        elif not switches_ok: hard = "switch_invalid"
        elif not channel(kill) or kill >= 1800: hard = "kill"
        elif not channel(stick): hard = "no_ch10"
        elif hold: hard = "hold_sw"
        elif middle and (not self.enabled or not channel(tele.get("rc8")) or not channel(tele.get("rc6"))): hard = "wheel_unavailable"
        elif not fresh: hard = "intent_expired"
        elif steer_nonfinite: hard = "steer_nonfinite"
        # A calibration process runs with forward cruise OFF, so its intents carry no throttle at
        # all. That is not a fault there: the recovery sequence owns the throttle outright and
        # perception's number is never consulted.
        elif not self.calibration_only and not finite(intent.throttle): hard = "invalid_throttle"
        # Perception asking for REVERSE is a fault, not a routine refusal: the forward controller
        # has no reverse authority at all, and every legitimate negative command comes from the
        # recovery sequence, which owns the throttle outright while it runs.
        elif not self.calibration_only and intent.throttle < neutral - dz: hard = "reverse_intent"
        elif a.cruise_trim and intent.dial != dial: hard = "dial_changed"
        elif not pose_ok: hard = "pose_invalid"
        elif self.active and pose.recording != self.recording: hard = "recording_changed"
        elif terminal: hard = terminal
        # Every cruise_decide reason is a hard stop: they are the radio, the human's trigger, the
        # brake latch, the dial at its stop, the mode and the arming. None of them means "forward
        # only". The operator winding the dial down must stop a recovery too.
        elif decide_veto is not None and not self.calibration_only: hard = decide_veto
        # A supervised trial commands no forward speed, so the cruise dial's position and the
        # cruise configuration are irrelevant to it. The SAFETY half of those conditions is
        # required explicitly and identically: armed, MANUAL, fresh radio, kill released
        # (all in drive_ok / above), the mode switch out of HOLD (`hold`, above), trigger at neutral.
        elif self.calibration_only and not drive_ok: hard = "not_drive_ok"
        elif self.calibration_only and not neutral_stick: hard = "human_trigger"
        if self.batch is not None and hard is None:
            if self.batch.phase in ("finished", "terminal"):
                hard = "batch_" + self.batch.phase
            elif intent.cell != self.batch.index and self.batch.phase != "await_frame":
                hard = "cell_mismatch"
        # The human's wheel and the brake latch cancel a CALIBRATION TRIAL exactly as they cancel
        # a recovery (external review, finding 3: an offline trial with the wheel copy at 1800 drove into primer
        # with reverse throttle, because calibration exempted the cruise decision wholesale and the
        # wheel check named only recovery and batch). Calibration may skip forward-cruise
        # ENABLEMENT; it must never thereby skip an operator veto.
        if self.calibration_only and hard is None and self.brake_latched:
            hard = "post_brake"
        if (self.recovering or self.calibrating or (self.calibration_only and self._trial_request is not None)
                or (self.batch is not None and self.active)) and hard is None:
            if not channel(tele.get("rc8")) or abs(self.fc.steer_from_pwm(tele["rc8"])) > a.override_dz:
                hard = "recovery_wheel"
            elif middle and (a.manual_knob > 0 and tele.get("rc6", 2000) <= a.manual_knob):
                hard = "recovery_manual"

        # Forward-only. Ordered after the hard stops so it can never mask one.
        forward_veto = None
        if hard is None:
            # In a calibration process forward is not merely unavailable this tick -- it does not
            # exist. Saying so here means the throttle branch below can never fall through to a
            # forward command, whatever perception publishes.
            if self.calibration_only: forward_veto = "calibration_only"
            elif not steer_ok or intent.steer is None: forward_veto = "no_steering"
            # At/near neutral: the producer is asking for no forward drive. Not a fault.
            elif intent.throttle <= neutral + dz: forward_veto = "no_drive_intent"
        veto = hard or forward_veto

        if self.active and hard:
            self._withdraw(hard)
            fresh = False               # old steering intent belongs to the cancelled epoch
            stopped = False             # evidence must follow the stop request
            self.forward_blocked_since = None
        elif (self.active and forward_veto and not (self.calibrating or self.recovering)
              and not self._stall_qualified()):
            # ORDINARY, UNQUALIFIED LINE LOSS IS TERMINAL -- the default this had before
            # calibration was added, restored on the reviewer's instruction: "do not make a new
            # blind-wait/resume policy the default as a side effect of adding calibration".
            self._withdraw(forward_veto)
            fresh = stopped = False
            self.forward_blocked_since = None
        elif self.active and forward_veto and not (self.calibrating or self.recovering):
            # Permission is RETAINED and forward throttle is not commanded. Bounded: a run cannot
            # sit here indefinitely holding authority it is not using. A reverse sequence already
            # in progress is exempt -- it supplies its own centred steering and has its own
            # shorter bounds, and cutting it off mid-pulse would leave the truck powered.
            if self.forward_block_max_s <= 0:
                # Zero means ZERO. It used to retain permission for the first blocked tick,
                # because the elapsed check could not fire on the tick that started the clock.
                self._withdraw(forward_veto)
                fresh = stopped = False
                self.forward_blocked_since = None
            elif self.forward_blocked_since is None:
                self.forward_blocked_since = now
            elif now - self.forward_blocked_since > self.forward_block_max_s:
                self._withdraw(forward_veto + "_timeout")
                fresh = stopped = False
                self.forward_blocked_since = None
        else:
            self.forward_blocked_since = None

        # A startup/restart cannot inherit permission from a raised dial or an
        # old frame. The gesture is observed by THIS sender, after its start.
        # In calibration-only mode NO forward start is ever granted, by any gesture: the start
        # gesture, --start-armed and the pending-start wait are all skipped below. The only
        # permission this process can obtain is one supervised reverse trial.
        safe_ready = (not self.calibration_only and radio_ok and bool(tele.get("armed")) and hold and neutral_stick
                      and (not a.cruise_trim or (channel(dial) and dial <= 1100)) and stopped)
        if not self.active and safe_ready:
            self.ready = True
        if not radio_ok or not neutral_stick:
            self.ready = False
        # An armed launch substitutes for the dial-down proof and the HOLD transition. Every
        # other condition is unchanged: radio fresh, armed, trigger neutral, truck at rest, and
        # The mode switch already out of HOLD, which remains the human's enable.
        if (not self.calibration_only and not self.active and self.start_armed and self.pending_start is None and not hold
                and radio_ok and bool(tele.get("armed")) and neutral_stick and stopped
                and veto is None):
            if self._armed_steady is None:
                self._armed_steady = now
            if now - self._armed_steady >= self.ARMED_STEADY_S:
                self.pending_start = (now + 1.5, pose.recording if pose else None, dial)
        elif not self.active and self.start_armed:
            self._armed_steady = None
        if not self.calibration_only and not self.active and self.ready and self.previous_hold and not hold:
            self.ready = False
            # RC_CHANNELS reports the mode switch before HEARTBEAT reports MANUAL. Do not
            # consume a valid start solely because the mode packet trails it.
            # Every other veto cancels; no later frame/restart can recreate it.
            self.pending_start = (now + 1.5, pose.recording if pose else None, dial)
        if not self.active and self.pending_start is not None:
            until, recording, start_dial = self.pending_start
            waiting_for_mode = veto == "mode" and tele.get("mode") == "HOLD"
            if (now > until or hold or not radio_ok or not neutral_stick or not tele.get("armed")
                    or not pose_ok or not stopped or pose.recording != recording
                    or dial != start_dial or (veto is not None and not waiting_for_mode)):
                self.pending_start = None
            elif a.cruise and drive_ok and veto is None:
                self.pending_start = None
                self.active = True
                self.start_armed = False        # one shot: spent on the start it granted
                self.reason = None
                self.recording = pose.recording
                self.recovery_attempts = 0
                if self.recovery is not None:
                    self.recovery.reset()
                    self.no_motion.reset()
                if self.batch is not None:
                    self.batch.start(now)
        self.previous_hold = hold if rc_ok else False

        # Recovery retains this explicit start's permission but owns every
        # intermediate throttle value. Perception cannot fill a neutral gap.
        recovery_cmd = None
        batch_pause = False
        if self.active and self.batch is not None:
            if (cell_done == self.batch.index or self.batch.timed_cell_done(now)) and not self.recovering:
                if self.batch.stop_cell(now):
                    prior = self._status
                    self._cell_stop_steer = (prior.get("steer")
                        if prior.get("sending") and prior.get("autonomous_permission")
                        and prior.get("epoch") == self.epoch
                        and prior.get("batch_cell") == self.batch.index
                        and prior.get("batch_phase") == "driving"
                        and finite(prior.get("steer")) else None)
                    self.rest.reset()
                    stopped = False
                    self.no_motion.reset()
                    self.motion_epoch += 1
                    self._motion_demonstrated = False
                    self._segment_origin = None
                with self._lock:
                    self._cell_done = None
            batch_pause = self.batch.paused
            error = self.batch.update(now, stopped, tele, self.fc.recovery_profile.servo_neutral, intent)
            if self.batch.phase != "stopping":
                self._cell_stop_steer = None
            batch_pause = batch_pause or self.batch.paused
            if error or self.batch.phase == "finished":
                self._withdraw(error or "batch_complete")
                fresh = False
                if error:
                    stopped = False
            if batch_pause:
                recovery_cmd = neutral
        # ---------------------------------------------------------- supervised trial, v15.6
        if self.calibration_only and self.recovery is not None:
            with self._lock:
                request = self._trial_request
            if not self.calibrating and request is not None:
                # ADMISSION. Every hard stop clear, pose valid, and rest CONFIRMED FROM POSE --
                # not merely "no command was sent". A refused request is reported with its reason
                # and dropped, never retried silently, so the operator always knows why nothing
                # happened. The configuration is exercised HERE, including the amplitude mapping:
                # an unsupported value must be a refusal at admission, not a ValueError on the
                # first tick that kills the sender before any trial result exists.
                with self._lock:
                    self._trial_request = None
                refusal = (hard if hard is not None
                           else "pose_invalid" if not pose_ok
                           else "not_at_rest" if not stopped
                           else "recording_missing" if not pose.recording
                           else None)
                config = None
                if refusal is None:
                    try:
                        config = Recovery(request["config"], self.fc.recovery_profile)
                        self.fc.recovery_profile.input_for_fraction(request["config"].max_fraction)
                    except (ValueError, TypeError, AttributeError, KeyError) as exc:
                        config, refusal = None, f"unsupported configuration: {exc}"
                if refusal is not None:
                    self.trial = dict(request, admitted=False, outcome="vetoed", reason=refusal,
                                      at=now, wall=time.time())
                    if self.receipt is not None:
                        self.receipt(dict(event="trial_refused", t=time.time(),
                                          **{k: v for k, v in self.trial.items() if k != "config"}))
                else:
                    self.recovery = config
                    self.calibrating = True
                    self.active = True                  # permission for THIS TRIAL ONLY
                    self.reason = None
                    self.recording = pose.recording
                    self.motion_epoch += 1
                    self._motion_demonstrated = False
                    self._segment_origin = None
                    self.rest.reset()
                    stopped = False
                    self.no_motion.reset()
                    self.trial = dict(request, admitted=True, outcome=None, reason=None,
                                      at=now, wall=time.time(), recording=pose.recording,
                                      origin=dict(x=pose.x, z=pose.z, heading=pose.heading,
                                                  seq=pose.seq, stamp=pose.stamp),
                                      servo_at_start=tele.get("servo3"),
                                      commands=[], phases=[])
                    self.recovery.start(now, pose)
                    if self.receipt is not None:
                        self.receipt(dict(event="trial_start", t=time.time(),
                                          **{k: v for k, v in self.trial.items() if k != "config"}))
            if self.calibrating:
                if hard is not None:
                    self._end_trial("vetoed", hard, now, pose, tele)
                    fresh = stopped = False
                else:
                    decision = self.recovery.update(now, pose, tele, intent.created if intent else now)
                    recovery_cmd = decision.throttle
                    self._pending_sample = (now, decision, pose, tele)
                    if decision.terminal_reason:
                        self._end_trial("terminal", decision.terminal_reason, now, pose, tele)
                        fresh = stopped = False
                    elif decision.done:
                        self._end_trial("completed", "sequence_done", now, pose, tele)
                        fresh = stopped = False
                    if not self.calibrating:
                        recovery_cmd = neutral
        elif self.active and self.recovery is not None and not batch_pause:
            # WHAT COUNTS AS A COMMANDED NONPROGRESS (v15.6 Phase 1B). Evidence may only START
            # while forward is actually being commanded, and once standing it is not thrown away
            # merely because the camera then lost the line. That is the difference between the
            # two cases, and it is what keeps line loss on its own from ever reversing the truck:
            #   collision -- commanded, already stopped, so `first` is set; the line is then lost;
            #                the evidence continues and admits a recovery.       -> recovery
            #   line loss -- commanded and MOVING, so a speed above 0.03 m/s keeps resetting the
            #                detector and `first` is None; forward then goes to neutral and the
            #                evidence can never start.                           -> no recovery
            forward_commanded = forward_veto is None and not self.recovering
            stall_input = forward_commanded or self._stall_qualified()
            if not self.recovering and self.no_motion.update(now, pose, commanded=stall_input):
                if not self.reverse_ready:
                    # 🔴 The waveform and its limits have no physical characterisation, so the
                    # truck is stopped rather than reversed. This is the accepted outcome until
                    # Phase 2 produces one; --reverse-ready is the human's acceptance.
                    self._withdraw("recovery_unaccepted")
                    fresh = stopped = False
                elif self.recovery_attempts >= 1:
                    self._withdraw("recovery_exhausted")
                    fresh = stopped = False
                else:
                    self.recovery_attempts += 1
                    self.motion_epoch += 1
                    self._motion_demonstrated = False
                    self._segment_origin = None
                    self.recovering = True
                    self.recovery.start(now, pose)
                    self.rest.reset()
            if self.recovering:
                decision = self.recovery.update(now, pose, tele, intent.created)
                recovery_cmd = decision.throttle
                if decision.terminal_reason:
                    self._withdraw(decision.terminal_reason)
                    fresh = stopped = False
                elif decision.done:
                    self.recovering = False
                    self.no_motion.reset()
                    if not self.resume_after_recovery:
                        # Phase 3 default: the episode ENDS here. The truck may or may not be
                        # free -- first movement is not clearance -- and resuming forward is a
                        # separate decision with its own evidence. `recovery_complete` is not a
                        # failure; it is the run finishing its one bounded attempt.
                        self._withdraw("recovery_complete")
                        fresh = stopped = False
                    # With resume accepted, the completed sequence emits neutral on this tick
                    # and forward may resume on the NEXT tick from a post-settle frame.

        # Recompute manual wheel authority on every tick, even without frames.
        wheel = weight = steer = None
        manual_on = override_on = False
        pwm = 0
        state = "release"
        if hold:
            state = "hold_sw"
        elif drive_ok and middle and self.enabled and channel(tele.get("rc8")) and channel(tele.get("rc6")):
            wheel = self.fc.steer_from_pwm(tele["rc8"])
            if a.override:
                weight, self.last_touch = self.weight(wheel, now, self.last_touch, a.override_dz, a.override_hold_ms)
                manual_on = a.manual_knob > 0 and tele["rc6"] <= a.manual_knob
                if manual_on: weight = 0.0
                override_on = weight == 0.0
            else:
                weight = max(0.0, min(1.0, (tele["rc6"] - 1000) / 1000.0))
            model = intent.steer if fresh and finite(intent.steer) else 0.0
            steer = weight * model + (1 - weight) * wheel
            pwm = self.fc.pwm(steer)
            state = "manual" if manual_on else "blend"
        elif drive_ok and model_end and fresh and finite(intent.steer):
            steer = intent.steer
            pwm = self.fc.pwm(steer)
            state = "model_sa_out"

        throttle = 0                   # release: receiver owns throttle, NOT active brake
        cell_stop_steer_held = False
        if self.active:
            if recovery_cmd is not None:
                throttle = int(recovery_cmd)
                cell_stop_steer_held = (batch_pause and self.batch.phase == "stopping"
                                       and finite(self._cell_stop_steer))
                steer = self._cell_stop_steer if cell_stop_steer_held else 0.0
                pwm = self.fc.pwm(steer)
                state = "cell_stop" if batch_pause else "recovery"
            elif forward_veto is not None:
                # v15.6: permission retained, forward NOT commanded. Neutral is a REQUEST, not
                # observed rest; `stop_state` below says which of the two this is.
                throttle = int(neutral)
                state = "trial_idle" if self.calibration_only else "forward_blocked"
            else:
                throttle = int(max(neutral, min(a.cruise_max, self.absolute_max, intent.throttle)))
        elif drive_ok and channel(stick) and stick < neutral - dz and a.reverse_scale < 1.0:
            throttle = int(round(neutral - a.reverse_scale * (neutral - stick)))

        # --t3-throttle (operator's ruling 2026-09-27 23:4x, at the truck): the speed dial IS the throttle.
        # No pose, no speed loop, no start gesture, no permission epoch. Conditions are the radio's
        # own: armed + MANUAL + fresh RC + kill released (drive_ok), the mode switch in the MIDDLE, trigger at
        # neutral (a pull hands the throttle back to the receiver), brake latch clear. Dial at or
        # below 1100 = neutral. Maps 1100..2000 linearly onto neutral..--cruise-max. Overrides
        # nothing above: a recovery or an active cruise epoch never coexists with this mode because
        # the mode is only used with --cruise absent (cruise_decide vetoes "off", so active is never
        # granted). Wheel override stays a STEERING override only.
        t3_on = False
        # Trigger-copy rest drifts with the MT12 throttle trim (1535 on 09-20, 1545 on 09-28): +/-60 is neutral here.
        t3_neutral_stick = channel(stick) and abs(stick - neutral) <= max(dz, 60)
        if getattr(a, "t3_throttle", False) and drive_ok and middle and channel(dial) and t3_neutral_stick \
                and not self.brake_latched and recovery_cmd is None and not self.active:
            if dial > 1100:
                # --cruise-band LO HI narrows the dial onto a usable throttle band (the XL-5 does nothing below
                # ~1600 us; 1500..1725 across the whole travel left ~5 usable clicks, operator 2026-09-27 23:5x).
                band = getattr(a, "cruise_band", None)
                lo, hi = (int(band[0]), int(band[1])) if band else (neutral, int(a.cruise_max))
                lvl = lo + (min(dial, 2000) - 1100) / 900.0 * (hi - lo)
                throttle = int(max(neutral, min(a.cruise_max, self.absolute_max, round(lvl))))
                t3_on = throttle > neutral + dz
            else:
                throttle = int(neutral)

        sent_ok = None
        if self.sending:
            self.fc.send(pwm, throttle)
            self.sends += 1
            sent_ok = True
        self.ticks += 1
        # THE LEDGER ROW GOES HERE, not where the decision was made. `throttle` is the value that
        # actually left this process; 0 means the override was RELEASED, which is not a neutral
        # command and must never be recorded as one.
        if self._pending_sample is not None:
            ts, decision, tpose, ttele = self._pending_sample
            self._pending_sample = None
            self._record_trial_sample(ts, decision, tpose, ttele,
                                      sent_throttle=int(throttle), sent=sent_ok,
                                      released=(throttle == 0))
        forward = (self.active and recovery_cmd is None and forward_veto is None) or t3_on
        status = dict(epoch=self.epoch, cruise_on=forward,
                      hard_veto=hard, forward_veto=forward_veto,
                      forward_blocked_s=(None if self.forward_blocked_since is None
                                         else round(now - self.forward_blocked_since, 2)),
                      reverse_ready=self.reverse_ready,
                      reverse_profile_id=self.reverse_profile_id,
                      resume_after_recovery=self.resume_after_recovery,
                      stall_evidence=(self.no_motion.first is not None) if self.no_motion is not None else None,
                      stall_qualified=self._stall_qualified(),
                      motion_demonstrated=self._motion_demonstrated,
                      autonomous_permission=self.active, motion_epoch=self.motion_epoch,
                      batch_phase=self.batch.phase if self.batch is not None else None,
                      batch_cell=self.batch.index if self.batch is not None else None,
                      cell_seconds=self.batch.cell_seconds if self.batch is not None else None,
                      recovery_phase=self.recovery.phase if self.recovery is not None else "idle",
                      recovery_attempts=self.recovery_attempts,
                      recovery_metrics=dict(self.recovery.metrics) if self.recovery is not None else None,
                      cruise_why=(("t3" if t3_on else None) if not self.active else
                                  ((("cell_stop" if batch_pause else "recovery") if recovery_cmd is not None
                                    else forward_veto) if not forward else None)) if (self.active or t3_on)
                                 else (self.reason or veto or "start_required"),
                      veto=veto, start_ready=self.ready, start_pending=self.pending_start is not None,
                      brake_latched=self.brake_latched,
                      # requested output, permission and observed motion are three different facts
                      stop_state=(("recovery" if recovery_cmd is not None else
                                   ("forward_blocked" if forward_veto is not None else "drive_active"))
                                  if self.active else ("stopped_pose" if stopped else "stop_requested")),
                      pwm=pwm, throttle=throttle, steer=steer, state=state, blend_w=weight, wheel=wheel,
                      manual_on=manual_on, override_on=override_on, sends=self.sends, sent_at=now,
                      sending=self.sending, ticks=self.ticks, thread_error=self.thread_error,
                      alive=self.is_alive() if self.ident is not None else None,
                      pose_valid=bool(pose_ok), pose_evidence=pose_evidence,
                      cell_stop_steer=self._cell_stop_steer,
                      cell_stop_steer_held=cell_stop_steer_held,
                      intent_created=intent.created if intent else None,
                      terminal_reason=terminal)
        with self._lock:
            self._status = status
        event = (self.epoch, self.active, status["cruise_why"], status["stop_state"], self.ready,
                 status["recovery_phase"], status["batch_phase"], status["batch_cell"])
        if self.receipt is not None and event != self._last_event:
            self.receipt(dict(event="output_owner", t=time.time(), **status))
        self._last_event = event
        return status

    def run(self):
        deadline = time.monotonic()
        try:
            while not self._quit.is_set():
                self.tick()
                deadline = max(deadline + self.period, time.monotonic())
                self._quit.wait(max(0.0, deadline - time.monotonic()))
        except BaseException as exc:
            # A sender that dies must SAY SO. Previously the exception unwound into the finally
            # below, the thread vanished, and nothing anywhere reported it -- the startup check
            # could still pass on booleans left in their passing states.
            self.thread_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._withdraw("sender_exit")
            if self.sending:
                self.fc.release()       # shutdown release is owned by this thread too
            # THE TERMINAL SNAPSHOT MUST NOT INHERIT LIVE-LOOKING FIELDS. A stopped owner was
            # reporting autonomous_permission=True and sending=True while internally active=False.
            # On exit: permission is false, output is released, and motion is UNKNOWN unless some
            # other evidence says otherwise -- which this thread no longer has.
            with self._lock:
                self._status = dict(self._status, epoch=self.epoch, cruise_on=False,
                                    autonomous_permission=False, sending=False,
                                    hard_veto=None, forward_veto=None,
                                    recovery_phase="idle", stall_evidence=False,
                                    stall_qualified=False, motion_demonstrated=False,
                                    cruise_why=self.thread_error or "sender_exit",
                                    thread_error=self.thread_error,
                                    throttle=0, pwm=0, stop_state="unknown")

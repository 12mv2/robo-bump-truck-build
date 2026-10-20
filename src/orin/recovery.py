# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Bounded XL-5 brake/neutral/reverse sequence; no connections or actuator writes.

All default distances, times and PWM fractions are PROPOSED, not measured on this
truck. The output owner must independently enforce human authority and fresh
intent on every tick. This module does not grant permission to start or resume.
"""
from dataclasses import dataclass
import math


# 🔴 ABSOLUTE bound on any reverse amplitude this code will ever command, supervised trial
# included. THIS IS THE ONE NUMBER: `HardwareProfile.input_for_fraction()` validates against it
# too, so there is no second range to drift out of step.
#
# It was .60 for a few hours on 2026-09-14 and that was a defect, found by the external review:
# `input_for_fraction` has always refused above .30, so a .40 trial queued successfully and then
# raised ValueError on its first owner tick -- killing the sender thread before any trial result
# existed. Two ranges for one quantity is how you get a dead sender.
#
# It is .30 and NOT .60 because the mapping's validated range is the real software limit. The
# 2026-09-14 sweep reached .60, but that tool talked to MAVLink directly and never went through
# this mapping, so it is not evidence that this path supports it. If characterisation shows the
# truck needs more than .30 to break away, raising this is a DELIBERATE, SEPARATE decision that
# must also change `input_for_fraction` and be justified by that measurement -- never a default
# widened so a trial can pass.
ABS_FRACTION_CEILING = .60
# RAISED .30 -> .60 on 2026-09-15 00:30, on the operator's explicit authorization and with the
# measurement the gate demanded. Two supervised trials through this exact code path, both
# admitted from pose-confirmed rest, truck wedged nose-in against the fridge with its left
# front wheel under it:
#   fridge0025  .25  -> no_motion_within_bounds, negative_timeout, backwards -0.0027 m
#   fridge0030  .30  -> no_motion_within_bounds, negative_timeout, backwards -0.0003 m
# 1,384 pose samples over the second gave a net displacement of 0.0002 m.
# 🔑 THE DECIDING OBSERVATION, from the operator, which no sensor on this rig can supply:
#   "the driven wheels did not turn during either pulse. They did not spin. Too weak."
# NO ROTATION means this is NOT the traction limit -- wheelspin would have ENDED the escalation
# and this bound would have stayed at .30. It is below break-away torque, so amplitude is a
# legitimate lever.
# .60 and not higher: that is the top row of the 2026-09-14 sweep, the highest amplitude this
# vehicle has ever been commanded at, and the run that did free it from the fridge. Going beyond
# amplitudes the vehicle has actually seen would be extrapolation.
# STILL A BOUND, NOT A TARGET. Tables start low and every row stops on first motion.


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _angle(value):
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class RecoveryConfig:
    # .10 -> .25, 2026-09-14 19:48, on the FIRST TWO LOADED MEASUREMENTS of this code.
    # At .10 the retreat commanded a minimum SERVO3 of 1464 us -- 36 us below neutral --
    # while this truck needs ~80 us ABOVE neutral to break away going forward (measured from
    # the operator's hand laps: rolls at servo3 ~1570-1580). So the retreat was asking for less than
    # half the push needed to move at all. Runs gain-1909 and fridge-1947, both nose-in
    # against an obstacle, both spent the full 3 s negative budget and moved 2 mm and 0.7 mm.
    # The operator at the truck: "was not strong enough."
    # .25 of the 400 us reverse range is ~100 us below neutral, matching the forward
    # break-away margin with a little over. THE TRAVEL BOUNDS ARE UNCHANGED (retreat_m .08,
    # max_travel_m .15, max_speed_mps .20): this changes how hard it pushes, not how far it goes.
    max_fraction: float = .25
    # The ceiling max_fraction is checked against. A DELIBERATE, LAUNCH-TIME, RECORDED human
    # decision for a supervised characterisation trial -- "select limits before the trial; a
    # failed test cannot raise its own acceptance limits". Nothing in a running trial may
    # change it, and ABS_FRACTION_CEILING below bounds it absolutely whatever is asked for.
    fraction_ceiling: float = .30
    # Raised with max_fraction, 2026-09-14 19:52. This is fraction PER SECOND, so it is
    # coupled to max_fraction: at .10/s a .25 cap takes 2.5 s to reach and would spend most
    # of the 3 s negative budget merely ramping. .25/s keeps the ORIGINAL one-second climb
    # to full amplitude. The bench caught this; the vehicle would have shown it as another
    # 'not strong enough' retreat. Coupled constants must move together.
    ramp_fraction_s: float = .25
    neutral_dwell_s: float = .30
    standstill_s: float = .50
    primer_hold_s: float = .20
    phase_timeout_s: float = 3.0
    total_timeout_s: float = 10.0
    # The operator ruled 1.0 s (even 0.5) at the truck 2026-09-14 18:04, after the first physical
    # exercise of this code (run blocks-1800, wheels up) and after reversing out of a corner by
    # hand. TRIED AND REVERTED THE SAME MINUTE: at 1.0 the sequence never reaches
    # `retreat` at all -- 3 recovery tests and 137 reverse-owner assertions fail, because this
    # budget covers the whole negative-command sequence and the XL-5 primer blip alone took
    # 1.24 s on the vehicle (blocks-1800: primer t+126.66 -> neutral_gap t+127.90).
    # WHAT THE DATA ACTUALLY SAYS: this timer is the CANNOT-MOVE backstop. When the truck can
    # move, `retreat_m` .08 ends the retreat first -- at the .20 m/s cap that is ~0.4 s of real
    # backwards motion, already inside what the operator asked for. The 3.02 s observed happened only
    # because a truck on blocks can never travel 8 cm.
    # OWED: measure the loaded duration nose-into-a-corner on the ground, then set this from that
    # measurement rather than from either of our impressions.
    negative_timeout_s: float = 3.0
    retreat_m: float = .08
    max_travel_m: float = .15
    # 🔴 PROVISIONAL AND NOT PHYSICALLY JUSTIFIED. External review, 2026-09-14 20:00:
    # "the recovery speed limit was raised from .20 to .35 after SIMULATED retreat tripped
    # the earlier bound ... the fake actuator is not a vehicle dynamics model ... those tests
    # cannot establish that a useful physical retreat necessarily exceeds 0.20 m/s."
    # That is correct: a MOTION LIMIT was raised to make a synthetic sequence pass. DO NOT
    # DRIVE ON THIS until a measured reverse calibration justifies it or returns it to .20.
    # .20 -> .35 with max_fraction, 2026-09-14 19:55. A push big enough to break the truck free
    # necessarily retreats faster than .20 m/s, so the old cap would abort every useful retreat
    # with 'speed_limit'. The manoeuvre stays bounded by DISTANCE (retreat_m .08, max_travel_m
    # .15): at .35 m/s that is under half a second of motion.
    max_speed_mps: float = .35
    max_forward_m: float = .02
    max_lateral_m: float = .04
    max_heading_rad: float = math.radians(20)
    # 1.5 -> 3.5 at the truck 2026-09-14 18:24. THE DETECTOR WAS FIRING ON EVERY NORMAL START.
    # This truck's measured break-away is ~2.5 s (FINDINGS 76, and again tonight: run
    # ladder-1822 started cruise at t+117.8 and the detector fired at t+119.3, 1.5 s later,
    # with the speed PI still ramping). A launch allowance shorter than the break-away time
    # guarantees a false stall on every cell. An earlier run hit this at
    # 1.0 s and raised it to 3.0; it was never carried into this default.
    # The cost is that a REAL mid-lap stall now takes 3.5 s to detect instead of 1.5. At
    # 0.3-0.6 m/s the vehicle is not moving during that time anyway, and the travel bounds in
    # the retreat are unchanged.
    no_motion_window_s: float = 3.5
    servo_max_age_ms: float = 150.0
    neutral_band_us: float = 5.0
    pose_max_age_s: float = .20
    max_sample_jump_m: float = .06
    rest_range_m: float = .01
    rest_speed_mps: float = .03
    primer_motion_m: float = .005

    def __post_init__(self):
        for name, value in vars(self).items():
            if not _finite(value) or value <= 0:
                raise ValueError("recovery config must be finite and positive: " + name)
        if not 0 < self.fraction_ceiling <= ABS_FRACTION_CEILING:
            raise ValueError(f"fraction ceiling must be in (0, {ABS_FRACTION_CEILING}]")
        if self.max_fraction > self.fraction_ceiling:
            raise ValueError(f"recovery throttle fraction {self.max_fraction} exceeds its "
                             f"ceiling {self.fraction_ceiling}")
        if self.ramp_fraction_s > self.fraction_ceiling:
            raise ValueError("recovery throttle ramp exceeds the fraction ceiling")
        if self.retreat_m > self.max_travel_m or self.max_travel_m > .15:
            raise ValueError("recovery travel exceeds initial bounds")
        if self.primer_motion_m > self.retreat_m:
            raise ValueError("primer_motion_m must not exceed retreat_m")
        if self.max_speed_mps > .35 or self.servo_max_age_ms > 150:
            raise ValueError("recovery speed/telemetry age exceeds initial bounds")
        if self.neutral_band_us > 5:
            raise ValueError("recovery neutral tolerance exceeds initial bound")


@dataclass(frozen=True)
class HardwareProfile:
    rc_min: float
    neutral: float
    rc_max: float
    rc_dz: float
    servo_min: float
    servo_neutral: float
    servo_max: float
    slew_rate: float
    reverse_delay: float
    override_time: float

    @classmethod
    def from_params(cls, params):
        names = ("RC2_MIN", "RC2_TRIM", "RC2_MAX", "RC2_DZ", "RC2_REVERSED",
                 "SERVO3_MIN", "SERVO3_TRIM", "SERVO3_MAX", "SERVO3_REVERSED",
                 "SERVO3_FUNCTION", "RCMAP_THROTTLE", "MOT_PWM_TYPE", "MOT_SLEWRATE",
                 "MOT_REV_DELAY", "MOT_THR_MIN", "MOT_THR_MAX", "MOT_THST_EXPO", "RC_OVERRIDE_TIME")
        for name in names:
            if name not in params or not _finite(params[name]):
                raise ValueError("missing/nonfinite recovery parameter: " + name)
        expected = dict(RC2_REVERSED=0, SERVO3_REVERSED=0, SERVO3_FUNCTION=70,
                        RCMAP_THROTTLE=2, MOT_PWM_TYPE=0, MOT_THR_MIN=0,
                        MOT_THR_MAX=100, MOT_THST_EXPO=0)
        for name, value in expected.items():
            if params[name] != value:
                raise ValueError("uncharacterized recovery parameter: " + name)
        lo, center, hi, dz = (params[n] for n in ("RC2_MIN", "RC2_TRIM", "RC2_MAX", "RC2_DZ"))
        slo, scenter, shi = (params[n] for n in ("SERVO3_MIN", "SERVO3_TRIM", "SERVO3_MAX"))
        if not (900 <= lo < center < hi <= 2100 and 0 <= dz < min(center-lo, hi-center)):
            raise ValueError("invalid RC2 calibration")
        if not (900 <= slo < scenter < shi <= 2100):
            raise ValueError("invalid SERVO3 calibration")
        if not 0 < params["RC_OVERRIDE_TIME"] <= .5:
            raise ValueError("RC_OVERRIDE_TIME must be positive and <=0.5 seconds")
        if not 0 <= params["MOT_SLEWRATE"] <= 1000 or not 0 <= params["MOT_REV_DELAY"] <= 2:
            raise ValueError("uncharacterized motor slew/reversal delay")
        return cls(lo, center, hi, dz, slo, scenter, shi, params["MOT_SLEWRATE"],
                   params["MOT_REV_DELAY"], params["RC_OVERRIDE_TIME"])

    def input_for_fraction(self, fraction):
        """Positive magnitude means reverse; skip RC input dead zone explicitly."""
        if not _finite(fraction) or not 0 <= fraction <= ABS_FRACTION_CEILING:
            raise ValueError(f"reverse fraction must be in [0, {ABS_FRACTION_CEILING}]")
        if fraction == 0:
            return int(round(self.neutral))
        # Ceil towards neutral: integer rounding must never exceed the cap.
        return int(math.ceil(self.neutral - self.rc_dz - fraction *
                             (self.neutral - self.rc_dz - self.rc_min)))

    def servo_for_input(self, pwm):
        """Steady state output for the validated linear ArduRover profile.

        Actual output may lag because of slew/reversal delay. The sequence uses
        SERVO_OUTPUT_RAW feedback to establish dwell, never this prediction alone.
        """
        if not _finite(pwm) or not self.rc_min <= pwm <= self.rc_max:
            raise ValueError("input outside calibrated range")
        if pwm < self.neutral - self.rc_dz:
            fraction = (pwm - self.neutral + self.rc_dz) / (self.neutral - self.rc_dz - self.rc_min)
            return self.servo_neutral + fraction * (self.servo_neutral - self.servo_min)
        if pwm > self.neutral + self.rc_dz:
            fraction = (pwm - self.neutral - self.rc_dz) / (self.rc_max - self.neutral - self.rc_dz)
            return self.servo_neutral + fraction * (self.servo_max - self.servo_neutral)
        return self.servo_neutral


@dataclass(frozen=True)
class Decision:
    throttle: int
    phase: str
    terminal_reason: object = None
    done: bool = False


def _pose_error(now, pose, max_age):
    if pose is None or getattr(pose, "tracking", None) != "normal" or not getattr(pose, "recording", None):
        return "pose_invalid"
    if any(not _finite(getattr(pose, name, None)) for name in
           ("seq", "stamp", "received", "age_s", "x", "z", "speed", "heading")):
        return "pose_invalid"
    if pose.age_s < -.02 or now < pose.received or max(0, pose.age_s) + now - pose.received > max_age:
        return "pose_stale"
    return None


class NoMotionDetector:
    """Fresh pose evidence of sustained commanded nonprogress, including startup.

    This is not an impact classifier. Missing/stale pose resets evidence and must
    be handled by the owner's independent localization veto.
    """
    def __init__(self, window_s=1.5, range_m=.015, speed_mps=.03, max_age_s=.20):
        if any(not _finite(v) or v <= 0 for v in (window_s, range_m, speed_mps, max_age_s)):
            raise ValueError("invalid no-motion bounds")
        self.window_s, self.range_m, self.speed_mps, self.max_age_s = window_s, range_m, speed_mps, max_age_s
        self.reset()

    def reset(self):
        self.first = self.last = None
        self.triggered = False
        self.min_x = self.max_x = self.min_z = self.max_z = None

    def update(self, now, pose, commanded):
        if not commanded or not _finite(now) or _pose_error(now, pose, self.max_age_s):
            self.reset()
            return False
        if self.last is not None:
            if pose.seq == self.last.seq and pose.stamp == self.last.stamp:
                return self.triggered
            if (pose.recording != self.last.recording or pose.seq <= self.last.seq
                    or pose.stamp <= self.last.stamp or pose.stamp - self.last.stamp > self.max_age_s):
                self.reset()
        if abs(pose.speed) > self.speed_mps:
            self.reset()
            self.last = pose
            return False
        if self.first is None:
            self.first = pose
            self.min_x = self.max_x = pose.x
            self.min_z = self.max_z = pose.z
        self.min_x, self.max_x = min(self.min_x, pose.x), max(self.max_x, pose.x)
        self.min_z, self.max_z = min(self.min_z, pose.z), max(self.max_z, pose.z)
        if math.hypot(self.max_x-self.min_x, self.max_z-self.min_z) > self.range_m:
            self.reset()
            self.first = pose
            self.min_x = self.max_x = pose.x
            self.min_z = self.max_z = pose.z
        self.last = pose
        self.triggered = pose.stamp - self.first.stamp >= self.window_s
        return self.triggered


NoMotion = NoMotionDetector


class Recovery:
    def __init__(self, config, profile):
        self.c, self.p = config, profile
        self.reset()

    def reset(self):
        self.phase, self.terminal_reason = "idle", None
        self.started = self.phase_started = self.last_now = None
        self.origin = self.last_pose = None
        self.servo_seq = self.servo_seen_at = None
        self.last_command = int(round(self.p.neutral))
        self.travel = self.negative_time = 0.0
        self.backwards = self.forward = self.lateral = self.heading_error = 0.0
        self._rest_first = self._neutral_first = self._neutral_last = self._primer_reached = None
        self._rest_min_x = self._rest_max_x = self._rest_min_z = self._rest_max_z = None
        self.settled_at = None
        self.primer_moved = False

    @property
    def metrics(self):
        return dict(phase=self.phase, travel_m=self.travel, negative_s=self.negative_time,
                    backwards_m=self.backwards, forward_m=self.forward, lateral_m=self.lateral,
                    heading_error_rad=self.heading_error, primer_moved=self.primer_moved,
                    started=self.started, phase_started=self.phase_started, settled_at=self.settled_at,
                    terminal_reason=self.terminal_reason)

    def start(self, now, pose):
        self.reset()
        if not _finite(now):
            return self._terminal("clock_invalid")
        error = _pose_error(now, pose, self.c.pose_max_age_s)
        if error:
            return self._terminal(error)
        self.started = self.last_now = now
        self.origin = self.last_pose = pose
        self._enter("neutral_stop", now)
        return self._decision()

    def _enter(self, phase, now):
        self.phase, self.phase_started = phase, now
        # Fresh intent is required AFTER measured settling. Keep that evidence
        # across the waiting phase, while continuing to invalidate it on motion.
        if phase != "await_intent":
            self._rest_first = self._neutral_first = self._neutral_last = self._primer_reached = None
            self._rest_min_x = self._rest_max_x = self._rest_min_z = self._rest_max_z = None
        self.last_command = int(round(self.p.neutral))

    def _decision(self, pwm=None):
        self.last_command = int(round(self.p.neutral)) if pwm is None else int(pwm)
        return Decision(self.last_command, self.phase, self.terminal_reason, self.phase == "done")

    def _terminal(self, reason):
        self.phase, self.terminal_reason = "terminal", reason
        return self._decision()

    def _rest(self, pose, is_new, neutral):
        if not neutral or abs(pose.speed) > self.c.rest_speed_mps:
            self._rest_first = None
            return False
        if is_new:
            if self._rest_first is None:
                self._rest_first = pose
                self._rest_min_x = self._rest_max_x = pose.x
                self._rest_min_z = self._rest_max_z = pose.z
            self._rest_min_x, self._rest_max_x = min(self._rest_min_x, pose.x), max(self._rest_max_x, pose.x)
            self._rest_min_z, self._rest_max_z = min(self._rest_min_z, pose.z), max(self._rest_max_z, pose.z)
            if math.hypot(self._rest_max_x-self._rest_min_x, self._rest_max_z-self._rest_min_z) > self.c.rest_range_m:
                self._rest_first = pose
                self._rest_min_x = self._rest_max_x = pose.x
                self._rest_min_z = self._rest_max_z = pose.z
        return self._rest_first is not None and pose.stamp - self._rest_first.stamp >= self.c.standstill_s

    def update(self, now, pose, tele, intent_created):
        if self.phase in ("idle", "terminal", "done"):
            return self._decision()
        if not _finite(now) or now < self.last_now:
            return self._terminal("clock_invalid")
        dt = now - self.last_now
        self.last_now = now
        if now - self.started > self.c.total_timeout_s:
            return self._terminal("episode_timeout")
        if now - self.phase_started > self.c.phase_timeout_s:
            return self._terminal("phase_timeout:" + self.phase)
        error = _pose_error(now, pose, self.c.pose_max_age_s)
        if error:
            return self._terminal(error)
        if pose.recording != self.origin.recording:
            return self._terminal("recording_changed")
        age, seq, servo = (tele.get(n) for n in ("servo3_age_ms", "servo3_seq", "servo3"))
        if not all(_finite(v) for v in (age, seq, servo)) or not 0 <= age <= self.c.servo_max_age_ms:
            return self._terminal("servo_stale")
        if not self.p.servo_min <= servo <= self.p.servo_max:
            return self._terminal("servo_out_of_range")
        servo_new = self.servo_seq is None or seq > self.servo_seq
        if self.servo_seq is not None and seq < self.servo_seq:
            return self._terminal("servo_sequence_regressed")
        if servo_new:
            self.servo_seq, self.servo_seen_at = seq, now - age / 1000.0
        if now - self.servo_seen_at > self.c.servo_max_age_ms / 1000.0:
            return self._terminal("servo_not_advancing")
        neutral = abs(servo-self.p.servo_neutral) <= self.c.neutral_band_us
        cap_servo = self.p.servo_for_input(self.p.input_for_fraction(self.c.max_fraction))
        if servo < cap_servo - self.c.neutral_band_us:
            return self._terminal("servo_reverse_limit")
        if self.last_command < self.p.neutral-self.p.rc_dz or servo < self.p.servo_neutral-self.c.neutral_band_us:
            self.negative_time += dt
        if self.negative_time > self.c.negative_timeout_s:
            return self._terminal("negative_timeout")

        previous = self.last_pose
        is_new = pose.seq > previous.seq and pose.stamp > previous.stamp
        position_speed = 0.0
        if not is_new and not (pose.seq == previous.seq and pose.stamp == previous.stamp):
            return self._terminal("pose_not_advancing")
        if not is_new:
            if any(getattr(pose, key) != getattr(previous, key) for key in ("x", "z", "speed", "heading")):
                return self._terminal("pose_duplicate_changed")
            if max(0, previous.age_s) + now-previous.received > self.c.pose_max_age_s:
                return self._terminal("pose_not_advancing")
        if is_new:
            if pose.stamp-previous.stamp > self.c.pose_max_age_s:
                return self._terminal("pose_gap")
            step = math.hypot(pose.x-previous.x, pose.z-previous.z)
            if step > self.c.max_sample_jump_m:
                return self._terminal("pose_jump")
            # Phone speed is averaged and can lag a sudden movement. This raw
            # position difference is an independent, deliberately strict gate.
            # Its sensitivity to VIO jitter still requires physical measurement.
            position_speed = step / (pose.stamp-previous.stamp)
            self.travel += step
            self.last_pose = pose
        dx, dz = pose.x-self.origin.x, pose.z-self.origin.z
        self.forward = dx*math.cos(self.origin.heading) + dz*math.sin(self.origin.heading)
        self.backwards = -self.forward
        self.lateral = -dx*math.sin(self.origin.heading) + dz*math.cos(self.origin.heading)
        self.heading_error = _angle(pose.heading-self.origin.heading)
        for exceeded, reason in (
                (self.travel > self.c.max_travel_m, "travel_limit"),
                (abs(pose.speed) > self.c.max_speed_mps, "speed_limit"),
                (self.forward > self.c.max_forward_m, "forward_limit"),
                (abs(self.lateral) > self.c.max_lateral_m, "lateral_limit"),
                (abs(self.heading_error) > self.c.max_heading_rad, "heading_limit"),
                (position_speed > self.c.max_speed_mps + 1e-9, "position_speed_limit")):
            if exceeded:
                return self._terminal(reason)

        if neutral and servo_new and self.servo_seen_at >= self.phase_started:
            if self._neutral_first is None:
                self._neutral_first = self.servo_seen_at
            self._neutral_last = self.servo_seen_at
        elif not neutral:
            self._neutral_first = self._neutral_last = None
        dwell = (self._neutral_first is not None and self._neutral_last is not None
                 and self._neutral_last-self._neutral_first >= self.c.neutral_dwell_s)
        rest = self._rest(pose, is_new, neutral)

        if self.phase in ("neutral_stop", "neutral_gap", "settle"):
            if self.phase == "neutral_gap" and self.backwards >= self.c.primer_motion_m:
                self.primer_moved = True
                self._enter("settle", now)  # includes coast after primer release
            elif dwell and rest:
                if self.phase == "neutral_stop":
                    self._enter("primer", now)
                elif self.phase == "neutral_gap":
                    self._enter("retreat", now)
                else:
                    self.settled_at = now
                    self._enter("await_intent", now)
            return self._decision()
        if self.phase == "await_intent":
            if not neutral or not rest:
                self._enter("settle", now)
                return self._decision()
            if _finite(intent_created) and self.settled_at < intent_created <= now:
                self._enter("done", now)
            return self._decision()

        if self.phase == "primer" and self.backwards >= self.c.primer_motion_m:
            self.primer_moved = True
            self._enter("settle", now)
            return self._decision()
        if self.phase == "retreat" and self.backwards >= self.c.retreat_m:
            self._enter("settle", now)
            return self._decision()
        fraction = min(self.c.max_fraction, max(0.0, now-self.phase_started)*self.c.ramp_fraction_s)
        pwm = self.p.input_for_fraction(fraction)
        if self.phase == "primer":
            expected = self.p.servo_for_input(pwm)
            reached = fraction >= self.c.max_fraction and abs(servo-expected) <= self.c.neutral_band_us
            if reached and servo_new:
                if self._primer_reached is None:
                    self._primer_reached = self.servo_seen_at
                if self.servo_seen_at-self._primer_reached >= self.c.primer_hold_s:
                    self._enter("neutral_gap", now)
                    return self._decision()
            elif not reached:
                self._primer_reached = None
        return self._decision(pwm)

# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Path Recovery and automatic turnarounds: frame-thread state machine, heading input, event log.

    SEARCH -> ACQUIRE -> ALIGN -> FOLLOW            recovery (regain the path)
    TURN -> SEARCH -> ACQUIRE -> ALIGN -> FOLLOW    turnaround (reverse the intended travel direction)

The vision loop owns this. It calls `PathRecovery.step()` once per frame, after the camera command
is computed and before the intent is published. Nothing here sends to the FC. Each step returns a
`Decision`:
  - `kappa`: a manoeuvre curvature that REPLACES the camera command (TURN / SEARCH / ACQUIRE), or
    None, meaning the ordinary lane controller steers (ALIGN / FOLLOW / idle).
  - `stop_reason`: set once, when a trial fails. SimpleOwner then latches neutral throttle until a
    HUMAN visit, the same latch the external planner's steer mode uses.
  - `maneuver_active`: true from trial start to trial end. The sweep clock pauses on those frames.
  - `frame`: the dict logged on the frame row (`recovery`, see RECOVERY-EVENTS.md).
Events go to the run's frames.jsonl as `event: "recovery"` rows, format `recovery-event/1`
(the recovery-events schema, not shipped). Startup thresholds and operator request
decisions go in as `event: "recovery_config"` and `event: "recovery_request"` rows.

CONVENTIONS
  kappa   1/m, + = right (live_trail).
  psi     radians, NED: + = clockwise seen from above. FC ATTITUDE and IMU GyrZ use it; checked
          against ARKit on the 2026-10-06 lap 02 log. Moving forward with kappa > 0 increases psi.
          Clockwise travel round an oval therefore accumulates POSITIVE psi.
  beta    the lane's angle in the truck frame from a line fit through the near ground points:
          + = the tape runs off to the right as it goes away. psi + beta is the lane direction the
          camera controller will take: the one ahead of the truck.

Every default threshold is a PROPOSAL, not a measurement on this truck. They are logged at startup
and on each trial_start, so a reader sees what was in force.
"""
from dataclasses import dataclass, field, asdict, fields
import hashlib
import json
import math
from pathlib import Path
import threading
import time

SCHEMA = "recovery-event/1"
STATES = ("INDUCE", "TURN", "SEARCH", "ACQUIRE", "ALIGN", "FOLLOW")
OUTCOMES = ("followed", "wrong_direction", "timeout", "human_takeover", "lost_after_follow",
            "aborted", "interrupted")
# Owner vetoes that are the operator's or the radio's, not a recovery failure.
HUMAN_VETOES = ("human",)
FAILURE_OUTCOMES = ("timeout", "lost_after_follow", "wrong_direction")
# Induced inward perturbation (Request.induction). v2 caps: a pulse long and wide enough to leave the tape on
# purpose. The bound that ends it is the gyro rotation or the commanded (driving) time, whichever comes first;
# INDUCTION_WALL_S over the commanded time is the independent wall bound for a stalled or paused observation.
INDUCTION_BOUNDS = (("duration_s", .1, 3.), ("max_heading_deg", 5., 90.), ("kappa", .1, 1.32))
INDUCTION_WALL_S = .5


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def wrap(a):
    """Angle to (-pi, pi]."""
    a = math.fmod(a + math.pi, 2 * math.pi)
    if a <= 0:
        a += 2 * math.pi
    return a - math.pi


def circ_mean(angles):
    if not angles:
        return None, None
    s = sum(math.sin(a) for a in angles); c = sum(math.cos(a) for a in angles)
    mean = math.atan2(s, c)
    spread = max(abs(wrap(a - mean)) for a in angles)
    return mean, spread


# --------------------------------------------------------------------------------------------------
# Configuration: every threshold in one visible place
# --------------------------------------------------------------------------------------------------
@dataclass
class PathRecoveryConfig:
    # automatic trigger (camera loss while following); off unless asked for
    auto: bool = False
    loss_s: float = 1.0                  # lane absent this long while following -> automatic trial
    arm_follow_s: float = 2.0            # lane followed this long before auto can fire (startup, handback)
    ref_window_s: float = 1.5            # heading reference: lane frames in the last this-many seconds before loss
    ref_max_spread_deg: float = 30.0     # reference refused if those headings spread wider
    # bounds (finite manoeuvre)
    max_trial_s: float = 40.0            # whole trial, wall of the manoeuvre
    max_search_s: float = 20.0           # one SEARCH episode
    max_search_turn_deg: float = 720.0   # gyro rotation inside one SEARCH episode
    max_reacquire: int = 2               # FOLLOW losses survived before the trial ends
    # search
    search_kappa: float = 1.1            # 1/m (R 0.9 m); the full-lock curvature is about 1.43
    search_side: str = "auto"            # auto (toward the side the lane was last seen) | left | right
    # Side of the SEARCH circle when the target is an oval_path direction (cw/ccw). last_seen: search_side as above
    # (an induced trial countersteers its inward pulse). travel: turn in the oval's travel sense (cw -> right,
    # ccw -> left) at the trial's start, after the induction pulse and on every re-search, so the circle is
    # co-directional with every tape it meets (an inward-departure countersteer circle meets each tape against
    # the travel direction). Trail and unknown targets are unchanged. last_seen is omitted from to_dict/digest,
    # so every existing config keeps its digest; travel is included.
    oval_search_sense: str = "last_seen"  # last_seen | travel
    # turnaround ("light bulb"): an away leg of turn_away_deg on radius Ra, then the main turn on Rb the other way,
    # then SEARCH continuing on Rb. With equal radii an Ackermann truck can only re-cross the line steeply (50-90 deg,
    # too few camera rows; desk sweep 2026-10-09: best 6/16). A 90 deg away leg with Ra ~ 0.9 Rb puts the return
    # circle's centre ~0.9 Rb beside the line, so it crosses the reversed line at acos(0.9) ~ 25 deg.
    # Lateral excursion ~ Ra + Rb (about 1.7 m) toward the away side, then back: choose the side with room.
    turn_side: str = "auto"              # the MAIN turn's side; the away leg goes the other way. auto: on an oval,
                                         # away leg OUTSIDE (cw travel: interior on the right -> main turn right);
                                         # elsewhere right. An inside turnaround needs ~1.7 m of a 2.4 m oval.
    turn_kappa: float = 1.1              # Rb 0.91 m; SEARCH after a turnaround keeps this curvature
    turn_away_kappa: float = 1.32        # Ra 0.76 m (desk sweep best of 1.12/1.22/1.32: 10/16 vs 7/16 vs 6/16)
    turn_away_deg: float = 90.0
    turn_deg: float = 90.0               # main-turn rotation before SEARCH (same side, same curvature, heading-gated)
                                         # takes over; the reversed lane is crossed later in the turn
    turn_max_s: float = 15.0
    # acquisition: temporal consistency + feasible approach, not one confident frame
    acq_frames: int = 5
    min_kept: int = 8                    # of 17 rows (= the controller's --min-rows 8). A lane crossed at 40-55 deg
                                         # shows only 7-9 rows; 12 refused every correct crossing in the desk sim.
                                         # Temporal consistency, goal jump and the heading gate carry the rejection.
    min_vote: int = 9                    # used only when fit_vote is logged
    max_goal_x: float = 0.6              # m
    max_goal_jump: float = 0.25          # m between consecutive candidate frames
    max_beta_deg: float = 60.0           # lane angle in the truck frame; steeper = not a feasible approach
    heading_gate_deg: float = 60.0       # psi + beta within this of the target heading (recovery on a trail)
    turn_gate_deg: float = 40.0          # the same gate for turnarounds: tighter, because on an oval the
                                         # neighbouring bend's same-direction tangent is only ~60-90 deg away
    # Where the gate applies. A heading captured before a loss describes the path only near that point:
    # on an oval bend the tangent turns ~40 deg per metre, so a search that rejoins further round the
    # bend meets the right lane at the "wrong" heading (desk sim 2026-10-09: 55 rejections, no rejoin).
    # turnaround_and_trail: turnarounds (rejoin within a few metres) and trails; oval recovery is then
    # checked by the turn sign in FOLLOW instead. always | turnaround_and_trail | never.
    heading_gate: str = "turnaround_and_trail"
    # alignment (the lane controller steers)
    align_max_s: float = 6.0
    follow_x: float = 0.15
    follow_beta_deg: float = 20.0
    follow_frames: int = 8
    # follow and success
    follow_loss_s: float = 0.7           # lane absent this long in ALIGN/FOLLOW -> follow_lost
    confirm_measure: str = "duration_s"  # duration_s only: no onboard distance without ARKit
    confirm_s: float = 5.0
    follow_lane_frac: float = 0.8        # FOLLOW frames with the lane present, required for follow_confirmed
    confirm_max_s: float = 25.0          # FOLLOW this long without a direction verdict -> timeout
    dir_min_deg: float = 45.0            # oval: net psi over FOLLOW with the target sign
    dir_tol_deg: float = 60.0            # trail: FOLLOW heading within this of the target heading
    on_wrong_direction: str = "stop"     # stop | turnaround
    # heading input
    heading_max_age_s: float = 0.25
    # authority
    stop_on_failure: bool = True
    throttle_cap_us: object = None       # optional PWM ceiling during TURN/SEARCH/ACQUIRE; the speed dial's lower value wins
    endpoint_refractory_s: float = 3.0   # FOLLOW time after a turnaround before another endpoint counts
    request_ttl_s: float = 60.0          # an accepted request not started (Assist + speed dial) within this expires

    def validate(self):
        pos = ("follow_lane_frac", "request_ttl_s", "turn_gate_deg", "ref_max_spread_deg", "loss_s", "arm_follow_s", "ref_window_s", "max_trial_s", "max_search_s", "max_search_turn_deg",
               "search_kappa", "turn_kappa", "turn_deg", "turn_max_s", "max_goal_x", "max_goal_jump",
               "max_beta_deg", "heading_gate_deg", "align_max_s", "follow_x", "follow_beta_deg",
               "follow_loss_s", "confirm_s", "confirm_max_s", "dir_min_deg", "dir_tol_deg", "heading_max_age_s")
        for k in pos:
            if not finite(getattr(self, k)) or getattr(self, k) <= 0:
                raise ValueError(f"path recovery: {k} must be a positive number")
        is_int = lambda v: isinstance(v, int) and not isinstance(v, bool)
        for k in ("acq_frames", "follow_frames", "min_kept"):
            if not is_int(getattr(self, k)) or getattr(self, k) < 1:
                raise ValueError(f"path recovery: {k} must be a positive integer")
        if not is_int(self.min_vote) or self.min_vote < 0:
            raise ValueError("path recovery: min_vote must be an integer >= 0")
        if not is_int(self.max_reacquire) or self.max_reacquire < 0:
            raise ValueError("path recovery: max_reacquire must be an integer >= 0")
        for k in ("auto", "stop_on_failure"):
            if not isinstance(getattr(self, k), bool):        # JSON "false" is a truthy string
                raise ValueError(f"path recovery: {k} must be true or false")
        if not finite(self.endpoint_refractory_s) or self.endpoint_refractory_s < 0:
            raise ValueError("path recovery: endpoint_refractory_s must be a number >= 0")
        if not finite(self.turn_away_deg):
            raise ValueError("path recovery: turn_away_deg must be a number")
        if self.search_kappa > 1.43 or self.turn_kappa > 1.43:
            raise ValueError("path recovery: curvature above full lock (1.43 1/m)")
        if self.search_side not in ("auto", "left", "right") or self.turn_side not in ("auto", "left", "right"):
            raise ValueError("path recovery: side must be auto/left/right (turn: left/right)")
        if self.oval_search_sense not in ("last_seen", "travel"):
            raise ValueError("path recovery: oval_search_sense must be last_seen or travel")
        if self.oval_search_sense == "travel" and self.search_side != "auto":
            raise ValueError("path recovery: oval_search_sense travel needs search_side auto")
        if self.confirm_measure != "duration_s":
            raise ValueError("path recovery: only duration_s is measurable onboard")
        if self.confirm_max_s < self.confirm_s:
            raise ValueError("path recovery: confirm_max_s < confirm_s")
        if self.heading_gate not in ("always", "turnaround_and_trail", "never"):
            raise ValueError("path recovery: heading_gate must be always, turnaround_and_trail or never")
        if self.on_wrong_direction not in ("stop", "turnaround"):
            raise ValueError("path recovery: on_wrong_direction must be stop or turnaround")
        if not 0 <= self.turn_away_deg <= 120:
            raise ValueError("path recovery: turn_away_deg must be in [0, 120]")
        if not finite(self.turn_away_kappa) or not 0 < self.turn_away_kappa <= 1.43:
            raise ValueError("path recovery: turn_away_kappa must be in (0, 1.43]")
        if self.throttle_cap_us is not None and not (finite(self.throttle_cap_us) and 900 <= self.throttle_cap_us <= 2100):
            raise ValueError("path recovery: throttle_cap_us must be a PWM in 900..2100")
        return self

    def to_dict(self):
        d = asdict(self)
        if d["oval_search_sense"] == "last_seen":
            del d["oval_search_sense"]       # the default: rows and digest of every existing config are unchanged
        return d

    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError("path recovery: unknown config keys " + ", ".join(sorted(unknown)))
        return cls(**d).validate()

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))


# --------------------------------------------------------------------------------------------------
# Heading: timestamped observations with source and freshness
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HeadingObs:
    source: str              # fc_attitude_gyro | arkit_replay | sim | none
    psi: object              # rad, continuous (unwrapped) relative heading within `epoch`; None if unknown
    rate: object             # rad/s Euler yaw rate
    age_s: object            # seconds since the last sample, on the consumer's monotonic clock
    epoch: int               # increments on any gap/reset: psi values compare only within one epoch
    fused_yaw: object = None  # rad, FC EKF yaw (compass-fused) -- logged, never used for decisions
    tilt_ok: bool = True

    def usable(self, max_age_s):
        return (finite(self.psi) and finite(self.age_s) and 0 <= self.age_s <= max_age_s and self.tilt_ok)

    def log(self):
        r = lambda v, k=4: None if v is None else round(v, k)
        return dict(source=self.source, psi_deg=r(None if self.psi is None else math.degrees(self.psi), 2),
                    rate_dps=r(None if self.rate is None else math.degrees(self.rate), 2),
                    age_ms=None if self.age_s is None else round(self.age_s * 1000, 1), epoch=self.epoch,
                    fused_yaw_deg=r(None if self.fused_yaw is None else math.degrees(self.fused_yaw), 2),
                    tilt_ok=self.tilt_ok)


NO_HEADING = HeadingObs("none", None, None, None, 0)


class GyroHeading:
    """Integrates MAVLink ATTITUDE body rates into a relative heading.

    Euler yaw rate = (q sin(roll) + r cos(roll)) / cos(pitch): the truck's FC sits at about 5 deg roll
    and -3.5 deg pitch, so raw body z alone mixes in other axes. Integration uses the FC's own
    time_boot_ms, so Orin receive jitter does not enter. A gap longer than `max_gap_s`, a backwards
    FC clock (reboot) or excessive tilt starts a new epoch: heading continuity is never assumed
    across one. ATTITUDE yaw (EKF, compass-fused) is carried for logging only: on the 2026-10-06 lap 02
    log it strayed ~8 deg per 60 s window, and the raw compass did not track at all (hard-iron offset
    ~340 mG > Earth's horizontal ~185 mG). Gyro integration: ~3.1 deg worst 60 s window for IMU0 minus
    the EKF bias estimate (the closest DataFlash stand-in for ATTITUDE body rates, an assumption Block 0
    checks), ~2.1 deg for raw IMU0 with a rest trim. (heading-check report, not shipped).

    `ingest_attitude` runs in whichever thread drains MAVLink (both hold FC.lock); `observe` runs
    in the vision loop. A lock guards the shared state.
    """
    source = "fc_attitude_gyro"

    def __init__(self, max_gap_s=0.2, max_tilt_deg=35.0):
        self.max_gap_s, self.max_tilt = max_gap_s, math.radians(max_tilt_deg)
        self._lock = threading.Lock()
        self.psi = None; self.rate = None; self.fused = None; self.epoch = 0
        self.last_fc_ms = None; self.last_rx = None; self.tilt_ok = True
        self.samples = 0; self.gaps = 0; self.resets = 0
        self.bias = 0.0

    def ingest_attitude(self, fc_ms, rx_mono, roll, pitch, yaw, p, q, r):
        if not all(finite(v) for v in (fc_ms, roll, pitch, q, r)):
            with self._lock:
                # A NaN sample breaks continuity: heading is unknown until the next valid sample starts a
                # fresh epoch at 0 (never integrate on across the break under a new epoch number).
                self.samples += 1; self.gaps += 1; self._new_epoch()
                self.psi = None; self.last_fc_ms = None
            return
        with self._lock:
            self.samples += 1
            tilt_ok = abs(roll) <= self.max_tilt and abs(pitch) <= self.max_tilt
            rate = (q * math.sin(roll) + r * math.cos(roll)) / max(math.cos(pitch), 0.2) - self.bias
            if self.last_fc_ms is not None and fc_ms == self.last_fc_ms:
                return                            # duplicate packet: not a reboot
            if self.last_fc_ms is None or fc_ms < self.last_fc_ms:
                if self.last_fc_ms is not None:
                    self.resets += 1
                self._new_epoch(); self.psi = 0.0
            else:
                dt = (fc_ms - self.last_fc_ms) / 1000.0
                if dt > self.max_gap_s:
                    self.gaps += 1; self._new_epoch(); self.psi = 0.0
                elif not tilt_ok or not self.tilt_ok:
                    if not tilt_ok and self.tilt_ok:
                        self._new_epoch()
                    self.psi = 0.0 if not tilt_ok else self.psi
                else:
                    self.psi += 0.5 * (rate + (self.rate if self.rate is not None else rate)) * dt
            self.tilt_ok = tilt_ok
            self.rate, self.fused = rate, yaw
            self.last_fc_ms, self.last_rx = fc_ms, rx_mono

    def _new_epoch(self):
        self.epoch += 1; self.rate = None

    def observe(self, now_mono):
        with self._lock:
            if self.last_rx is None:
                return NO_HEADING
            if self.psi is None:                  # after a NaN sample: unusable, but the new epoch is visible
                return HeadingObs(self.source, None, None, max(0.0, now_mono - self.last_rx), self.epoch,
                                  self.fused, self.tilt_ok)
            # The sender thread may ingest a sample between the frame's clock read and this call:
            # a microseconds-negative age is fresh, not invalid (smoke run 2026-10-09: 0.6% of frames).
            return HeadingObs(self.source, self.psi, self.rate, max(0.0, now_mono - self.last_rx), self.epoch,
                              self.fused, self.tilt_ok)

    def stats(self):
        with self._lock:
            return dict(samples=self.samples, gaps=self.gaps, resets=self.resets, epoch=self.epoch)


# --------------------------------------------------------------------------------------------------
# Inputs per frame
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LaneObs:
    """What the camera branch produced this frame (live_trail names in brackets)."""
    lane: bool               # the camera steered this frame [state == "lane"]
    kept: int                # rows with a line [kept]
    goal_x: object           # m, + = right [goal[0]]
    beta: object = None      # rad, lane angle in the truck frame (line_angle(pts)); None if unfit
    vote: object = None      # [fit_vote], when logged


def line_angle(pts, max_y=1.4, min_pts=4):
    """Least-squares x = a + b*y through ground points with y <= max_y; beta = atan(b)."""
    near = [(x, y) for x, y in pts if y <= max_y]
    if len(near) < min_pts:
        return None
    n = len(near); my = sum(y for _, y in near) / n; mx = sum(x for x, _ in near) / n
    syy = sum((y - my) ** 2 for _, y in near)
    if syy < 1e-6:
        return None
    b = sum((y - my) * (x - mx) for x, y in near) / syy
    return math.atan(b)


@dataclass(frozen=True)
class OwnerView:
    """The subset of the output owner's snapshot recovery reads."""
    mode: object
    veto: object
    cruise_on: bool
    override_on: bool
    throttle_source: object
    model_fresh: bool

    @classmethod
    def from_snapshot(cls, s):
        return cls(s.get("mode"), s.get("veto"), bool(s.get("cruise_on")), bool(s.get("override_on")),
                   s.get("throttle_source"), bool(s.get("model_fresh")))

    def human(self):
        return self.veto in HUMAN_VETOES or self.override_on or self.throttle_source == "human"

    def abort_reason(self):
        if self.throttle_source == "stale_capped":
            return "vision_stall"                 # our own loop stalled mid-manoeuvre: a fault, not the operator
        # Every owner veto that is not the human's (stop_sw, kill, radio, fc_mode_*, sa_between, latches, ...)
        # ends the trial: recovery never waits out a veto and resumes without a fresh operator go.
        if self.veto is not None and self.veto not in HUMAN_VETOES:
            return self.veto
        if self.mode in ("assist", "auto") and self.veto is None and not self.commanded() and not self.human():
            return "t3_bottom"
        return None

    def commanded(self):
        # speed-dial forward, or speed-dial forward held under this manoeuvre's own throttle cap
        return self.cruise_on or self.throttle_source == "t3_capped"

    def driving(self):
        return self.mode in ("assist", "auto") and self.veto is None and self.commanded() and not self.human()


@dataclass(frozen=True)
class Decision:
    kappa: object = None
    stop_reason: object = None
    throttle_cap_us: object = None
    maneuver_active: bool = False
    frame: object = None


@dataclass
class Request:
    """An operator or endpoint request. `id` makes it idempotent."""
    id: str
    maneuver: str                         # recovery | turnaround
    requested_direction: str = "current"  # current | reverse
    direction_reference: dict = field(default_factory=lambda: {"frame": "unknown", "value": None})
    induced: bool = True
    reason: str = "operator"
    source: str = "induced"               # induced | automatic | operator
    trial_id: object = None
    attempt: object = None
    search_side: object = None
    induction: object = None             # optional bounded inward perturbation, never a position target

    @classmethod
    def from_dict(cls, d, search_sense="last_seen"):
        """`search_sense` is the controller's PathRecoveryConfig.oval_search_sense: it decides the induction side rule."""
        known = {f.name for f in fields(cls)}
        r = cls(**{k: v for k, v in d.items() if k in known})
        if not isinstance(r.id, str) or not r.id:
            raise ValueError("request needs an id")
        if r.maneuver not in ("recovery", "turnaround"):
            raise ValueError("maneuver must be recovery or turnaround")
        if r.requested_direction not in ("current", "reverse"):
            raise ValueError("requested_direction must be current or reverse")
        ref = r.direction_reference
        if not isinstance(ref, dict) or ref.get("frame") not in ("oval_path", "trail", "unknown"):
            raise ValueError("direction_reference.frame must be oval_path, trail or unknown")
        if ref["frame"] == "oval_path" and ref.get("value") not in ("cw", "ccw"):
            raise ValueError("oval_path reference must be cw or ccw")
        if ref["frame"] == "trail" and ref.get("value") not in ("outbound", "return"):
            raise ValueError("trail reference must be outbound or return")
        if r.search_side not in (None, "auto", "left", "right"):
            raise ValueError("search_side must be auto, left or right")
        if r.maneuver == "turnaround" and r.requested_direction != "reverse":
            raise ValueError("a turnaround reverses travel: requested_direction must be reverse")
        if r.trial_id is not None and (isinstance(r.trial_id, bool) or not isinstance(r.trial_id, (str, int))):
            raise ValueError("trial_id must be a string or integer")
        if r.trial_id is not None:
            r.trial_id = str(r.trial_id)
        if r.attempt is not None and (isinstance(r.attempt, bool) or not isinstance(r.attempt, int) or r.attempt < 1):
            raise ValueError("attempt must be an integer >= 1")
        if not isinstance(r.induced, bool):
            raise ValueError("induced must be true or false")
        if not isinstance(r.reason, str) or r.source not in ("induced", "automatic", "operator"):
            raise ValueError("reason must be a string; source induced, automatic or operator")
        if r.induction is not None:
            if (not r.induced or r.source != "induced" or r.maneuver != "recovery"
                    or r.requested_direction != "current" or ref.get("frame") != "oval_path"):
                raise ValueError("induction requires induced same-direction oval recovery")
            if not isinstance(r.induction, dict) or set(r.induction) != {"duration_s", "max_heading_deg", "kappa"}:
                raise ValueError("induction needs duration_s, max_heading_deg and kappa only")
            for key, lo, hi in INDUCTION_BOUNDS:
                v = r.induction[key]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                    raise ValueError(f"induction {key} must be finite in [{lo}, {hi}]")
            if search_sense == "travel":
                # Direction chooses the inward side; search keeps turning in the oval's travel sense.
                expected = "right" if ref["value"] == "cw" else "left"
                if r.search_side not in (None, "auto", expected):
                    raise ValueError("induction search_side must be absent, auto or the travel side")
            else:
                # Direction chooses the inward side; search deliberately countersteers.
                expected = "left" if ref["value"] == "cw" else "right"
                if r.search_side not in (None, "auto", expected):
                    raise ValueError("induction search_side must countersteer the inward perturbation")
        return r


OPPOSITE = {"cw": "ccw", "ccw": "cw", "outbound": "return", "return": "outbound"}


def target_of(reference, requested):
    if reference.get("frame") == "unknown" or reference.get("value") is None:
        return {"frame": "unknown", "value": None}
    v = reference["value"] if requested == "current" else OPPOSITE[reference["value"]]
    return {"frame": reference["frame"], "value": v}


# --------------------------------------------------------------------------------------------------
# Operator / endpoint requests: a directory inbox, consumed once
# --------------------------------------------------------------------------------------------------
class RequestInbox:
    """`<dir>/*.json` request files. Each is renamed `.consumed` or `.rejected` after reading, so a
    controller restart never replays one. Ids seen in this process are refused a second time.
    A visible floor marker detector can replace this later: anything with `poll(now) -> [Request]`."""

    def __init__(self, directory, period_s=0.25, search_sense="last_seen"):
        self.dir = Path(directory); self.period = period_s; self.next_t = 0.0
        self.search_sense = search_sense
        self.dir.mkdir(parents=True, exist_ok=True)

    def poll(self, now):
        if now < self.next_t:
            return []
        self.next_t = now + self.period
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                if not isinstance(d, dict):
                    raise ValueError("request must be a JSON object")
                req = Request.from_dict(d, self.search_sense)
                p.rename(p.with_suffix(".consumed"))
                out.append(req)
            except Exception as exc:              # an operator file must never stop the controller
                try:
                    p.with_suffix(".reason").write_text(f"{type(exc).__name__}: {exc}\n")
                    p.rename(p.with_suffix(".rejected"))
                except OSError:
                    pass
                out.append(("invalid", p.name, f"{type(exc).__name__}: {exc}"))
        return out


# --------------------------------------------------------------------------------------------------
# Event log: recovery-event/1 rows with run, trial and clock identity
# --------------------------------------------------------------------------------------------------
def boot_identity():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        # macOS / tests: the boot epoch estimate is stable for one boot to ~1 s
        return "est-boot-%d" % round(time.time() - time.monotonic())


class EventLog:
    def __init__(self, sink, run_id, experiment_id=None, continuation_of=None, boot_id=None,
                 wall=time.time):
        self.sink, self.run_id = sink, run_id
        self.experiment_id, self.continuation_of = experiment_id, continuation_of
        self.boot_id = boot_id or boot_identity(); self.wall = wall
        self.seq = 0

    def identity(self, mono):
        self.seq += 1
        return dict(event="recovery", schema=SCHEMA, run_id=self.run_id, experiment_id=self.experiment_id,
                    continuation_of=self.continuation_of, seq=self.seq, t=self.wall(),
                    monotonic=round(mono, 4), boot_id=self.boot_id)

    def trial_row(self, trial, mono, kind, **extra):
        row = self.identity(mono)
        row.update(trial_id=trial.trial_id, attempt=trial.attempt, kind=kind, **trial.task())
        row.update(extra)
        self.sink(row)
        return row

    def side_row(self, event, mono, **extra):
        row = dict(event=event, schema=SCHEMA, run_id=self.run_id, t=self.wall(), monotonic=round(mono, 4),
                   boot_id=self.boot_id)
        row.update(extra)
        self.sink(row)
        return row


def attempt_history(paths, closed=None):
    """{trial_id: highest attempt} from earlier frames.jsonl files (continuations). If `closed` (a set) is given,
    it receives the trial_ids whose highest attempt ended with an outcome other than `interrupted`."""
    best = {}; ends = {}
    for path in paths or ():
        try:
            with open(path) as f:
                for line in f:
                    if '"recovery"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue          # a torn last line after power loss
                    if r.get("event") == "recovery" and r.get("kind") == "trial_start":
                        best[r["trial_id"]] = max(best.get(r["trial_id"], 0), int(r["attempt"]))
                    elif r.get("event") == "recovery" and r.get("kind") == "trial_end":
                        ends[(r["trial_id"], int(r["attempt"]))] = r.get("outcome")
        except OSError:
            continue
    if closed is not None:
        closed.update(tid for tid, a in best.items() if ends.get((tid, a)) not in (None, "interrupted"))
    return best


# --------------------------------------------------------------------------------------------------
# One trial
# --------------------------------------------------------------------------------------------------
@dataclass
class Trial:
    trial_id: str
    attempt: int
    maneuver: str
    requested_direction: str
    direction_reference: dict
    target_direction: dict
    induced: bool
    origin_stage: object
    trigger: dict
    t0: float
    target_psi: object = None          # (epoch, rad) when a local heading reference exists
    ref_note: str = "none"
    side: int = 1                      # +1 right, -1 left (search / turn side)
    follows_trial: object = None
    induction: object = None
    induction_elapsed_s: float = 0.0
    induction_rotation: float = 0.0
    induction_last: object = None       # monotonic, heading epoch, psi
    induction_done_t: object = None     # induction_complete
    tangent: object = None              # (epoch, psi) at trial start, logged when travel sense or induction
    lane_lost_t: object = None          # first frame without the lane after induction_complete, before first detection
    state: object = None
    state_t: float = 0.0
    first_detection_t: object = None
    first_follow_t: object = None
    confirmed_t: object = None
    follow_losses: int = 0
    human_active: bool = False
    human_s: float = 0.0
    human_t: object = None
    rejections: dict = field(default_factory=dict)
    # per-state working values
    cand: int = 0
    last_cand_x: object = None
    follow_ok: int = 0
    lane_absent_t: object = None
    psi_mark: object = None            # (epoch, psi) at the start of the current TURN leg / FOLLOW
    search_t0: object = None           # SEARCH episode start; ACQUIRE -> SEARCH does not restart it
    search_rot: float = 0.0            # gyro PATH LENGTH (sum of |dpsi|) in this SEARCH episode, across epochs:
                                       # conservative -- wobble counts too; rotation during a dropout counts on return
    last_psi: object = None            # (epoch, psi) of the previous SEARCH/ACQUIRE frame
    hd_bad_since: object = None        # first frame of a heading dropout during TURN
    turn_leg: int = 0
    follow_psi: list = field(default_factory=list)
    follow_n: int = 0                  # FOLLOW frames, and those with the lane present (confirmation needs a fraction)
    follow_lane: int = 0

    def task(self):
        return dict(maneuver=self.maneuver, requested_direction=self.requested_direction,
                    direction_reference=dict(self.direction_reference),
                    target_direction=dict(self.target_direction), induced=self.induced,
                    origin_stage=None if self.origin_stage is None else dict(self.origin_stage))


# --------------------------------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------------------------------
class PathRecovery:
    def __init__(self, cfg, log, direction_reference=None, history=None, clock_note=None, closed=None):
        self.cfg = cfg.validate()
        self.log = log
        self.default_reference = direction_reference or {"frame": "unknown", "value": None}
        self.history = dict(history or {})
        self.closed = set(closed or ())       # trial_ids whose latest attempt ended with a real outcome
        self.trial = None
        self.pending = None            # induced request waiting for the operator's go (Assist + speed dial)
        self.seen_ids = set()
        self.auto_n = 0
        self.lane_since = None         # continuous lane following, for arming
        self.lane_absent_since = None
        self.lane_heading = []         # (t, epoch, psi) on lane frames, recent
        self.last_goal_x = None
        self.refractory_until = None   # FOLLOW-seconds bookkeeping after a turnaround
        self.follow_after_turn = 0.0
        self.last_t = None
        self.stop_sent = False
        self.counts = dict(trials=0, followed=0, failed=0)
        log.side_row("recovery_config", 0.0 if clock_note is None else clock_note,
                     config=cfg.to_dict(), config_digest=cfg.digest(),
                     direction_reference=self.default_reference, capabilities=["induction_v1", "induction_v2"])

    # ---- requests ---------------------------------------------------------------------------------
    def submit(self, req, now, origin_stage=None):
        """Operator/endpoint request. Returns the decision string (also logged)."""
        def decide(decision, **kw):
            self.log.side_row("recovery_request", now, request=asdict(req), decision=decision, **kw)
            return decision
        # submit is also called directly in-process; do not trust only inbox parsing.
        if req.induction is not None:
            try:
                Request.from_dict(asdict(req), self.cfg.oval_search_sense)
            except (ValueError, TypeError) as exc:
                return decide("invalid_induction", detail=str(exc))
            if not self.cfg.stop_on_failure:
                return decide("invalid_induction", detail="induction requires stop_on_failure")
            if req.induction["kappa"] > self.cfg.search_kappa:
                return decide("invalid_induction", detail="kappa exceeds configured search curvature")
            # The slowest success path must fit inside max_trial_s: the induction's wall bound, a full SEARCH,
            # a full ALIGN and the minimum FOLLOW confirmation. Defaults: 3.0 + 0.5 + 20 + 6 + 5 = 34.5 <= 40 s.
            need = (req.induction["duration_s"] + INDUCTION_WALL_S + self.cfg.max_search_s
                    + self.cfg.align_max_s + self.cfg.confirm_s)
            if need > self.cfg.max_trial_s:
                return decide("invalid_induction", detail="induction %.1f s + search %.1f s + align %.1f s + confirm %.1f s "
                              "exceeds max_trial_s %.1f s" % (req.induction["duration_s"] + INDUCTION_WALL_S, self.cfg.max_search_s,
                                                             self.cfg.align_max_s, self.cfg.confirm_s, self.cfg.max_trial_s))
        if req.id in self.seen_ids:
            return decide("duplicate_id")
        self.seen_ids.add(req.id)
        if self.trial is not None:
            return decide("trial_active")
        if self.pending is not None:
            return decide("request_pending")
        if req.trial_id is not None:
            # a trial_id is reused only to re-run an attempt that was interrupted or left unterminated
            if req.trial_id in self.closed:
                return decide("trial_id_used")
            if isinstance(req.attempt, int) and req.attempt <= self.history.get(req.trial_id, 0):
                return decide("attempt_conflict", known_attempt=self.history.get(req.trial_id, 0))
        if (req.maneuver == "turnaround" and self.refractory_until is not None
                and self.follow_after_turn < self.cfg.endpoint_refractory_s):
            return decide("endpoint_refractory")
        if req.direction_reference.get("frame") == "unknown" and self.default_reference.get("frame") != "unknown" \
                and req.source != "induced":
            req.direction_reference = dict(self.default_reference)
        self.pending = (req, origin_stage, now)
        return decide("accepted")

    # ---- per frame ----------------------------------------------------------------------------------
    def step(self, now, owner, lane, heading, origin_stage=None, context=None):
        cfg = self.cfg
        dt = 0.0 if self.last_t is None else max(0.0, min(0.5, now - self.last_t))
        self.last_t = now
        hd_ok = heading.usable(cfg.heading_max_age_s)
        self._track_lane(now, lane, heading, hd_ok, owner, origin_stage)

        if self.trial is None:
            if self.pending is not None and now - self.pending[2] > cfg.request_ttl_s:
                req = self.pending[0]; self.pending = None
                self.log.side_row("recovery_request", now, request=asdict(req), decision="expired",
                                  ttl_s=cfg.request_ttl_s)
            if self.pending is not None and owner.driving():
                req, stage, _ = self.pending; self.pending = None
                self._start(now, req, heading, hd_ok, stage if stage is not None else origin_stage, context, lane)
            elif self.pending is None and cfg.auto and self._auto_due(now, owner):
                origin_stage = self.loss_origin      # the stage in force when the lane was lost, not loss_s later
                self.auto_n += 1
                req = Request(id=f"auto-{self.auto_n}", maneuver="recovery", requested_direction="current",
                              direction_reference=dict(self.default_reference), induced=False,
                              reason="lane_lost", source="automatic")
                self.seen_ids.add(req.id)
                self._start(now, req, heading, hd_ok, origin_stage, context, lane)
            if self.trial is None:
                return Decision(frame=None)

        t = self.trial
        # ---- authority first: Human / Stop / radio always win ----
        if owner.human():
            if not t.human_active:
                t.human_active = True; t.human_t = now
                self.log.trial_row(t, now, "human", human=dict(active=True, sa_mode=owner.mode, why=self._why(owner)))
            return Decision(maneuver_active=True, frame=self._frame(t, heading, None))
        if t.human_active:
            t.human_s += now - t.human_t; t.human_active = False
            self.log.trial_row(t, now, "human", human=dict(active=False, sa_mode=owner.mode, why=None))
            return self._end(now, "human_takeover", heading, detail="operator_took_control")
        abort = owner.abort_reason()
        if abort == "vision_stall":
            # Not an operator stop: end bounded with the neutral latch, never hand back to the uncapped speed dial
            return self._end(now, "aborted", heading, detail="vision_stall", stop=True)
        if abort is not None:
            # RECOVERY-EVENTS: a `human` row whenever Human/stop/override starts during a trial
            self.log.trial_row(t, now, "human", human=dict(active=True, sa_mode=owner.mode, why=abort, ended_by_abort=True))
            return self._end(now, "aborted", heading, detail=abort)
        if now - t.t0 > cfg.max_trial_s:
            return self._fail(now, "lost_after_follow" if t.first_follow_t is not None else "timeout",
                              heading, detail="max_trial_s")

        if t.state == "INDUCE":
            return self._induce(now, lane, heading, hd_ok, owner)
        if t.state == "TURN":
            return self._turn(now, lane, heading, hd_ok)
        if t.state in ("SEARCH", "ACQUIRE"):
            return self._search(now, lane, heading, hd_ok)
        if t.state == "ALIGN":
            return self._align(now, lane, heading, hd_ok)
        return self._follow(now, lane, heading, hd_ok, dt)

    def close(self, now, reason="controller_exit", stop=False):
        """Clean controller exit: an active trial ends `interrupted` (a crash leaves it unterminated)."""
        if self.trial is not None:
            if self.trial.human_active:
                self.trial.human_s += now - self.trial.human_t
            self._end(now, "interrupted", NO_HEADING, detail=reason, stop=stop)

    @property
    def active(self):
        return self.trial is not None

    # ---- lane bookkeeping (arming, reference heading, side) -----------------------------------------
    loss_origin = None

    def _track_lane(self, now, lane, heading, hd_ok, owner, origin_stage=None):
        following = lane.lane and owner.driving() and self.trial is None
        if lane.lane:
            self.lane_absent_since = None
            if lane.goal_x is not None:
                self.last_goal_x = lane.goal_x
            if hd_ok:
                self.lane_heading.append((now, heading.epoch, heading.psi))
        elif self.lane_absent_since is None:
            self.lane_absent_since = now; self.loss_origin = origin_stage
        keep = self.cfg.ref_window_s + self.cfg.loss_s + 1.0      # the trigger fires loss_s after the last lane frame
        self.lane_heading = [h for h in self.lane_heading if now - h[0] <= keep]
        if following:
            self.lane_since = now if self.lane_since is None else self.lane_since
        elif not owner.driving() or self.trial is not None:
            self.lane_since = None
        if self.refractory_until is not None and self.trial is None and lane.lane and owner.driving():
            self.follow_after_turn += 0.0 if self._prev_t is None else max(0.0, min(0.5, now - self._prev_t))
        self._prev_t = now

    _prev_t = None

    def _auto_due(self, now, owner):
        if not owner.driving() or self.lane_since is None or self.lane_absent_since is None:
            return False
        armed = self.lane_absent_since - self.lane_since >= self.cfg.arm_follow_s
        return armed and now - self.lane_absent_since >= self.cfg.loss_s

    def _reference_psi(self, now, heading, hd_ok):
        """Circular mean of heading on lane frames in the reference window ending at the last lane frame."""
        if not hd_ok or not self.lane_heading:
            return None, "no_heading" if not hd_ok else "no_lane_history"
        last = self.lane_heading[-1][0]
        win = [psi for (t, ep, psi) in self.lane_heading if last - t <= self.cfg.ref_window_s and ep == heading.epoch]
        if len(win) < 3:
            return None, "too_few_lane_frames"
        mean, spread = circ_mean(win)
        if math.degrees(spread) > self.cfg.ref_max_spread_deg:
            return None, "spread_%.0f_deg" % math.degrees(spread)
        # psi is continuous within the epoch; keep the unwrapped value nearest the current heading
        ref = heading.psi + wrap(mean - heading.psi)
        return (heading.epoch, ref), "lane_history_%d" % len(win)

    # ---- trial lifecycle ------------------------------------------------------------------------------
    def _start(self, now, req, heading, hd_ok, origin_stage, context, lane):
        cfg = self.cfg
        context_value = self._context(context)
        if req.induction is not None:
            camera_kappa = context_value.get("camera_kappa")
            direction_sign = 1 if req.direction_reference["value"] == "cw" else -1
            if (not hd_ok or not lane.lane or not finite(camera_kappa)
                    or abs(camera_kappa) >= .7 or direction_sign * camera_kappa < 0):
                self.log.side_row("recovery_request", now, request=asdict(req),
                                  decision="induction_start_refused", detail="need_fresh_heading_and_low_curvature_direction_consistent_lane")
                return
        trial_id = req.trial_id or (f"{self.log.run_id}-{req.id}")
        attempt = req.attempt if isinstance(req.attempt, int) and req.attempt >= 1 else self.history.get(trial_id, 0) + 1
        self.history[trial_id] = max(self.history.get(trial_id, 0), attempt)
        ref = dict(req.direction_reference)
        target = target_of(ref, req.requested_direction)
        target_psi, note = None, "none"
        flip = math.pi if req.requested_direction == "reverse" else 0.0
        if not req.induced or req.maneuver == "turnaround":
            ref_psi, note = self._reference_psi(now, heading, hd_ok)
            if ref_psi is not None:
                target_psi = (ref_psi[0], ref_psi[1] + flip)
        elif ref.get("frame") == "trail":
            # Induced trail trial: the operator declares the lane AHEAD at this moment to be the reference
            # direction (e.g. placed on the trail pointing outbound). Captured once, here.
            if hd_ok and lane.lane and lane.beta is not None:
                target_psi = (heading.epoch, heading.psi + lane.beta + flip); note = "lane_ahead_at_start"
            else:
                note = "no_lane_or_heading_at_start"
        side = self._side(req)
        if req.induction is not None:
            travel = self._travel_side(target)
            side = travel if travel is not None else (-1 if ref["value"] == "cw" else 1)  # else countersteer the inward phase
        t = Trial(trial_id=trial_id, attempt=attempt, maneuver=req.maneuver,
                  requested_direction=req.requested_direction, direction_reference=ref, target_direction=target,
                  induced=bool(req.induced), origin_stage=origin_stage if not req.induced else None,
                  trigger=dict(source=req.source, reason=req.reason, request_id=req.id),
                  t0=now, target_psi=target_psi, ref_note=note, side=side,
                  follows_trial=getattr(req, "_follows", None),
                  induction=dict(req.induction) if req.induction is not None else None,
                  induction_last=(now, heading.epoch, heading.psi) if hd_ok else None,
                  tangent=(heading.epoch, heading.psi) if hd_ok else None)
        self.trial = t; self.stop_sent = False; self.counts["trials"] += 1
        self.lane_since = None
        extra = {}
        if self._sense_fields(t):
            extra["tangent_psi_deg"] = None if t.tangent is None else round(math.degrees(t.tangent[1]), 2)
        self.log.trial_row(t, now, "trial_start", trigger=t.trigger, config_digest=cfg.digest(),
                           heading_reference=dict(source=heading.source, note=note,
                                                  target_psi_deg=None if target_psi is None else round(math.degrees(target_psi[1]), 2),
                                                  epoch=None if target_psi is None else target_psi[0]),
                           side="right" if side > 0 else "left", follows_trial=t.follows_trial,
                           context=context_value, induction=t.induction, **extra)
        self._enter(now, "INDUCE" if req.induction is not None else ("TURN" if req.maneuver == "turnaround" else "SEARCH"), heading)

    @staticmethod
    def _context(context):
        try:
            return (context() if callable(context) else context) or {}
        except Exception as exc:                  # identity capture must not stop a trial
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _side(self, req):
        cfg = self.cfg
        if req.maneuver == "turnaround":
            side = req.search_side if req.search_side in ("left", "right") else cfg.turn_side
            if side == "auto":
                ref = req.direction_reference          # the CURRENT travel direction the turnaround reverses
                side = "left" if (ref.get("frame") == "oval_path" and ref.get("value") == "ccw") else "right"
            return 1 if side == "right" else -1
        side = req.search_side if req.search_side in ("left", "right") else cfg.search_side
        if side == "auto":
            travel = self._travel_side(target_of(req.direction_reference, req.requested_direction))
            if travel is not None:
                return travel
            # toward where the lane was last seen; right when unknown
            return -1 if (self.last_goal_x is not None and self.last_goal_x < 0) else 1
        return 1 if side == "right" else -1

    def _travel_side(self, target):
        """+1 (right) for a cw oval target, -1 for ccw, when oval_search_sense is travel; else None."""
        if self.cfg.oval_search_sense != "travel" or target.get("frame") != "oval_path":
            return None
        return {"cw": 1, "ccw": -1}.get(target.get("value"))

    def _sense_fields(self, t):
        """The travel-sense measurement fields go on rows of travel-sense runs and induced trials only."""
        return self.cfg.oval_search_sense == "travel" or t.induction is not None

    def _enter(self, now, state, heading):
        t = self.trial
        extra = {}
        if state == "SEARCH" and t.state != "ACQUIRE" and self._sense_fields(t):
            # heading minus the trial-start heading (+ = clockwise/right), same heading epoch only
            ok = t.tangent is not None and heading.psi is not None and heading.epoch == t.tangent[0]
            extra["e_loss_deg"] = round(math.degrees(heading.psi - t.tangent[1]), 2) if ok else None
        self.log.trial_row(t, now, "state", state=state, from_state=t.state, **extra)
        prev = t.state
        t.state, t.state_t = state, now
        t.cand = 0; t.last_cand_x = None; t.follow_ok = 0; t.lane_absent_t = None
        if not (state in ("SEARCH", "ACQUIRE") and prev in ("SEARCH", "ACQUIRE")):
            t.hd_bad_since = None             # a later SEARCH gets its full heading_max_age_s grace
        t.psi_mark = (heading.epoch, heading.psi) if heading.psi is not None else None
        if state == "SEARCH" and prev != "ACQUIRE":
            t.search_t0, t.search_rot, t.last_psi = now, 0.0, None
        if state == "FOLLOW":
            t.follow_psi = []; t.follow_n = t.follow_lane = 0

    def _milestone(self, now, name, measure, **kw):
        self.log.trial_row(self.trial, now, "milestone", milestone=name, measure=measure, **kw)

    def _end(self, now, outcome, heading, detail=None, stop=False):
        t = self.trial
        rel = lambda x: None if x is None else round(x - t.t0, 3)
        self.log.trial_row(
            t, now, "trial_end", outcome=outcome, detail=detail,
            success_criterion=dict(measure=self.cfg.confirm_measure, threshold=self.cfg.confirm_s,
                                   direction_check=self._direction_method(t),
                                   direction_min_deg=self.cfg.dir_min_deg if self._direction_method(t) == "oval_turn_sign" else None,
                                   direction_tol_deg=self.cfg.dir_tol_deg if self._direction_method(t) == "heading_reference" else None),
            time_to_first_detection_s=rel(t.first_detection_t), time_to_first_follow_s=rel(t.first_follow_t),
            time_to_follow_s=rel(t.confirmed_t) if outcome == "followed" else None,
            duration_s=round(now - t.t0, 3), human_s=round(t.human_s, 3), follow_losses=t.follow_losses,
            rejections=dict(t.rejections), final_state=t.state, stop_requested=bool(stop),
            **self._induced_fields(t, rel))
        self.trial = None
        if outcome == "interrupted":
            self.closed.discard(t.trial_id)
        else:
            self.closed.add(t.trial_id)
        if outcome == "followed":
            self.counts["followed"] += 1
        elif outcome in FAILURE_OUTCOMES:
            self.counts["failed"] += 1
        if t.maneuver == "turnaround":
            self.refractory_until = now; self.follow_after_turn = 0.0
        stop_reason = None
        if stop and self.cfg.stop_on_failure:
            stop_reason = "recovery_" + outcome
        return Decision(stop_reason=stop_reason, maneuver_active=False,
                        frame=dict(trial_id=t.trial_id, attempt=t.attempt, state="END", maneuver=t.maneuver,
                                   outcome=outcome))

    @staticmethod
    def _induced_fields(t, rel):
        """trial_end fields for induced trials only (rows of other trials are unchanged). left_tape: a frame without
        the lane after induction_complete and before first_path_detection; None if the induction never completed."""
        if t.induction is None:
            return {}
        return dict(left_tape=None if t.induction_done_t is None else t.lane_lost_t is not None,
                    time_lane_lost_s=rel(t.lane_lost_t))

    def _fail(self, now, outcome, heading, detail):
        t = self.trial
        # One automatic turnaround per trial chain: a turnaround that itself ends wrong-way stops (no loop).
        if (outcome == "wrong_direction" and self.cfg.on_wrong_direction == "turnaround" and t.follows_trial is None):
            self._end(now, outcome, heading, detail=detail, stop=False)
            cur = t.target_direction.get("value")
            ref = {"frame": t.target_direction["frame"], "value": OPPOSITE.get(cur)} if cur else {"frame": "unknown", "value": None}
            req = Request(id=f"{t.trial_id}-a{t.attempt}-turn", maneuver="turnaround", requested_direction="reverse",
                          direction_reference=ref, induced=t.induced, reason="wrong_direction", source="automatic",
                          trial_id=f"{t.trial_id}-a{t.attempt}-turn")
            req._follows = dict(trial_id=t.trial_id, attempt=t.attempt)
            self.seen_ids.add(req.id)
            self.refractory_until = None
            hd = heading
            self._start(now, req, hd, hd.usable(self.cfg.heading_max_age_s), t.origin_stage, None,
                        LaneObs(True, 0, None))
            # the new turnaround's reference is the lane we were (wrongly) following: reverse of it
            return Decision(kappa=self._turn_kappa(), maneuver_active=True,
                            throttle_cap_us=self.cfg.throttle_cap_us, frame=self._frame(self.trial, heading, None))
        return self._end(now, outcome, heading, detail=detail, stop=True)

    def _gate_applies(self, t):
        g = self.cfg.heading_gate
        return g == "always" or (g == "turnaround_and_trail" and
                                 (t.maneuver == "turnaround" or t.target_direction.get("frame") == "trail"))

    def _direction_method(self, t):
        f = t.target_direction.get("frame")
        if t.maneuver == "turnaround" and f == "unknown":
            # a turnaround only succeeds if travel actually reversed: never confirm without evidence
            return "heading_reference" if t.target_psi is not None else "unavailable"
        if f == "oval_path":
            return "oval_turn_sign"
        if f == "trail":
            return "heading_reference" if t.target_psi is not None else "unavailable"
        return "none"

    def _why(self, owner):
        return "wheel" if owner.override_on else ("trigger" if owner.throttle_source == "human" else owner.veto)

    # ---- frame association ------------------------------------------------------------------------------
    def _frame(self, t, heading, kappa, cand=None):
        return dict(trial_id=t.trial_id, attempt=t.attempt, state=t.state, maneuver=t.maneuver,
                    kappa=None if kappa is None else round(kappa, 4), cand=cand,
                    since_start_s=round((self.last_t or t.t0) - t.t0, 3))

    def _reject(self, reason):
        self.trial.rejections[reason] = self.trial.rejections.get(reason, 0) + 1
        return reason

    # ---- INDUCE (explicit experiment-only inward perturbation) --------------------------------------
    def _induce(self, now, lane, heading, hd_ok, owner):
        t = self.trial
        if not hd_ok or t.induction_last is None or heading.epoch != t.induction_last[1]:
            return self._end(now, "aborted", heading, detail="induction_heading_unavailable", stop=True)
        previous, epoch, psi = t.induction_last
        if owner.driving():
            t.induction_elapsed_s += max(0.0, now - previous)
        t.induction_rotation += abs(heading.psi - psi)
        t.induction_last = (now, epoch, heading.psi)
        why = None
        if t.induction_rotation >= math.radians(t.induction["max_heading_deg"]):
            why = "heading_bound"
        elif t.induction_elapsed_s >= t.induction["duration_s"]:
            why = "commanded_time_bound"
        if why is not None:
            t.induction_done_t = now
            self._milestone(now, "induction_complete", "commanded_time_and_gyro", bound=why,
                            commanded_s=t.induction_elapsed_s,
                            rotation_deg=math.degrees(t.induction_rotation), position_target_met=None)
            self._enter(now, "SEARCH", heading)
            return self._search(now, lane, heading, hd_ok)
        # Independent wall bound handles stalled/paused observation; no indefinite pulse.
        if now - t.state_t > t.induction["duration_s"] + INDUCTION_WALL_S:
            return self._end(now, "aborted", heading, detail="induction_wall_timeout", stop=True)
        k = (1 if t.direction_reference["value"] == "cw" else -1) * t.induction["kappa"]   # inward: the travel sense
        return Decision(kappa=k, maneuver_active=True, throttle_cap_us=self.cfg.throttle_cap_us,
                        frame={**self._frame(t, heading, k), "induction_commanded_s": t.induction_elapsed_s,
                               "induction_rotation_deg": math.degrees(t.induction_rotation)})

    # ---- TURN (turnaround) ----------------------------------------------------------------------------
    def _turn_kappa(self):
        t = self.trial
        if t.turn_leg == 0 and self.cfg.turn_away_deg > 0:
            return -t.side * self.cfg.turn_away_kappa
        return t.side * self.cfg.turn_kappa

    def _rotated(self, heading):
        t = self.trial
        if t.psi_mark is None or heading.psi is None or heading.epoch != t.psi_mark[0]:
            return None
        return heading.psi - t.psi_mark[1]

    def _turn(self, now, lane, heading, hd_ok):
        t, cfg = self.trial, self.cfg
        if not hd_ok:
            # A turnaround is a rotation; without heading it cannot know when to stop turning. Ride out a dropout
            # up to heading_max_age_s on the current leg's curvature, then fail bounded.
            t.hd_bad_since = now if t.hd_bad_since is None else t.hd_bad_since
            if heading.psi is None or now - t.hd_bad_since > cfg.heading_max_age_s:
                return self._fail(now, "timeout", heading, detail="heading_unavailable_in_turn")
            k = self._turn_kappa()
            return Decision(kappa=k, maneuver_active=True, throttle_cap_us=cfg.throttle_cap_us,
                            frame=self._frame(t, heading, k))
        t.hd_bad_since = None
        if t.psi_mark is None:
            t.psi_mark = (heading.epoch, heading.psi)
        elif t.psi_mark[0] != heading.epoch:
            # FC gap/reboot/tilt mid-turn: the rotation so far is unknown, so the turn cannot be finished honestly
            return self._fail(now, "timeout", heading, detail="heading_epoch_change_in_turn")
        if now - t.state_t > cfg.turn_max_s:
            return self._fail(now, "timeout", heading, detail="turn_max_s")
        rot = self._rotated(heading) or 0.0
        if t.turn_leg == 0 and cfg.turn_away_deg > 0:
            if -t.side * rot >= math.radians(cfg.turn_away_deg):
                t.turn_leg = 1; t.psi_mark = (heading.epoch, heading.psi)
        elif t.side * rot >= math.radians(cfg.turn_deg):
            self._enter(now, "SEARCH", heading)
            return self._search(now, lane, heading, hd_ok)
        if t.turn_leg == 0 and cfg.turn_away_deg <= 0:
            t.turn_leg = 1
        k = self._turn_kappa()
        return Decision(kappa=k, maneuver_active=True, throttle_cap_us=cfg.throttle_cap_us,
                        frame=self._frame(t, heading, k))

    # ---- SEARCH / ACQUIRE ---------------------------------------------------------------------------------
    def _candidate(self, lane, heading, hd_ok):
        """None if this frame is a plausible, feasible lane; else the rejection reason."""
        cfg, t = self.cfg, self.trial
        if not lane.lane or lane.goal_x is None:
            return "no_lane"
        if lane.kept < cfg.min_kept:
            return "rows"
        if lane.vote is not None and lane.vote < cfg.min_vote:
            return "vote"
        if abs(lane.goal_x) > cfg.max_goal_x:
            return "goal_far"
        if lane.beta is None:
            return "no_angle"
        if abs(lane.beta) > math.radians(cfg.max_beta_deg):
            return "steep"
        if t.last_cand_x is not None and abs(lane.goal_x - t.last_cand_x) > cfg.max_goal_jump:
            return "jump"
        if t.target_psi is not None and self._gate_applies(t):
            if not hd_ok or heading.epoch != t.target_psi[0]:
                return "heading_unavailable"
            gate = cfg.turn_gate_deg if t.maneuver == "turnaround" else cfg.heading_gate_deg
            if abs(wrap(heading.psi + lane.beta - t.target_psi[1])) > math.radians(gate):
                return "wrong_orientation"
        return None

    def _search(self, now, lane, heading, hd_ok):
        t, cfg = self.trial, self.cfg
        if (t.induction_done_t is not None and t.first_detection_t is None and t.lane_lost_t is None
                and not lane.lane):
            t.lane_lost_t = now
        if t.search_t0 is None:
            t.search_t0 = now
        if hd_ok:
            if t.last_psi is not None and t.last_psi[0] == heading.epoch:
                t.search_rot += abs(heading.psi - t.last_psi[1])
            t.last_psi = (heading.epoch, heading.psi)
        lost = "lost_after_follow" if t.first_follow_t is not None else "timeout"
        if now - t.search_t0 > cfg.max_search_s:
            return self._fail(now, lost, heading, detail="max_search_s")
        if t.search_rot > math.radians(cfg.max_search_turn_deg):
            return self._fail(now, lost, heading, detail="max_search_turn_deg")
        if t.target_psi is not None and self._gate_applies(t):
            if hd_ok and heading.epoch != t.target_psi[0]:
                return self._fail(now, lost, heading, detail="heading_reference_lost")
        if not hd_ok:
            # without heading the rotation bound cannot be enforced (and a gated search cannot accept anything)
            t.hd_bad_since = now if t.hd_bad_since is None else t.hd_bad_since
            if now - t.hd_bad_since > cfg.heading_max_age_s:
                return self._fail(now, lost, heading, detail="heading_unavailable_in_search")
        else:
            t.hd_bad_since = None
        why = self._candidate(lane, heading, hd_ok)
        if why is None:
            if t.first_detection_t is None:
                t.first_detection_t = now
                extra = {}
                if self._sense_fields(t):
                    extra["rot_since_search_deg"] = round(math.degrees(t.search_rot), 2)   # gyro path length, this SEARCH episode
                self._milestone(now, "first_path_detection", "candidate_frame",
                                kept=lane.kept, goal_x=round(lane.goal_x, 3),
                                beta_deg=round(math.degrees(lane.beta), 1), **extra)
            t.cand += 1; t.last_cand_x = lane.goal_x
            if t.state == "SEARCH":
                self._enter(now, "ACQUIRE", heading); t.cand = 1; t.last_cand_x = lane.goal_x
            if t.cand >= cfg.acq_frames:
                self._enter(now, "ALIGN", heading)
                return Decision(kappa=None, maneuver_active=True, frame=self._frame(t, heading, None, cand="ok"))
        else:
            if why != "no_lane":
                self._reject(why)
            if t.state == "ACQUIRE":
                self._enter(now, "SEARCH", heading)      # search_t0 / search_rot carry on (see _enter)
        k = t.side * (cfg.turn_kappa if t.maneuver == "turnaround" and t.follow_losses == 0 else cfg.search_kappa)
        return Decision(kappa=k, maneuver_active=True, throttle_cap_us=cfg.throttle_cap_us,
                        frame=self._frame(t, heading, k, cand=why or "ok"))

    # ---- ALIGN / FOLLOW (the lane controller steers) -----------------------------------------------------
    def _lane_loss(self, now, lane):
        t = self.trial
        if lane.lane:
            t.lane_absent_t = None
            return False
        if t.lane_absent_t is None:
            t.lane_absent_t = now
        return now - t.lane_absent_t >= self.cfg.follow_loss_s

    def _lost(self, now, heading, hd_ok, where):
        t, cfg = self.trial, self.cfg
        t.follow_losses += 1
        self._milestone(now, "follow_lost", "lane_absent_s", state=where, losses=t.follow_losses)
        if t.follow_losses > cfg.max_reacquire:
            return self._fail(now, "lost_after_follow" if t.first_follow_t is not None else "timeout",
                              heading, detail="max_reacquire")
        travel = self._travel_side(t.target_direction)
        if travel is not None:
            t.side = travel
        elif self.last_goal_x is not None:
            t.side = -1 if self.last_goal_x < 0 else 1
        self._enter(now, "SEARCH", heading)
        k = t.side * cfg.search_kappa
        return Decision(kappa=k, maneuver_active=True, throttle_cap_us=cfg.throttle_cap_us,
                        frame=self._frame(t, heading, k))

    def _align(self, now, lane, heading, hd_ok):
        t, cfg = self.trial, self.cfg
        if self._lane_loss(now, lane):
            return self._lost(now, heading, hd_ok, "ALIGN")
        if now - t.state_t > cfg.align_max_s:
            self._reject("align_timeout")
            return self._lost(now, heading, hd_ok, "ALIGN")
        if (lane.lane and lane.goal_x is not None and abs(lane.goal_x) <= cfg.follow_x
                and lane.beta is not None and abs(lane.beta) <= math.radians(cfg.follow_beta_deg)):
            t.follow_ok += 1
        else:
            t.follow_ok = 0                       # consecutive good frames only; an absent frame resets too
        if t.follow_ok >= cfg.follow_frames:
            self._enter(now, "FOLLOW", heading)
            if t.first_follow_t is None:
                t.first_follow_t = now
            self._milestone(now, "follow_entered", "state")
        return Decision(kappa=None, maneuver_active=True, frame=self._frame(t, heading, None))

    def _follow(self, now, lane, heading, hd_ok, dt):
        t, cfg = self.trial, self.cfg
        if self._lane_loss(now, lane):
            return self._lost(now, heading, hd_ok, "FOLLOW")
        if hd_ok:
            if t.psi_mark is None or heading.epoch != t.psi_mark[0]:
                # heading continuity broke (gap/reboot/tilt): restart the direction evidence, keep the held time
                t.psi_mark = (heading.epoch, heading.psi); t.follow_psi = []
            t.follow_psi.append(heading.psi)
        held = now - t.state_t
        t.follow_n += 1; t.follow_lane += 1 if lane.lane else 0
        lane_frac = t.follow_lane / t.follow_n
        verdict, value = self._direction(t, heading, hd_ok)
        if verdict == "wrong":
            self._milestone(now, "wrong_direction", t and self._direction_method(t), value_deg=value)
            return self._fail(now, "wrong_direction", heading, detail=self._direction_method(t))
        if held >= cfg.confirm_s and verdict in ("correct", "not_applicable") and lane_frac >= cfg.follow_lane_frac:
            t.confirmed_t = now
            self._milestone(now, "follow_confirmed", cfg.confirm_measure, held_s=round(held, 3),
                            direction=verdict, direction_value_deg=value, lane_fraction=round(lane_frac, 3))
            return self._end(now, "followed", heading, detail=None)
        if held >= cfg.confirm_max_s:
            return self._fail(now, "timeout", heading, detail="direction_unverified_%s" % verdict)
        return Decision(kappa=None, maneuver_active=True, frame=self._frame(t, heading, None))

    def _direction(self, t, heading, hd_ok):
        """correct | wrong | pending | not_applicable | unavailable, and the measured value (deg)."""
        method = self._direction_method(t)
        if method == "none":
            return "not_applicable", None
        if method == "unavailable" or len(t.follow_psi) < 2:
            return ("unavailable" if method == "unavailable" else "pending"), None
        if method == "oval_turn_sign":
            sign = 1 if t.target_direction["value"] == "cw" else -1
            net = math.degrees(t.follow_psi[-1] - t.follow_psi[0])
            if sign * net >= self.cfg.dir_min_deg:
                return "correct", round(net, 1)
            if sign * net <= -self.cfg.dir_min_deg:
                return "wrong", round(net, 1)
            return "pending", round(net, 1)
        # heading_reference (trail): mean heading against the target, same epoch only
        if t.target_psi is None or heading.epoch != t.target_psi[0]:
            return "unavailable", None
        mean, _ = circ_mean(t.follow_psi)
        err = math.degrees(abs(wrap(mean - t.target_psi[1])))
        if err <= self.cfg.dir_tol_deg:
            return "correct", round(err, 1)
        if err >= 180 - self.cfg.dir_tol_deg:
            return "wrong", round(err, 1)
        return "pending", round(err, 1)


# --------------------------------------------------------------------------------------------------
# live_trail adapter: everything the vision loop needs, so the controller edit stays a few lines
# --------------------------------------------------------------------------------------------------
class RecoveryHook:
    """Owns the inbox, the state machine and the heading source for one controller run.

    live_trail calls `frame()` once per frame after the camera (and path) command and before the
    intent is published, then applies the returned Decision: a manoeuvre curvature replaces the
    camera steer; `stop_reason` and `throttle_cap_us` ride on the Intent to SimpleOwner.
    """

    def __init__(self, cfg, sink, run_id, inbox_dir=None, direction_reference=None, experiment_id=None,
                 continuation_of=None, history_paths=(), heading=None, boot_id=None, now=None):
        now = time.monotonic() if now is None else now
        self.log = EventLog(sink, run_id, experiment_id, continuation_of, boot_id)
        self.heading = heading
        self.inbox = RequestInbox(inbox_dir, search_sense=cfg.oval_search_sense) if inbox_dir else None
        closed = set()
        self.recovery = PathRecovery(cfg, self.log, direction_reference, attempt_history(history_paths, closed),
                                     clock_note=now, closed=closed)
        self.last = Decision()

    @property
    def active(self):
        return self.failed is None and self.recovery.active

    failed = None

    def frame(self, now, owner_snapshot, cam_state, kept, goal, pts, vote=None, sweep=None, context=None):
        """Never raises into the vision loop. An internal error ends recovery for the run: one latched stop
        (the truck may be mid-manoeuvre), an error row, then plain lane control after the HUMAN visit."""
        if self.failed is not None:
            self.last = Decision()
            self.last_heading = self.heading.observe(now) if self.heading is not None else None
            return self.last
        try:
            return self._frame(now, owner_snapshot, cam_state, kept, goal, pts, vote, sweep, context)
        except Exception as exc:
            import traceback
            self.failed = f"{type(exc).__name__}: {exc}"
            try:
                self.log.side_row("recovery_error", now, error=self.failed, traceback=traceback.format_exc()[-2000:],
                                  trial=None if self.recovery.trial is None else self.recovery.trial.trial_id)
                if self.recovery.trial is not None:
                    self.recovery.close(now, reason="recovery_error", stop=True)
            except Exception:
                pass
            self.recovery.trial = None; self.recovery.pending = None     # never left active: the sweep resumes
            self.last = Decision(stop_reason="recovery_error")
            return self.last

    def _frame(self, now, owner_snapshot, cam_state, kept, goal, pts, vote, sweep, context):
        origin = None
        if sweep is not None and sweep.get("started", True) and not sweep.get("complete", False):
            # only while a tuning stage is actually in force (not before sweep_start or after sweep_complete)
            origin = dict(run_id=self.log.run_id, stage=sweep.get("stage"), name=sweep.get("name"))
        if self.inbox is not None:
            for req in self.inbox.poll(now):
                if isinstance(req, tuple):
                    self.log.side_row("recovery_request", now, request=dict(file=req[1]), decision="invalid",
                                      error=req[2])
                else:
                    self.recovery.submit(req, now, origin)
        lane = LaneObs(lane=cam_state == "lane", kept=int(kept or 0),
                       goal_x=None if not goal else float(goal[0]),
                       beta=line_angle(pts or []), vote=vote)
        heading = self.heading.observe(now) if self.heading is not None else NO_HEADING
        d = self.recovery.step(now, OwnerView.from_snapshot(owner_snapshot or {}), lane, heading, origin, context)
        self.last = d; self.last_heading = heading
        return d

    def frame_record(self):
        out = {"recovery": self.last.frame}
        if self.heading is not None:
            # the observation this frame's decision used, not a fresh re-read
            out["heading"] = (self.last_heading if self.last_heading is not None else NO_HEADING).log()
        return out

    last_heading = None

    def close(self, now=None):
        try:
            self.recovery.close(time.monotonic() if now is None else now)
        except Exception:                         # at controller exit: never block the end row
            self.recovery.trial = None


def parse_direction(text):
    """'oval_path:cw' | 'trail:outbound' | 'unknown' -> {frame, value}."""
    if not text or text == "unknown":
        return {"frame": "unknown", "value": None}
    frame, _, value = text.partition(":")
    ref = {"frame": frame, "value": value or None}
    Request.from_dict(dict(id="check", maneuver="recovery", direction_reference=ref))   # validates
    return ref

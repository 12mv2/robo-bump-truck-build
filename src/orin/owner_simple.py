# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""SimpleOwner — the one-switch output owner (operator's ruling 2026-09-30 21:4x: "it's a 1/10 RC car").

The mode switch is the mode. The kill dial is the kill (at the FC; here it only releases). The speed dial is the speed. The wheel and the trigger
override while touched. Nothing else: no start gesture, no --start-armed, no permission epoch, no cruise loop,
no legacy recovery, no batch cells, no steering-source dial.
Opt-in Path Recovery adds a neutral latch until the mode switch is at HUMAN and an optional downward speed-dial cap.
Source labels are observational; no external-planner authority or full-forward profile is imported.

Mode-switch band (from the FC's RC_CHANNELS)        mode     what this thread sends
  far end   <= 1230                             HUMAN    nothing (0, 0): the receiver has both channels
  middle    1361..1620                          ASSIST   steering = the steering head (wheel overrides while touched); throttle = the speed dial when the trigger is neutral
  near end  >= 1750                             STOP     nothing; today the FC is in HOLD in this band  (--sa-near auto: same as ASSIST, reported as AUTO)
  between                                       release  nothing

Releases (0, 0) on: stale RC (> rc_max_age_ms), stale heartbeat, kill dial engaged (>= 1800), FC not armed, FC not in
MANUAL, mode switch between bands. A trigger pull (|trigger copy - neutral| > max(dz, 60)) releases the throttle so the trigger drives.
Same thread rules as OutputOwner: only this thread calls fc.send(); the status snapshot is published atomically.
"""
import threading, time

import math


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def channel(value):
    return finite(value) and 900 <= value <= 2100


class SimpleOwner(threading.Thread):
    def __init__(self, fc, args, sending, override_weight, receipt, rc_max_age_ms, min_band, absolute_max):
        super().__init__(daemon=True, name="simple-owner")
        self.fc, self.a, self.sending = fc, args, sending
        self.weight, self.receipt = override_weight, receipt
        self.rc_max_age_ms, self.min_band, self.absolute_max = rc_max_age_ms, min_band, absolute_max
        self.period = 1.0 / max(1.0, float(getattr(args, "wheel_hz", 20.0)))
        self.enabled = False            # live_trail sets it after the wheel-copy gate passes
        self.epoch = 0; self.sends = 0; self.ticks = 0; self.thread_error = None
        self.last_touch = -1e9
        self.recovery_stop_latched = False; self.recovery_stop_reason = None; self._pending_recovery_stop = None
        self._lock = threading.Lock(); self._intent = None; self._quit = threading.Event()
        self._status = self._base_status(state="init", cruise_why="startup", mode=None)
        self._last_event = None

    # ---- interface live_trail uses -------------------------------------------------------------
    def snapshot(self):
        with self._lock: return dict(self._status)
    def publish(self, intent):
        with self._lock:
            self._intent = intent
            reason = getattr(intent, "stop_reason", None)
            if isinstance(reason, str) and reason.startswith("recovery_") and self._pending_recovery_stop is None:
                self._pending_recovery_stop = reason
    def request_trial(self, trial): return False      # no trials in the simple owner
    def trial_state(self): return None
    def stop(self): self._quit.set()

    # ---- the decision ----------------------------------------------------------------------------
    def mode_of(self, sw):
        if not channel(sw): return None
        if sw <= 1230: return "human"
        if 1361 <= sw <= 1620: return "assist"
        if sw >= 1750: return "auto" if getattr(self.a, "sa_near", "stop") == "auto" else "stop"
        return None

    def tick(self, now=None):
        tele = self.fc.poll()
        with self._lock:
            intent = self._intent
            pending_recovery_stop = self._pending_recovery_stop; self._pending_recovery_stop = None
        now = time.monotonic() if now is None else now
        a = self.a
        neutral, dz = self.fc.thr_neutral, max(self.fc.thr_dz, self.min_band)
        rc_age, hb_age = tele.get("rc_age_ms"), tele.get("hb_age_ms")
        rc_ok = finite(rc_age) and 0 <= rc_age <= self.rc_max_age_ms
        hb_ok = finite(hb_age) and 0 <= hb_age <= 2000
        sw, stick, dial, kill = (tele.get(k) for k in ("rc3", "rc2_stick", "cruise_pwm", "rc5"))
        mode = self.mode_of(sw)
        fresh = intent is not None and finite(intent.created) and (now - intent.created) * 1000.0 <= float(getattr(a, "hold_ms", 500))
        why = None
        if not rc_ok: why = "rc_stale"
        elif not hb_ok: why = "heartbeat_stale"
        elif not channel(kill) or kill >= 1800: why = "kill"
        elif mode is None: why = "sa_between"
        elif mode == "human": why = "human"
        elif mode == "stop": why = "stop_sw"
        elif not tele.get("armed"): why = "not_armed"
        elif tele.get("mode") != "MANUAL": why = "fc_mode_" + str(tele.get("mode"))
        elif not self.enabled: why = "wheel_copy_gate"
        elif not channel(tele.get("rc8")): why = "no_wheel_copy"

        if why == "human":
            self.recovery_stop_latched = False; self.recovery_stop_reason = None
        elif pending_recovery_stop:
            self.recovery_stop_latched = True; self.recovery_stop_reason = pending_recovery_stop
        recovery_stop = why is None and self.recovery_stop_latched
        pwm = 0; throttle = 0; steer = wheel = weight = None; override_on = t3_on = False
        steer_source = "human" if why == "human" else "release"
        throttle_source = "human" if why == "human" else "release"
        t3_neutral = channel(stick) and abs(stick - neutral) <= max(dz, 60)
        trigger_pulled = channel(stick) and not t3_neutral
        if recovery_stop:
            throttle = 0 if trigger_pulled else int(neutral)
            steer_source = "human"
            throttle_source = "human" if trigger_pulled else "recovery_stop_neutral"
        elif why is None:                                 # deployed ASSIST/AUTO semantics preserved
            wheel = self.fc.steer_from_pwm(tele["rc8"])
            weight, self.last_touch = self.weight(wheel, now, self.last_touch, a.override_dz, a.override_hold_ms)
            override_on = weight == 0.0
            model = intent.steer if (fresh and finite(intent.steer)) else 0.0
            steer = weight * model + (1.0 - weight) * wheel
            pwm = self.fc.pwm(steer)
            steer_source = ("human" if override_on else (getattr(intent, "steer_source", None) or "camera") if fresh else "stale_model")
            if t3_neutral and channel(dial):
                if dial > 1100:
                    band = getattr(a, "cruise_band", None)
                    lo, hi = (int(band[0]), int(band[1])) if band else (neutral, int(a.cruise_max))
                    lvl = lo + (min(dial, 2000) - 1100) / 900.0 * (hi - lo)
                    throttle = int(max(neutral, min(a.cruise_max, self.absolute_max, round(lvl))))
                    t3_on = throttle > neutral + dz
                    throttle_source = "t3"
                else:
                    throttle = int(neutral)
                    throttle_source = "t3_bottom"
            else:
                throttle_source = "human"
            cap = getattr(intent, "throttle_cap", None) if intent is not None else None
            manoeuvre = getattr(intent, "steer_source", None) == "path_recovery"   # TURN/SEARCH/ACQUIRE curvature
            if throttle_source == "t3" and (finite(cap) or manoeuvre) and not fresh:
                throttle = int(neutral); t3_on = False; throttle_source = "stale_capped"
            elif throttle_source == "t3" and finite(cap) and throttle > cap:
                throttle = int(max(neutral, cap)); t3_on = throttle > neutral + dz
                throttle_source = "t3_capped"
            # trigger moved, or no dial: throttle stays released exactly as deployed
        state = why if why is not None else ("recovery_stop" if recovery_stop else mode)

        sent_ok = None
        if self.sending:
            self.fc.send(pwm, throttle); self.sends += 1; sent_ok = True
        self.ticks += 1
        cruise_why = "recovery_stop" if recovery_stop else ("t3" if t3_on else (why if why is not None else ("trigger" if not t3_neutral else "t3_bottom")))
        status = self._base_status(state=state, cruise_why=cruise_why, mode=mode, cruise_on=t3_on,
                                   pwm=pwm, throttle=throttle, steer=steer, blend_w=weight, wheel=wheel,
                                   override_on=override_on, sends=self.sends, ticks=self.ticks, sent_at=now,
                                   veto=why or ("recovery_stop" if recovery_stop else None), hard_veto=why, intent_created=intent.created if intent else None,
                                   model_fresh=fresh, t3_neutral=t3_neutral, alive=True,
                                   steer_source=steer_source, throttle_source=throttle_source,
                                   recovery_stop_latched=self.recovery_stop_latched, recovery_stop_reason=self.recovery_stop_reason,
                                   stop_state="recovery_latched" if recovery_stop else "n/a")
        with self._lock: self._status = status
        event = (state, cruise_why, mode, override_on, steer_source, throttle_source, self.recovery_stop_latched)
        if self.receipt is not None and event != self._last_event:
            self.receipt(dict(event="output_owner", t=time.time(), **status))
        self._last_event = event
        return status

    def _base_status(self, **kw):
        s = dict(owner="simple", epoch=0, cruise_on=False, cruise_why="startup", mode=None, state="init",
                 pwm=0, throttle=0, steer=None, blend_w=None, wheel=None, override_on=False, manual_on=False,
                 brake_latched=False, batch_cell=None, batch_phase=None, cell_seconds=None, sends=self.sends,
                 sending=self.sending, ticks=self.ticks, thread_error=self.thread_error, alive=None,
                 autonomous_permission=False, motion_epoch=0, hard_veto=None, forward_veto=None, veto=None,
                 start_ready=False, start_pending=False, stop_state="n/a", recovery_phase="idle",
                 recovery_attempts=0, pose_valid=None, terminal_reason=None, intent_created=None, sent_at=None,
                 model_fresh=False, t3_neutral=None)
        s.update(kw); return s

    def run(self):
        deadline = time.monotonic()
        try:
            while not self._quit.is_set():
                self.tick()
                deadline = max(deadline + self.period, time.monotonic())
                self._quit.wait(max(0.0, deadline - time.monotonic()))
        except BaseException as exc:
            self.thread_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if self.sending:
                self.fc.release()
            with self._lock:
                self._status = dict(self._status, cruise_on=False, sending=False, state="sender_exit",
                                    cruise_why=self.thread_error or "sender_exit", thread_error=self.thread_error, alive=False)

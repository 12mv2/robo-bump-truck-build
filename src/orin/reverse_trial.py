#!/usr/bin/env python3
# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Phase 2: ONE supervised reverse trial at a time, with one question and one result.

Server and operator client in one file so the request/result schema cannot drift.

WHY THIS EXISTS. `reverse_sweep.py` applied a whole table of amplitudes with no motion
sensing, could not stop when it had succeeded, and put the truck two feet back into a bin.
The operator, watching: "You already broke free." It also left the successful row unidentified, so
the 0.25-0.60 "break-free bracket" it produced had to be withdrawn -- it was never a
calibration. The replacement obeys four rules from the external review:

  1. ONE TRIAL, ONE QUESTION, ONE RESULT. The result is one word from a fixed vocabulary:
     `moved` / `wheelspin_observed` / `no_motion_within_bounds` / `vetoed` / `indeterminate`.
  2. NO AUTOMATIC ADVANCE. The tool never selects the next amplitude. A wheelspin observation
     ENDS that escalation and triggers review; it is not a reason to try a bigger pulse.
  3. LIMITS ARE CHOSEN BEFORE THE TRIAL. `--ceiling` is a launch-time human decision, recorded
     in every trial record, and absolutely bounded by recovery.ABS_FRACTION_CEILING.
  4. THE HUMAN SUPPLIES THE WHEEL OBSERVATION. Phone pose measures the BODY; it cannot
     distinguish a stalled drivetrain from wheels spinning under a stationary truck, and no
     sensor on this rig can. The tool asks, records the answer, and never infers it.

It commands nothing itself. It asks the output owner, which admits a trial only from confirmed
rest with every hard stop clear, and which owns the actuator as it does for everything else --
so a trial exercises the SAME code path the vehicle drives on, not a stand-in for it.

SERVER (inside live_trail.py --reverse-trial, on the Orin): watches a request file.
CLIENT (on the Mac):
    python3 reverse_trial.py --orin USER@HOST --fraction 0.25 \
        --question "does 0.25 move the truck on free floor?" \
        --surface "kitchen floor, free, straightaway" --contact none --wheels floor
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path

SCHEMA = 1
OUTCOMES = ("moved", "wheelspin_observed", "no_motion_within_bounds", "vetoed", "indeterminate")
WHEEL_OBSERVATIONS = ("rotated_and_body_moved", "rotated_body_still", "did_not_rotate",
                      "not_observed", "hidden")
REQUEST = "trial_request.json"
RESULT = "trial_result.json"
LEDGER = "trials.jsonl"
SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes"]


# ---------------------------------------------------------------- server (runs on the Orin)

class TrialServer(threading.Thread):
    """Watches for a request file and hands it to the owner. Commands nothing.

    Deliberately NOT on the frame loop and NOT on the sender: it only moves JSON between the
    filesystem and the owner's request mailbox. The owner decides, on its own thread, whether
    the vehicle is in a state where a trial may be admitted.
    """
    def __init__(self, owner, directory, base_config, ceiling, identity=None, poll_s=0.25):
        super().__init__(daemon=True)
        self.owner = owner
        self.dir = Path(directory).expanduser()
        self.base, self.ceiling = base_config, float(ceiling)
        self.identity = dict(identity or {})
        self.poll_s = poll_s
        self._quit = threading.Event()
        self.served = 0
        self.last_error = None

    # ---- the one place a request becomes a validated configuration
    def build_config(self, request):
        """-> (config, None) or (None, refusal). Refuses, never clamps.

        Silently clamping an out-of-range request would make the record lie about what was
        commanded. Every refusal names the bound it hit.
        """
        fraction = request.get("fraction")
        if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 < fraction:
            return None, f"fraction {fraction!r} must be a positive number"
        if fraction > self.ceiling:
            return None, (f"fraction {fraction} exceeds this run's ceiling {self.ceiling}; "
                          "the ceiling is chosen at launch and a trial may not raise it")
        overrides = request.get("overrides") or {}
        allowed = {"ramp_fraction_s", "negative_timeout_s", "primer_hold_s", "neutral_dwell_s",
                   "retreat_m", "primer_motion_m", "max_travel_m", "max_speed_mps", "phase_timeout_s",
                   "total_timeout_s", "standstill_s"}
        unknown = sorted(set(overrides) - allowed)
        if unknown:
            return None, f"overrides not permitted in a trial: {', '.join(unknown)}"
        try:
            config = replace(self.base, max_fraction=float(fraction),
                             fraction_ceiling=self.ceiling,
                             **{k: float(v) for k, v in overrides.items()})
        except (TypeError, ValueError) as exc:
            return None, f"configuration refused: {exc}"
        return config, None

    def write_result(self, payload):
        path = self.dir / RESULT
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
        with open(self.dir / LEDGER, "a") as fh:      # append-only; never rewritten
            fh.write(json.dumps(payload, default=str) + "\n")

    def serve_once(self):
        path = self.dir / REQUEST
        if not path.is_file():
            return None
        try:
            request = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            self.last_error = f"unreadable request: {exc}"
            path.unlink(missing_ok=True)
            return None
        path.unlink(missing_ok=True)                  # one request, consumed once
        trial_id = str(request.get("trial_id") or f"t{int(time.time())}")
        config, refusal = self.build_config(request)
        if refusal is not None:
            self.write_result(dict(schema=SCHEMA, trial_id=trial_id, admitted=False,
                                   outcome="vetoed", reason=refusal, request=request,
                                   **self.identity))
            return "refused"
        accepted, why = self.owner.request_trial(dict(
            request, trial_id=trial_id, config=config, fraction=float(request["fraction"]),
            config_values=asdict(config)))
        if not accepted:
            self.write_result(dict(schema=SCHEMA, trial_id=trial_id, admitted=False,
                                   outcome="vetoed", reason=why, request=request,
                                   **self.identity))
            return "refused"
        deadline = time.monotonic() + float(config.total_timeout_s) + 15.0
        while time.monotonic() < deadline and not self._quit.is_set():
            state = self.owner.trial_state()
            if state is not None and state.get("trial_id") == trial_id and state.get("outcome"):
                state.pop("config", None)
                # The owner retains the client's schema in its state. Keyword expansion into
                # dict(schema=..., **state) raised only AFTER a real trial finished, so the
                # client timed out despite a complete trial_end in the run log. Merge mappings
                # explicitly; the server owns the envelope and the original request remains
                # nested intact alongside the owner's measured outcome and command history.
                self.write_result({**state, **self.identity, "schema": SCHEMA, "request": request})
                self.served += 1
                return state["outcome"]
            self._quit.wait(0.05)
        self.write_result(dict(schema=SCHEMA, trial_id=trial_id, admitted=True,
                               outcome="indeterminate",
                               reason="the owner did not report an outcome before the deadline",
                               request=request, **self.identity))
        return "indeterminate"

    def stop(self):
        self._quit.set()

    def run(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / REQUEST).unlink(missing_ok=True)   # a stale request must never fire a pulse
        while not self._quit.is_set():
            try:
                self.serve_once()
            except Exception as exc:                   # a trial server must not kill the tool
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(f"trial server error: {self.last_error}", file=sys.stderr, flush=True)
            self._quit.wait(self.poll_s)


# ---------------------------------------------------------------- the acceptance artefact

PROFILE_SCHEMA = 1


# Terminal reasons that mean the waveform COMPLETED its bounded sequence. A trial stopped by a
# limit measured the limit, not the waveform, and is not acceptance evidence.
CLEAN_TERMINALS = frozenset(("sequence_done",))
LIMIT_TERMINALS = frozenset(("speed_limit", "travel_limit", "negative_timeout", "episode_timeout",
                             "phase_timeout", "servo_reverse_limit"))
# A wheel observation that actually observed the wheels.
ADEQUATE_OBSERVATIONS = frozenset(("rotated_and_body_moved", "did_not_rotate"))
PURPOSES = ("onset", "tail")
# Config fields that must be IDENTICAL across the trials that reproduce an amplitude. Two runs
# with different ramps or dwells are two waveforms, not one reproduced twice.
WAVEFORM_FIELDS = ("max_fraction", "ramp_fraction_s", "neutral_dwell_s", "primer_hold_s",
                   "negative_timeout_s", "phase_timeout_s", "total_timeout_s", "standstill_s",
                   "retreat_m", "max_travel_m", "max_speed_mps")


def _waveform_key(row):
    cfg = row.get("config_values") or {}
    return tuple(cfg.get(f) for f in WAVEFORM_FIELDS)


def _calibration_key(row):
    """Actuator calibration identity. Two trials on different RC/servo calibrations are not
    the same experiment, whatever the fraction says."""
    return json.dumps(row.get("actuator_calibration") or {}, sort_keys=True)


def build_profile(ledger_path, profile_id, operator, note=""):
    """Turn a trial ledger into an ACCEPTED reverse profile, or refuse and say why.

    This is the only thing `--reverse-ready` will load, and autonomous recovery is refused
    without it. REWRITTEN 2026-09-14 after an external review reproduced five different ledgers
    that it wrongly accepted: no stopping-tail measurement, the same trial id counted twice as a
    reproduction, two different waveforms grouped as one, trials that ended on a LIMIT rather
    than completing, and trials with no wheel observation or battery stated.

    What it now demands, and refuses to infer:
      * DISTINCT trial identities -- one row duplicated is one trial;
      * the SAME COMPLETE WAVEFORM and the same actuator calibration across them;
      * CLEAN BOUNDED COMPLETION -- a limit stop measured the limit, not the waveform;
      * BACKWARD travel, direction-checked, not net displacement;
      * an ADEQUATE WHEEL OBSERVATION on every accepted trial, and NO wheelspin at or below
        the amplitude -- spin bounds the escalation from above;
      * a STATED ENVELOPE: surface, contact, battery voltage;
      * a MEASURED STOPPING TAIL at that amplitude (`purpose: tail`), because a recovery that
        cannot say how far it travels after the stop command cannot claim rear clearance.

    Onset evidence and recovery acceptance are kept separate throughout.
    """
    rows = []
    for line in Path(ledger_path).read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    if not rows:
        return None, f"{ledger_path} holds no readable trials"

    def req(r, key, default=None):
        return (r.get("request") or {}).get(key, default)

    floor = [r for r in rows if r.get("admitted") and req(r, "wheels") == "floor"]
    if not floor:
        return None, ("no admitted ON-THE-FLOOR trial in the ledger. Wheels-clear trials "
                      "establish delivery and sequencing only; they cannot establish break-away.")

    def fraction(r):
        try:
            return float(req(r, "fraction"))
        except (TypeError, ValueError):
            return None

    spun = [r for r in floor if r.get("outcome") == "wheelspin_observed"
            or r.get("wheel_observation") == "rotated_body_still"]

    onset = [r for r in floor if req(r, "purpose", "onset") == "onset"
             and r.get("outcome") == "moved"]
    if not onset:
        return None, "no ON-THE-FLOOR onset trial has outcome `moved`; there is nothing to accept"

    # Group by (waveform, calibration, surface, contact) -- not by amplitude alone.
    groups = {}
    for r in onset:
        key = (_waveform_key(r), _calibration_key(r), req(r, "surface"), req(r, "contact"))
        groups.setdefault(key, []).append(r)

    rejected = []
    candidates = {}
    for key, members in groups.items():
        ids = {r.get("trial_id") for r in members}
        if len(ids) < 2:
            rejected.append(f"{len(members)} row(s) but {len(ids)} distinct trial id(s) "
                            f"at fraction {fraction(members[0])}")
            continue
        clean = [r for r in members if r.get("reason") in CLEAN_TERMINALS]
        if len({r.get("trial_id") for r in clean}) < 2:
            reasons = sorted({str(r.get("reason")) for r in members})
            rejected.append(f"fraction {fraction(members[0])}: fewer than two trials completed "
                            f"their bounded sequence (terminal reasons: {', '.join(reasons)})")
            continue
        bad_obs = [r.get("trial_id") for r in clean
                   if r.get("wheel_observation") not in ADEQUATE_OBSERVATIONS]
        if bad_obs:
            rejected.append(f"fraction {fraction(members[0])}: no adequate wheel observation on "
                            f"{', '.join(str(t) for t in bad_obs)}")
            continue
        no_volts = [r.get("trial_id") for r in clean
                    if not isinstance(req(r, "battery_v"), (int, float))]
        if no_volts:
            rejected.append(f"fraction {fraction(members[0])}: battery voltage unstated on "
                            f"{', '.join(str(t) for t in no_volts)}")
            continue
        short = [r.get("trial_id") for r in clean
                 if not isinstance((r.get("metrics") or {}).get("backwards_m"), (int, float))]
        if short:
            rejected.append(f"fraction {fraction(members[0])}: no measured backward travel on "
                            f"{', '.join(str(t) for t in short)}")
            continue
        candidates[key] = clean

    if not candidates:
        return None, ("no amplitude is supported by two distinct, cleanly completed, fully "
                      "observed trials of ONE waveform under ONE stated envelope. "
                      + "; ".join(rejected))

    chosen_key = min(candidates, key=lambda k: (fraction(candidates[k][0]) or 1e9))
    evidence = candidates[chosen_key]
    amplitude = fraction(evidence[0])
    surface, contact = chosen_key[2], chosen_key[3]

    spun_at_or_below = sorted({f for f in (fraction(r) for r in spun)
                               if f is not None and amplitude is not None and f <= amplitude})
    if spun_at_or_below:
        return None, (f"wheelspin was observed at {spun_at_or_below}, at or below the candidate "
                      f"amplitude {amplitude}. Spin bounds the escalation from above; this "
                      "amplitude cannot be accepted. Review, do not increase.")

    # THE STOPPING TAIL. Required, at this amplitude, on this waveform, with a real measurement.
    tails = [r for r in floor
             if req(r, "purpose") == "tail"
             and _waveform_key(r) == chosen_key[0] and _calibration_key(r) == chosen_key[1]
             and req(r, "surface") == surface and req(r, "contact") == contact
             and isinstance((r.get("tail") or {}).get("max_rearward_after_stop_m"), (int, float))
             and (r.get("tail") or {}).get("rest_confirmed") is True]
    if not tails:
        return None, (f"no STOPPING TAIL measurement for amplitude {amplitude} on this waveform "
                      "and envelope. Run trials with `--purpose tail`: a recovery that cannot say "
                      "how far it travels after the stop command cannot claim rear clearance. "
                      "This gate is unsatisfiable until that instrumentation exists, and that is "
                      "the correct state -- not a reason to relax it.")

    config = dict(evidence[0].get("config_values") or {})
    config["max_fraction"] = amplitude
    volts = [req(r, "battery_v") for r in evidence if isinstance(req(r, "battery_v"), (int, float))]
    tail_max = max(t["tail"]["max_rearward_after_stop_m"] for t in tails)
    profile = dict(
        schema=PROFILE_SCHEMA, profile_id=profile_id, accepted_by=operator,
        accepted_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), note=note,
        config=config, amplitude=amplitude,
        waveform=dict(zip(WAVEFORM_FIELDS, chosen_key[0])),
        actuator_calibration=json.loads(chosen_key[1]) if chosen_key[1] != "{}" else None,
        envelope=dict(surface=surface, contact=contact,
                      battery_v_min=min(volts), battery_v_max=max(volts)),
        evidence=dict(onset_trial_ids=sorted(str(r.get("trial_id")) for r in evidence),
                      tail_trial_ids=sorted(str(t.get("trial_id")) for t in tails),
                      backwards_m=[(r.get("metrics") or {}).get("backwards_m") for r in evidence],
                      wheel_observations=[r.get("wheel_observation") for r in evidence],
                      terminal_reasons=[r.get("reason") for r in evidence],
                      trials_considered=len(rows), floor_trials=len(floor)),
        stopping_tail_m=tail_max,
        stopping_tail_status=(f"MEASURED: worst rearward travel after the stop command across "
                              f"{len(tails)} tail trial(s) was {tail_max:.3f} m, at the PHONE's "
                              "reference point. That is NOT rear-bumper clearance -- add the "
                              "camera-to-rear offset before quoting a clearance."),
        limits_note="Accepted ONLY within the stated envelope. A different pack, surface, contact "
                    "or vehicle state requires recharacterisation, not extrapolation.")
    return profile, None


def load_profile(path):
    """-> (profile, None) or (None, refusal). Used by live_trail before any autonomous reverse."""
    try:
        profile = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        return None, f"cannot read reverse profile {path}: {exc}"
    if profile.get("schema") != PROFILE_SCHEMA:
        return None, f"reverse profile schema {profile.get('schema')!r} is not {PROFILE_SCHEMA}"
    for field in ("profile_id", "config", "amplitude", "envelope", "evidence", "accepted_by",
                  "waveform"):
        if not profile.get(field):
            return None, f"reverse profile is missing {field!r}"
    # THE LOADER CHECKS THE ACCEPTANCE CONDITIONS TOO. A boolean plus a JSON file is not a gate
    # unless the file's own claims are verified: the previous version checked field PRESENCE and
    # then printed ACCEPTED over a profile whose stopping tail was explicitly unmeasured.
    tail = profile.get("stopping_tail_m")
    if not isinstance(tail, (int, float)) or tail < 0:
        return None, ("reverse profile has NO MEASURED STOPPING TAIL "
                      f"(stopping_tail_m={tail!r}). Autonomous reverse without a measured tail "
                      "cannot bound rear clearance; it is refused.")
    env = profile.get("envelope") or {}
    for field in ("surface", "contact", "battery_v_min", "battery_v_max"):
        if env.get(field) in (None, ""):
            return None, f"reverse profile envelope does not state {field!r}"
    ev = profile.get("evidence") or {}
    onset_ids = set(ev.get("onset_trial_ids") or [])
    if len(onset_ids) < 2:
        return None, (f"reverse profile cites {len(onset_ids)} distinct onset trial(s); "
                      "an amplitude must have moved the truck twice to be accepted")
    if not (ev.get("tail_trial_ids") or []):
        return None, "reverse profile cites no stopping-tail trial"
    # The configuration must match the waveform the profile says was characterised.
    waveform = profile.get("waveform") or {}
    config = profile.get("config") or {}
    mismatch = [k for k, v in waveform.items()
                if v is not None and k in config and config[k] != v]
    if mismatch:
        return None, ("reverse profile's configuration disagrees with the waveform it claims to "
                      f"have characterised: {', '.join(sorted(mismatch))}")
    if config.get("max_fraction") != profile.get("amplitude"):
        return None, ("reverse profile's config max_fraction does not equal its accepted "
                      "amplitude")
    return profile, None


# ---------------------------------------------------------------- client (runs on the Mac)

def ssh(host, command, timeout=60):
    return subprocess.run(["ssh", *SSH_OPTS, host, command],
                          capture_output=True, text=True, timeout=timeout)


def summarise(result):
    m = result.get("metrics") or {}
    rows = result.get("commands") or []
    requested = [r["requested_rc2"] for r in rows if isinstance(r.get("requested_rc2"), (int, float))]
    reported = [r["reported_servo3"] for r in rows if isinstance(r.get("reported_servo3"), (int, float))]
    lines = [f"outcome: {result.get('outcome', '?').upper()}   ({result.get('reason')})"]
    if not result.get("admitted"):
        lines.append("NOT ADMITTED -- nothing was commanded.")
        return "\n".join(lines)
    lines.append(f"trial {result.get('trial_id')}  fraction {(result.get('request') or {}).get('fraction')}"
                 f"  duration {result.get('duration_s')} s")
    lines.append("phases: " + ", ".join(f"{p['phase']}@{p['t']:.2f}s" for p in result.get("phases") or []))
    if requested:
        lines.append(f"requested RC2: min {min(requested)} max {max(requested)} us over {len(requested)} ticks")
    if reported:
        lines.append(f"reported SERVO3: min {min(reported)} max {max(reported)} us "
                     "(the firmware's OUTPUT REPORT -- not the pin, the ESC or wheel torque)")
    else:
        lines.append("reported SERVO3: NONE SEEN -- delivery is indeterminate, not failed")
    lines.append(f"backwards {m.get('backwards_m')} m  forward {m.get('forward_m')} m  "
                 f"lateral {m.get('lateral_m')} m  net {result.get('net_displacement_m')} m")
    lines.append(f"total travel {m.get('travel_m')} m  negative command {m.get('negative_s')} s  "
                 f"primer moved {m.get('primer_moved')}")
    lines.append("Body displacement is from the phone's pose. It CANNOT distinguish a stalled "
                 "drivetrain from spinning wheels; only your eyes can.")
    return "\n".join(lines)


def accept_main(argv):
    ap = argparse.ArgumentParser(prog="reverse_trial.py accept",
                                 description="Turn a trial ledger into an accepted reverse profile.")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", required=True, help="where to write the profile JSON")
    ap.add_argument("--operator", required=True, help="who is accepting this, by name")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    profile_id = "rev-" + time.strftime("%Y-%m-%d-%H%M%S")
    profile, refusal = build_profile(a.ledger, profile_id, a.operator, a.note)
    if refusal is not None:
        print("NOT ACCEPTED.\n  " + refusal)
        print("\nThis is a complete and correct outcome. Run more trials, or accept that the "
              "evidence does not support an autonomous reverse yet.")
        return 1
    out = Path(a.out)
    if out.exists():
        print(f"REFUSING: {out} already exists. A profile is never overwritten -- write a new one.")
        return 3
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    print(f"ACCEPTED: {profile_id}")
    print(f"  amplitude {profile['amplitude']} reproduced by trials "
          f"{', '.join(str(t) for t in profile['evidence']['onset_trial_ids'])}")
    print(f"  envelope: {profile['envelope']['surface']!r}, contact {profile['envelope']['contact']!r}, "
          f"battery {profile['envelope']['battery_v_min']}-{profile['envelope']['battery_v_max']} V")
    print(f"  stopping tail: {profile['stopping_tail_status']}")
    print(f"  written to {out}")
    print("\nAutonomous recovery now needs BOTH --reverse-ready AND --reverse-profile "
          f"{out}. It is accepted only inside the envelope above.")
    return 0


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import recovery as RECOVERY
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "accept":
        return accept_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orin", required=True, help="USER@HOST of the Orin (key-based ssh)")
    ap.add_argument("--dir", default="~/live/trials")
    ap.add_argument("--fraction", type=float, required=True, help="reverse amplitude for THIS trial")
    ap.add_argument("--question", required=True, help="the ONE question this trial answers")
    ap.add_argument("--surface", required=True, help="floor and its condition, in words")
    ap.add_argument("--contact", default="none", help="none | nose-in <what> | rear-against <what>")
    ap.add_argument("--purpose", choices=PURPOSES, default="onset",
                    help="onset = does this amplitude move the truck at all; tail = how far does "
                         "it travel AFTER the stop command. Acceptance needs both, and a stop at "
                         "initial break-away does not characterise stopping after a longer retreat.")
    ap.add_argument("--wheels", choices=("clear", "floor"), required=True,
                    help="clear = up on blocks (delivery/sequencing only, NOT break-away); "
                         "floor = loaded on the ground")
    ap.add_argument("--battery-v", type=float, help="drivetrain pack voltage before the trial")
    ap.add_argument("--override", action="append", default=[], metavar="KEY=VALUE",
                    help="one sequence term, e.g. ramp_fraction_s=0.25 (repeatable)")
    ap.add_argument("--trial-id", default=None)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--ledger", default=None, help="local append-only jsonl to add the result to")
    a = ap.parse_args(argv)

    if not 0 < a.fraction <= RECOVERY.ABS_FRACTION_CEILING:
        sys.exit(f"REFUSING: --fraction must be in (0, {RECOVERY.ABS_FRACTION_CEILING}]")
    overrides = {}
    for item in a.override:
        key, _, value = item.partition("=")
        try:
            overrides[key.strip()] = float(value)
        except ValueError:
            sys.exit(f"REFUSING: --override {item!r} is not KEY=VALUE with a number")
    trial_id = a.trial_id or time.strftime("t%H%M%S")
    request = dict(schema=SCHEMA, trial_id=trial_id, fraction=a.fraction, question=a.question,
                   purpose=a.purpose, surface=a.surface, contact=a.contact, wheels=a.wheels,
                   battery_v=a.battery_v, overrides=overrides,
                   requested_wall=time.time(), requested_local=time.strftime("%Y-%m-%d %H:%M:%S"))

    print(f"\ntrial {trial_id}: fraction {a.fraction}, wheels {a.wheels}, contact {a.contact}")
    print(f"question: {a.question}")
    if a.wheels == "clear":
        print("WHEELS CLEAR: this trial can establish DELIVERY and ESC SEQUENCING only. Rotation "
              "without translation is EXPECTED here and is not a traction result.")
    print("\nBefore you confirm: kill switch in hand, rear clearance checked, eyes on a MARKED "
          "DRIVEN WHEEL. If either driven wheel is hidden, wheelspin cannot be excluded.")
    try:
        if input("type GO to command this trial: ").strip() != "GO":
            print("not sent.")
            return 1
    except EOFError:
        print("not sent (no terminal).")
        return 1

    remote = f"{a.dir}/{REQUEST}"
    ssh(a.orin, f"mkdir -p {a.dir} && rm -f {a.dir}/{RESULT}")
    payload = json.dumps(request)
    r = ssh(a.orin, f"cat > {remote} <<'TRIALEOF'\n{payload}\nTRIALEOF")
    if r.returncode:
        sys.exit(f"could not place the request: {r.stderr.strip()}")
    print("requested. waiting for the owner's verdict...")

    deadline = time.time() + a.timeout
    result = None
    while time.time() < deadline:
        got = ssh(a.orin, f"cat {a.dir}/{RESULT} 2>/dev/null")
        if got.returncode == 0 and got.stdout.strip():
            try:
                candidate = json.loads(got.stdout)
            except ValueError:
                time.sleep(0.4); continue
            if candidate.get("trial_id") == trial_id:
                result = candidate
                break
        time.sleep(0.4)
    if result is None:
        print("\nNO RESULT within the timeout. This is INDETERMINATE, not a failure to move.\n"
              "Check `python3 truck_status.py --orin " + a.orin + "` before anything else.")
        return 2

    print("\n" + summarise(result) + "\n")
    observation = None
    if result.get("admitted"):
        print("What did the DRIVEN WHEELS do? " + " / ".join(WHEEL_OBSERVATIONS))
        try:
            observation = input("wheel observation: ").strip() or "not_observed"
        except EOFError:
            observation = "not_observed"
        if observation not in WHEEL_OBSERVATIONS:
            print(f"(recording {observation!r} verbatim; it is not one of the standard words)")
        result["wheel_observation"] = observation
        if observation == "rotated_body_still":
            result["outcome"] = "wheelspin_observed"
            print("\n*** WHEELSPIN OBSERVED. This ENDS the escalation and triggers review. ***\n"
                  "Do NOT try a larger pulse: the limit here is traction, not break-away.")
    result["operator_note"] = ""
    try:
        result["operator_note"] = input("note (optional): ").strip()
    except EOFError:
        pass

    ledger = Path(a.ledger) if a.ledger else (Path(__file__).resolve().parent
                                              / "trials" / LEDGER)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a") as fh:                     # append-only: a trial is never overwritten
        fh.write(json.dumps(result, default=str) + "\n")
    print(f"\nrecorded: {ledger}")
    print(f"FINAL OUTCOME: {result['outcome'].upper()}")
    print("\nThe tool will not choose the next amplitude. Read the result, decide, and run one "
          "more trial.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

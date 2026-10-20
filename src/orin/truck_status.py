#!/usr/bin/env python3
# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""One snapshot of the control layer, published by the tool and read by a command.

Phase 1A. Writer and reader live in the same file so the schema cannot drift.

What this is for: on 2026-09-14 the answer to "why is it not driving?" took a log
dig every single time, and the only field that carried the cause -- `output.veto`
-- was not printed anywhere. `cruise_why` says `"startup"` for every cause.

Three separations it keeps, because conflating them has already cost hours:

  * **permission is not output, and output is not motion.** A released override,
    an explicit neutral and an observed standstill are three different facts.
    MAVLink override zero RELEASES a channel; it does not command neutral.
  * **fresh is not recent.** Every age is computed on the Orin from its own
    monotonic clock and a boot id; a snapshot from a previous boot is UNKNOWN,
    not old. A dead or recycled PID is UNKNOWN, never yesterday's stopped state.
  * **applied is not requested.** The settings revision reported here is read
    back from the live args namespace, not from the plan file. Reading a changed
    file is not an acknowledgement that anything was applied.

Read it:            python3 ~/live/truck_status.py            # on the Orin
From the Mac:       python3 truck_status.py --orin USER@HOST
Machine-readable:   python3 ~/live/truck_status.py --json
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SCHEMA = 1
DEFAULT_PATH = str(Path.home() / "live" / "status.json")
STALE_S = 2.0            # a snapshot older than this is not describing the present


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def process_start(pid=None):
    """Field 22 of /proc/<pid>/stat: start time in clock ticks since boot.

    (pid, starttime) together survive PID reuse; pid alone does not.
    """
    pid = os.getpid() if pid is None else pid
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def alive(pid, starttime):
    if pid is None:
        return False
    current = process_start(pid)
    if current is None:
        return False
    return starttime is None or current == starttime


class Publisher(threading.Thread):
    """Publishes atomically, OFF the sender's thread, at its own rate.

    `collect` returns the variable half of the snapshot. It is called outside the
    50 Hz actuator loop on purpose: a status write must never be able to delay a
    command. If `collect` raises, the snapshot still goes out carrying the error,
    because a status tool that goes silent on a fault is worse than one that says
    what the fault was.
    """
    def __init__(self, path, collect, hz=5.0, static=None):
        super().__init__(daemon=True)
        self.path = Path(path).expanduser()
        self.collect, self.period = collect, 1.0 / max(0.5, hz)
        self.static = dict(static or {})
        self._quit = threading.Event()
        self.writes = self.errors = 0

    def payload(self):
        body, error = {}, None
        try:
            body = self.collect() or {}
        except Exception as exc:                        # never let status die quietly
            error = f"{type(exc).__name__}: {exc}"
            self.errors += 1
        return dict(schema=SCHEMA, **self.static,
                    pid=os.getpid(), proc_start=process_start(), boot_id=boot_id(),
                    monotonic=time.monotonic(), wall=time.time(),
                    collect_error=error, writes=self.writes, **body)

    def write_once(self):
        payload = self.payload()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, default=str) + "\n")
        os.replace(tmp, self.path)                      # atomic: a reader sees one or the other
        self.writes += 1
        return payload

    def stop(self):
        self._quit.set()

    def run(self):
        while not self._quit.is_set():
            try:
                self.write_once()
            except OSError:
                self.errors += 1
            self._quit.wait(self.period)
        try:                                            # the last word is that nothing is running
            self.static["exited"] = True
            self.write_once()
        except OSError:
            pass


# ------------------------------------------------------------------ reading

def read(path=DEFAULT_PATH):
    """-> (payload, verdict). verdict is one of live / stale / dead / absent / other_boot."""
    p = Path(path).expanduser()
    try:
        payload = json.loads(p.read_text())
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError):
        return None, "unreadable"
    if payload.get("schema") != SCHEMA:
        return payload, "schema_mismatch"
    if payload.get("boot_id") and boot_id() and payload["boot_id"] != boot_id():
        return payload, "other_boot"
    payload["age_s"] = None
    if payload.get("boot_id") == boot_id() and isinstance(payload.get("monotonic"), (int, float)):
        payload["age_s"] = time.monotonic() - payload["monotonic"]
    if not alive(payload.get("pid"), payload.get("proc_start")):
        return payload, "dead"
    if payload.get("exited"):
        return payload, "dead"
    if payload["age_s"] is None or payload["age_s"] > STALE_S or payload["age_s"] < -0.5:
        return payload, "stale"
    return payload, "live"


def render(payload, verdict):
    """Human view. UNKNOWN is a first-class answer and is never dressed up."""
    if payload is None or verdict in ("absent", "unreadable", "schema_mismatch"):
        return (f"UNKNOWN | no usable snapshot ({verdict})\n"
                "nothing can be said about permission, output or motion from here.")
    out = payload.get("output") or {}
    lines = []
    if verdict != "live":
        why = {"dead": "the publishing process is gone",
               "other_boot": "this snapshot is from a previous boot",
               "stale": f"snapshot is {payload.get('age_s')!r} s old (limit {STALE_S} s)"}[verdict]
        lines.append(f"UNKNOWN | {why}")
        lines.append("Permission, output and motion below are the LAST PUBLISHED values, not the present state.")
    phase = "DRIVING" if out.get("cruise_on") else ("RECOVERY" if out.get("autonomous_permission") else "IDLE")
    start = "start granted" if out.get("autonomous_permission") else (
        "start pending" if out.get("start_pending") else
        ("start ready" if out.get("start_ready") else "start required"))
    lines.append(f"{phase} | {start} | autonomy {'ON' if out.get('autonomous_permission') else 'off'} "
                 f"| pkg {str(payload.get('package_digest') or 'UNGATED')[:8]}"
                 f"/run {str(payload.get('running_digest') or '?')[:8]} cfg {payload.get('settings_revision', '?')}")
    veto = out.get("veto") or out.get("cruise_why") or "none"
    lines.append(f"veto {veto} | stop_state {out.get('stop_state', '?')} | epoch {out.get('epoch', '?')}"
                 f" | recovery {out.get('recovery_phase', '?')} x{out.get('recovery_attempts', 0)}")
    lines.append(f"output: state {out.get('state', '?')} steer_pwm {out.get('pwm', '?')} "
                 f"throttle_req {out.get('throttle', '?')} us; sends {out.get('sends', '?')}; "
                 f"{'SENDING' if out.get('sending') else 'not sending'}")
    servo = payload.get("servo") or {}
    lines.append(f"observed SERVO3 {servo.get('value') if servo.get('value') is not None else 'unknown'} "
                 f"(recv age {servo.get('recv_age_ms') if servo.get('recv_age_ms') is not None else 'unknown'} ms, "
                 f"source age unknown -- dequeue time is not packet time)")
    pose = payload.get("pose") or {}
    lines.append(f"pose {pose.get('age_ms', 'unknown')} ms {pose.get('tracking', '?')} "
                 f"| speed {pose.get('speed', '?')} m/s | pose_valid {out.get('pose_valid', '?')}")
    lines.append(f"recording {payload.get('recording') or 'none'} | path {payload.get('path_name') or 'none'} "
                 f"{'valid' if payload.get('path_valid') else 'INVALID/absent'}")
    cal = payload.get("reverse") or {}
    lines.append(f"reverse profile {cal.get('profile', 'none')}: {cal.get('acceptance', 'UNACCEPTED')}"
                 + (f" ({cal['reason']})" if cal.get("reason") else ""))
    if payload.get("batch"):
        b = payload["batch"]
        lines.append(f"batch cell {b.get('cell')}/{b.get('cells')} phase {b.get('phase')}")
    if payload.get("collect_error"):
        lines.append(f"COLLECT ERROR: {payload['collect_error']}")
    if verdict == "live":
        lines.append(f"snapshot {payload['age_s'] * 1000:.0f} ms old, pid {payload.get('pid')}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="print the control layer's published snapshot")
    # No default here: an expanded LOCAL default was being passed to the Orin, so the documented
    # `--orin` command read the Mac's own home-directory path on a machine that has no such path.
    # Let each side resolve its own default.
    ap.add_argument("--path", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--orin", help="USER@HOST: run this same command over ssh (key-based) on the Orin instead")
    a = ap.parse_args(argv)
    if a.orin:
        import shlex
        remote = f" --path {shlex.quote(a.path)}" if a.path else ""
        cmd = "python3 ~/live/truck_status.py" + remote + (" --json" if a.json else "")
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=8",
                            "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                            a.orin, cmd],
                           capture_output=True, text=True, timeout=30)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        if r.returncode == 255:
            print(f"UNKNOWN | the Orin at {a.orin} did not answer. Nothing can be said from here.")
        return r.returncode
    payload, verdict = read(a.path or DEFAULT_PATH)
    if a.json:
        print(json.dumps(dict(verdict=verdict, snapshot=payload), indent=2, default=str, sort_keys=True))
    else:
        print(render(payload, verdict))
    return 0 if verdict == "live" else 2


if __name__ == "__main__":
    sys.exit(main())

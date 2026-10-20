# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Per-cell parameter changes applied at a confirmed batch cell boundary.

WHY THIS EXISTS (the operator, 2026-09-14 17:30, at the truck): "Once I start the initial
automation, I'm only there for safety." A settings matrix that needs a human to
restart the process between cells is not that. The batch already repeats identical
cells under one authorization (FINDINGS 73); this varies ONE thing per cell inside
the same process, so there is no restart and therefore no restart hazard.

WHAT MAKES IT SAFE:
  * The frame loop and output_owner share ONE args namespace and both read it live,
    so a change reaches both. That is also why a change may only be applied while the
    batch is PAUSED with rest confirmed -- a torn read cannot drive anything when
    autonomous throttle has already been withdrawn.
  * Only knobs the frame loop re-reads every frame may be changed. Anything baked at
    startup is refused BY NAME, because changing it would leave the run in a state
    where half the pipeline has the new value and half the old.
  * Every value is range-checked when the plan is LOADED, before the vehicle can move,
    so a bad plan fails at startup rather than at a cell boundary with the truck live.
  * The plan can never enable a behaviour, only retune one that the command line
    already turned on. It cannot switch on cruise, the path, recovery or the batch.

REFUSED BY NAME, and why:
  fit_max_y   the fit's row VOTE MASK is precomputed once at startup from this value
              (live_trail.py builds `vote_rows` before the loop). The loop also reads
              a.fit_max_y, so changing it would leave the mask and the value disagreeing.
              This is the vote mask biting for the fourth time on this project.
  cruise_max, cruise_kp, cruise_ki, cruise_i_max
              the speed loop's PWM limits are computed once when CruiseSpeedLoop is built.
  pitch, pitch_fallback, head, path, cap, kappa_full_lock, steer_sign
              consumed at startup to build the projection, the perception model or the reference.
"""
import json
import math


def _finite(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


class CellPlan:
    """A validated list of parameter sets, one per batch cell."""

    # knob -> (kind, lo, hi). Every one of these is re-read by the frame loop or by
    # cruise_decide on every pass; none is baked into a startup-built object.
    SAFE = {
        "gain":              ("float", 0.1, 3.0),
        "lookahead":         ("float", 0.2, 2.0),
        "cap_kappa":         ("float", 0.0, 5.0),
        "min_rows":          ("int", 0, 17),
        "min_vote":          ("int", 0, 17),
        "ema":               ("float", 0.0, 1.0),
        "goal_jump":         ("float", 0.0, 2.0),
        "lowrow_hold_ms":    ("float", 0.0, 10000.0),
        "kff":               ("float", 0.0, 2.0),
        "kff_max":           ("float", 0.0, 2.0),
        "path_max_blind_m":  ("float", 0.0, 3.0),
        "path_max_dist_m":   ("float", 0.05, 2.0),
        "cruise_mps":        ("pair", 0.05, 1.5),
    }

    # named refusals, so the error says WHY rather than "unknown key"
    BAKED = {
        "fit_max_y": "the fit vote mask is precomputed at startup from this value",
        "cruise_max": "the speed loop's PWM ceiling is fixed when the loop is built",
        "cruise_kp": "the speed loop is built once with its gains",
        "cruise_ki": "the speed loop is built once with its gains",
        "cruise_i_max": "the speed loop is built once with its gains",
        "pitch": "the ground projection is built at startup",
        "pitch_fallback": "the ground projection is built at startup",
        "head": "the steering head is loaded at startup",
        "path": "the reference is loaded at startup",
        "cap": "the patch-space cap is folded into the fit at startup",
        "kappa_full_lock": "the steering scale is fixed at startup",
        "steer_sign": "the actuator convention is fixed at startup",
    }

    # a plan may retune these only if the command line already enabled them
    REQUIRES_ENABLED = {
        "cruise_mps": "cruise_mps",
        "kff": "kff",
        "kff_max": "kff",
        "path_max_blind_m": "path",
        "path_max_dist_m": "path",
    }

    def __init__(self, cells, args, ff_us, us_per_mps, abs_max_mps, thr_floor_us, ceil_us):
        """`cells` is a list of dicts, one per batch cell. Validated here, at load time."""
        if not isinstance(cells, list) or not cells:
            raise ValueError("cell plan must be a non-empty list")
        if len(cells) > 12:
            raise ValueError("cell plan may hold at most 12 cells")
        self.ff_us, self.us_per_mps = float(ff_us), float(us_per_mps)
        self.abs_max_mps, self.thr_floor_us, self.ceil_us = float(abs_max_mps), float(thr_floor_us), float(ceil_us)
        self.cells = [self._check(i, c, args) for i, c in enumerate(cells)]

    # ---------------------------------------------------------------- validation
    def _check(self, index, cell, args):
        if not isinstance(cell, dict):
            raise ValueError(f"cell {index}: each cell must be an object of knob -> value")
        out = {}
        for key, val in cell.items():
            if key in self.BAKED:
                raise ValueError(f"cell {index}: '{key}' cannot change mid-run -- {self.BAKED[key]}")
            if key not in self.SAFE:
                raise ValueError(f"cell {index}: '{key}' is not a knob this plan may change")
            need = self.REQUIRES_ENABLED.get(key)
            if need is not None and not getattr(args, need, None):
                raise ValueError(f"cell {index}: '{key}' needs --{need.replace('_', '-')} "
                                 "already enabled on the command line; a plan may retune a "
                                 "behaviour but never switch one on")
            out[key] = self._value(index, key, val)
        if not out:
            raise ValueError(f"cell {index}: empty cells are not allowed; repeat a value to repeat a cell")
        return out

    def _value(self, index, key, val):
        kind, lo, hi = self.SAFE[key]
        if kind == "pair":
            if (not isinstance(val, (list, tuple)) or len(val) != 2
                    or not all(_finite(v) for v in val)):
                raise ValueError(f"cell {index}: '{key}' must be two finite numbers")
            a, b = float(val[0]), float(val[1])
            if not 0 < a < b:
                raise ValueError(f"cell {index}: '{key}' needs 0 < LO {a:g} < HI {b:g}")
            if b > hi:
                raise ValueError(f"cell {index}: '{key}' HI {b:g} is above the tool ceiling {hi:g} m/s")
            if a < lo:
                raise ValueError(f"cell {index}: '{key}' LO {a:g} is below {lo:g} m/s")
            ff_lo = self.ff_us + self.us_per_mps * a
            ff_hi = self.ff_us + self.us_per_mps * b
            if ff_hi > self.ceil_us:
                raise ValueError(f"cell {index}: '{key}' HI {b:g} m/s feeds forward to {ff_hi:.0f} us, "
                                 f"above the ceiling {self.ceil_us:.0f}")
            if ff_lo <= self.thr_floor_us:
                raise ValueError(f"cell {index}: '{key}' LO {a:g} m/s feeds forward to {ff_lo:.0f} us, "
                                 f"which reads as STOPPED")
            return (a, b)
        if not _finite(val):
            raise ValueError(f"cell {index}: '{key}' must be a finite number, got {val!r}")
        if kind == "int":
            if float(val) != int(val):
                raise ValueError(f"cell {index}: '{key}' must be a whole number, got {val!r}")
            val = int(val)
        else:
            val = float(val)
        if not lo <= val <= hi:
            raise ValueError(f"cell {index}: '{key}' {val!r} is outside {lo:g}..{hi:g}")
        return val

    # ------------------------------------------------------------------- applying
    def __len__(self):
        return len(self.cells)

    def describe(self):
        return [" ".join(f"{k}={v}" for k, v in sorted(c.items())) for c in self.cells]

    def apply_at_boundary(self, args, index, phase):
        """The ONLY entry point the tool uses. -> (changed dict or None, human-readable note).

        Returns (None, why) and writes nothing unless `phase` is the batch's confirmed-rest
        boundary. batch.py reaches 'await_frame' only after measured standstill plus fresh
        neutral servo feedback, so that is the one phase in which writing the shared args
        namespace cannot reach a moving vehicle. Any other phase is refused, including a
        plan index past the end of the plan.
        """
        if index is None or not isinstance(index, int) or isinstance(index, bool):
            return None, f"cell index {index!r} is not an integer"
        if not 0 <= index < len(self.cells):
            return None, f"cell {index} is outside the plan's 0..{len(self.cells) - 1}"
        if phase != "await_frame":
            return None, (f"batch phase is {phase!r}, not the confirmed-rest boundary; "
                          "nothing was changed")
        changed = self.apply(args, index)
        if not changed:
            return changed, "plan asks for no change"
        return changed, ", ".join(f"{k} {v['from']} -> {v['to']}" for k, v in sorted(changed.items()))

    def apply(self, args, index):
        """Set this cell's knobs on the shared args namespace. -> dict of what changed.

        CALL ONLY while the batch is paused with rest confirmed. Both the frame loop and
        the output owner read this namespace live and neither takes a lock.
        """
        if not isinstance(index, int) or not 0 <= index < len(self.cells):
            raise ValueError(f"cell index {index!r} outside the plan's 0..{len(self.cells) - 1}")
        changed = {}
        for key, val in sorted(self.cells[index].items()):
            before = getattr(args, key, None)
            if isinstance(val, tuple):
                same = (before is not None and len(tuple(before)) == 2
                        and tuple(float(x) for x in before) == val)
                val = list(val)
            else:
                same = before == val
            if not same:
                setattr(args, key, val)
                changed[key] = {"from": before, "to": val}
        return changed


def load(path, cells, args, ff_us, us_per_mps, abs_max_mps, thr_floor_us, ceil_us):
    """Read a JSON plan file and validate it against the run's configuration."""
    with open(path) as fh:
        raw = json.load(fh)
    if isinstance(raw, dict) and "cells" in raw:
        raw = raw["cells"]
    plan = CellPlan(raw, args, ff_us, us_per_mps, abs_max_mps, thr_floor_us, ceil_us)
    if cells != len(plan):
        raise ValueError(f"--batch-cells {cells} does not match the plan's {len(plan)} cells")
    return plan

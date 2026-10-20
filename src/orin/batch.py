# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Finite repeat cells under one start. No connections, threads or actuator writes.

Normal lap or timed completion pauses at explicit neutral; only measured rest plus fresh
neutral servo feedback permits the next cell. A terminal fault never advances.
"""
import math


class Batch:
    def __init__(self, cells, cell_max_s, max_s, stop_max_s=5., cell_seconds=None):
        if type(cells) is not int or not 2 <= cells <= 12:
            raise ValueError("batch needs 2..12 cells")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (cell_max_s, max_s, stop_max_s)):
            raise ValueError("batch budgets must be finite numbers")
        if not (1 <= cell_max_s <= 300 and cell_max_s <= max_s <= 1800 and 1 <= stop_max_s <= 10):
            raise ValueError("batch budgets outside initial bounds")
        if cell_seconds is not None and (isinstance(cell_seconds, bool)
                or not isinstance(cell_seconds, (int, float)) or not math.isfinite(cell_seconds)
                or not 0 < cell_seconds < cell_max_s):
            raise ValueError("--cell-seconds must be finite and 0 < seconds < --cell-max-s")
        self.cells, self.cell_max_s, self.max_s, self.stop_max_s = cells, cell_max_s, max_s, stop_max_s
        self.cell_seconds = cell_seconds
        self.index, self.phase = 0, "idle"
        self.started = self.phase_started = self.resume_after = None
        self.neutral_first = self.neutral_last = self.servo_seq = None
        self.reason = None

    @property
    def paused(self):
        return self.phase in ("stopping", "await_frame")

    def start(self, now):
        if self.phase != "idle":
            raise ValueError("a batch cannot be restarted in this process")
        self.started = self.phase_started = now
        self.phase = "driving"

    def abort(self, reason):
        if self.phase not in ("idle", "finished", "terminal"):
            self.phase, self.reason = "terminal", reason

    def stop_cell(self, now):
        if self.phase == "driving" and self.deadline(now) is None:
            self.phase, self.phase_started = "stopping", now
            self.neutral_first = self.neutral_last = self.servo_seq = None
            return True
        return False

    def timed_cell_done(self, now):
        """Elapsed driving time requests the same measured stop used by lap completion."""
        return (self.phase == "driving" and self.cell_seconds is not None
                and now - self.phase_started >= self.cell_seconds)

    def deadline(self, now):
        if self.phase in ("idle", "finished", "terminal"):
            return None
        if now - self.started >= self.max_s:
            return "batch_time_limit"
        if self.phase == "driving" and now - self.phase_started >= self.cell_max_s:
            return "cell_time_limit"
        return None

    def update(self, now, stopped, tele, servo_neutral, intent):
        if self.phase in ("idle", "finished", "terminal"):
            return None
        deadline = self.deadline(now)
        if deadline is not None:
            return deadline
        if self.phase == "driving":
            return None
        if now - self.phase_started >= self.stop_max_s:
            return "cell_stop_timeout" if self.phase == "stopping" else "cell_frame_timeout"
        age, seq, pwm = (tele.get(k) for k in ("servo3_age_ms", "servo3_seq", "servo3"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (age, seq, pwm)):
            return "cell_servo_invalid"
        if not 0 <= age <= 150:
            return "cell_servo_stale"
        if self.servo_seq is not None and seq < self.servo_seq:
            return "cell_servo_regressed"
        received = now - age / 1000.
        if abs(pwm - servo_neutral) > 5:
            self.neutral_first = self.neutral_last = None
        elif (self.servo_seq is None or seq > self.servo_seq) and received >= self.phase_started:
            if self.neutral_first is None:
                self.neutral_first = received
            self.neutral_last = received
        if self.servo_seq is None or seq > self.servo_seq:
            self.servo_seq = seq
        neutral = (self.neutral_first is not None and self.neutral_last is not None
                   and now - self.neutral_last <= .15
                   and self.neutral_last - self.neutral_first >= .30)
        if self.phase == "stopping" and stopped and neutral:
            if self.index + 1 == self.cells:
                self.phase = "finished"
            else:
                self.index += 1
                self.phase, self.phase_started, self.resume_after = "await_frame", now, now
        elif self.phase == "await_frame":
            if not stopped or abs(pwm - servo_neutral) > 5:
                return "cell_moved_while_stopped"
            if (neutral and intent.cell == self.index and intent.created > self.resume_after
                    and not intent.cell_done):
                self.phase, self.phase_started = "driving", now
        return None

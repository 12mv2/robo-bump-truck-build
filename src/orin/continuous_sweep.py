# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Frame-thread-only tuning sweep; never owns throttle or arms the vehicle.

Each stage supplies all four values. The output owner receives the resulting
steering intent only after the frame finishes; it does not read these knobs.
Elapsed time advances only on consecutive eligible frames, using monotonic time.

Live changes (2026-10-10): `SweepInbox` reads one command per `<dir>/*.json` file,
`{"id", "op": "append"|"next"|"now", "stage": {name, gain, lookahead, ema, steer_offset, secs}}`,
checked by `check_stage`, the rule the plan file passes. `next` goes after the current stage,
`append` at the end of the plan before its closing baseline stage, and `now` ends the current
stage at the next eligible frame. The plan keeps ending at the baseline: a stage placed after
the closing stage brings its own baseline copy. A `now` waits like any stage boundary, so it
never lands during a Path Recovery trial (the clock is paused) and applies on the first eligible
frame after it. Nothing here changes a row while the inbox stays empty.
"""
import json
import math
from pathlib import Path

LIMITS = {'gain': (.1, 3.), 'lookahead': (.2, 2.), 'ema': (0., 1.), 'steer_offset': (-.2, .2)}
MAX_STAGES = 128
OPS = ('append', 'next', 'now')
REQUEST_KEYS = {'id', 'op', 'stage', 'note'}
STAGE_KEYS = {'name', 'secs', *LIMITS}


def finite(x):
    return type(x) in (float, int) and math.isfinite(x)


def check_stage(name, duration, values, names):
    """The one stage rule, for the plan file and for inbox commands alike."""
    if not isinstance(name, str) or not name or name in names:
        raise ValueError('stage names must be unique and nonempty')
    if not finite(duration) or not 1 <= duration <= 300:
        raise ValueError('stage duration must be 1..300 seconds')
    if not isinstance(values, dict) or set(values) != set(LIMITS):
        raise ValueError('every stage must specify exactly all four sweep knobs')
    for k, (lo, hi) in LIMITS.items():
        if not finite(values[k]) or not lo <= values[k] <= hi:
            raise ValueError('invalid ' + k)
    return dict(name=name, seconds=float(duration), values=dict(values))


def parse_request(d):
    """An inbox command -> (id, op, stage in plan form). Static checks only; the plan-dependent
    ones (unique name, 128 stages, repeated id) are Sweep.request's and end in a logged decision."""
    if not isinstance(d, dict):
        raise ValueError('request must be a JSON object')
    if set(d) - REQUEST_KEYS:
        raise ValueError('unknown request keys ' + ', '.join(sorted(set(d) - REQUEST_KEYS)))
    rid, op, st = d.get('id'), d.get('op'), d.get('stage')
    if not isinstance(rid, str) or not 1 <= len(rid) <= 96:
        raise ValueError('request needs an id of 1..96 characters')
    if op not in OPS:
        raise ValueError('op must be append, next or now')
    if 'note' in d and (not isinstance(d['note'], str) or len(d['note']) > 200):
        raise ValueError('note must be text of at most 200 characters')
    if not isinstance(st, dict):
        raise ValueError('request needs a stage object')
    if 'cruise_mps' in st:
        raise ValueError('cruise_mps is not a sweep knob: the sweep runs only under --owner simple, '
                         'where the speed dial maps straight to PWM and nothing holds a speed')
    if set(st) - STAGE_KEYS:
        raise ValueError('unknown stage keys ' + ', '.join(sorted(set(st) - STAGE_KEYS)))
    return rid, op, check_stage(st.get('name'), st.get('secs'), {k: st[k] for k in LIMITS if k in st}, ())


class Sweep:
    def __init__(self, plan, args):
        if plan.get('version') != 1 or not isinstance(plan.get('stages'), list):
            raise ValueError('sweep needs version 1 and stages')
        if getattr(args, 'owner', None) != 'simple' or not getattr(args, 'log', None):
            raise ValueError('continuous sweep requires simple owner and recording')
        for k in ('cell_plan', 'batch_cells', 'gain_trim', 'look_trim', 'gain_low',
                  'manual_knob', 'steer_i', 'path', 'kff', 'relay_udp'):
            if getattr(args, k, None):
                raise ValueError('continuous sweep conflicts with ' + k)
        stages = plan['stages']
        if not 1 <= len(stages) <= MAX_STAGES:
            raise ValueError('sweep needs 1..128 stages')
        self.stages = []
        names = set()
        for stage in stages:
            self.stages.append(check_stage(stage.get('name'), stage.get('seconds'), stage.get('values'), names))
            names.add(self.stages[-1]['name'])
        self.baseline = dict(self.stages[0]['values'])
        if self.stages[-1]['values'] != self.baseline:
            raise ValueError('last stage must restore baseline')
        for k, v in self.baseline.items():
            if not math.isclose(getattr(args, k), v, abs_tol=1e-9):
                raise ValueError('launch baseline disagrees with plan: ' + k)
        self.index = 0
        self.elapsed = 0.
        self.last = None
        self.was_active = False
        self.started = False
        self.complete = False
        self.revision = 0
        self.names = names            # every name the plan has held or reserved; never reused
        self.seen_ids = set()
        self.now_stage = None         # (request id, stage): a `now` waiting for the next eligible frame
        self.ended = False            # the current stage's end row is written (reopened after sweep_complete)

    @classmethod
    def load(cls, path, args):
        return cls(json.loads(Path(path).read_text()), args)

    def _place(self, pos, stage, rid):
        """Insert at `pos`; past the closing stage, a baseline copy follows so the plan still ends there."""
        added = [dict(stage, request=rid)]
        if pos == len(self.stages) and stage['values'] != self.baseline:
            added.append(dict(name='restore-after-' + stage['name'], seconds=self.stages[-1]['seconds'],
                              values=dict(self.baseline), request=rid))
        self.stages[pos:pos] = added
        return [s['name'] for s in added]

    def request(self, rid, op, stage):
        """One parsed inbox command -> (decision, detail). Frame thread only, like tick()."""
        if rid in self.seen_ids:
            return 'duplicate_id', {}
        self.seen_ids.add(rid)
        restore = 'restore-after-' + stage['name']
        if stage['name'] in self.names or restore in self.names:
            return 'name_taken', {}
        superseded = self.now_stage[0] if op == 'now' and self.now_stage is not None else None
        reserved = 2 if self.now_stage is not None and superseded is None else 0
        if len(self.stages) + reserved + 2 > MAX_STAGES:
            return 'plan_full', dict(stages=len(self.stages))
        self.names.update((stage['name'], restore))
        if self.complete:                      # reopen: every op follows the finished plan
            pos = len(self.stages)
            added = self._place(pos, stage, rid)
            self.complete, self.ended = False, True
            return 'accepted', dict(position=pos, added=added, stages=len(self.stages), reopened=True)
        if op == 'now':
            self.now_stage = (rid, stage)
            detail = dict(pending=True, stages=len(self.stages))
            if superseded is not None:
                detail['superseded'] = superseded
            return 'accepted', detail
        pos = self.index + 1 if op == 'next' else max(self.index + 1, len(self.stages) - 1)
        added = self._place(pos, stage, rid)
        return 'accepted', dict(position=pos, added=added, stages=len(self.stages))

    def tick(self, now, active, args):
        events = []
        dt = 0. if self.last is None else now - self.last
        self.last = now
        if self.complete:
            return events
        if active and not self.started:
            self.started = True
            events.append({'event': 'sweep_start'})
        # No catch-up after loss of input or a stalled inference loop.
        if active and self.was_active and 0 <= dt <= .5:
            self.elapsed += dt
        if self.started and active != self.was_active:
            events.append({'event': 'sweep_resume' if active else 'sweep_pause'})
        self.was_active = bool(active)
        cut = self.now_stage
        if active and (cut or self.ended or self.elapsed >= self.stages[self.index]['seconds']):
            if not self.ended:
                events.append({'event': 'sweep_stage_end', 'stage': self.index,
                               'name': self.stages[self.index]['name'], 'elapsed_s': self.elapsed,
                               **({'cut_by': cut[0]} if cut else {})})
            self.ended = False
            added = None
            if cut:
                self.now_stage = None
                added = self._place(self.index + 1, cut[1], cut[0])
            if self.index + 1 == len(self.stages):
                self.complete = True
                events.append({'event': 'sweep_complete'})
            else:
                self.index += 1
                self.elapsed = 0.
                values = self.stages[self.index]['values']
                for k, v in values.items():
                    setattr(args, k, v)
                self.revision += 1
                events.append({'event': 'sweep_apply', 'stage': self.index,
                               'name': self.stages[self.index]['name'], 'values': dict(values),
                               'revision': self.revision,
                               **({'request': self.stages[self.index]['request']}
                                  if 'request' in self.stages[self.index] else {}),
                               **({'added': added} if added else {})})
        return events

    def snapshot(self):
        stage = self.stages[self.index]
        snap = dict(stage=self.index, stages=len(self.stages), name=stage['name'],
                    values=dict(stage['values']), elapsed_s=round(self.elapsed, 3),
                    remaining_s=round(max(0., stage['seconds']-self.elapsed), 3),
                    started=self.started, active=self.was_active, complete=self.complete,
                    revision=self.revision)
        if self.now_stage is not None:         # this stage ends at the next eligible frame
            snap.update(remaining_s=0., pending_now=self.now_stage[0])
        return snap


class SweepInbox:
    """`<dir>/*.json` commands, oldest first. Each file is renamed `.consumed` (parsed; the decision is the
    `sweep_request` row) or `.rejected` with a `.reason`, so a restart never replays one; ids seen in this
    process are refused again. An operator file must never stop the controller."""

    def __init__(self, directory, period_s=.25):
        self.dir = Path(directory); self.period = period_s; self.next_t = 0.
        self.dir.mkdir(parents=True, exist_ok=True)

    def _files(self):
        out = []
        for p in self.dir.glob('*.json'):
            try:
                out.append((p.stat().st_mtime_ns, p.name, p))
            except OSError:
                pass
        return [p for _, _, p in sorted(out)]

    def poll(self, now, sweep):
        """-> `sweep_request` rows, [] when nothing arrived (every other row is then unchanged)."""
        if now < self.next_t:
            return []
        self.next_t = now + self.period
        rows = []
        for p in self._files():
            try:
                d = json.loads(p.read_text())
                rid, op, stage = parse_request(d)
                p.rename(p.with_suffix('.consumed'))
            except Exception as exc:
                try:
                    p.with_suffix('.reason').write_text(f'{type(exc).__name__}: {exc}\n')
                    p.rename(p.with_suffix('.rejected'))
                except OSError:
                    pass
                rows.append(dict(event='sweep_request', file=p.name, decision='invalid',
                                 error=f'{type(exc).__name__}: {exc}'))
                continue
            try:
                decision, detail = sweep.request(rid, op, stage)
            except Exception as exc:
                decision, detail = 'error', dict(error=f'{type(exc).__name__}: {exc}')
            rows.append(dict(event='sweep_request', id=rid, op=op, file=p.name, inbox=str(self.dir),
                             decision=decision, stage=stage, **detail,
                             **({'note': d['note']} if 'note' in d else {})))
        return rows


def eligible(owner, pose, now):
    """Same radio authority; observed movement starts/advances the experiment clock."""
    if (owner.get('mode') != 'assist' or owner.get('veto') or owner.get('override_on')
            or not owner.get('cruise_on') or not owner.get('model_fresh') or pose is None):
        return False
    age, received, speed = pose.age_s, pose.received, pose.speed
    return (all(finite(x) for x in (age, received, speed))
            and 0 <= age + now - received < .3 and speed > .1)

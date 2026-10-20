# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Bounded, read-only FC telemetry startup. No actuator/parameter writes.

511 ACKs do not echo message IDs. Request ATTITUDE first, serialize requests,
record duplicate/late ACK ambiguity, and require independent source cadence.
No retries or scheduler changes; current runtime heading guards remain in force.
A delayed ACK crossing transactions is intrinsically indistinguishable from the
current request; first-ATTITUDE ordering and observed cadence bound this risk,
but cannot prove packet causality. Another command sender must not share the link.
"""
import json
import math
import os
import statistics
import time
from pathlib import Path
from durable_run import sync_directory

ATTITUDE_INTERVAL_US = 50000
ACK_TIMEOUT_S = 0.75
QUIET_S = 0.15
SETTLE_S = 0.25
OBSERVE_S = 2.0
MIN_SAMPLES = 30


def persist(path, receipt):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as f:
        json.dump(receipt, f, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    sync_directory(path.parent)


def cadence(samples, now):
    """Last uninterrupted epoch of finite, advancing uint32 ATTITUDE samples."""
    good = []; rejected = 0; resets = 0
    for stamp, received, values in samples:
        if not isinstance(stamp, int) or not 0 <= stamp < 2**32 or not all(math.isfinite(x) for x in (received, *values)):
            rejected += 1; continue
        if good:
            delta = stamp - good[-1][0]
            if delta == 0:
                rejected += 1; continue
            # Reboot/backwards/wrap explicitly reset, do not blend epochs.
            if delta < 0 or received <= good[-1][1]:
                good = []; resets += 1
        good.append((stamp, received))
    intervals = [b[0] - a[0] for a, b in zip(good, good[1:])]
    host_intervals = [1000 * (b[1] - a[1]) for a, b in zip(good, good[1:])]
    age = 1000 * (now - good[-1][1]) if good else None
    med = statistics.median(intervals) if intervals else None
    p95 = sorted(intervals)[math.ceil(.95 * len(intervals)) - 1] if intervals else None
    maximum = max(intervals) if intervals else None
    ready = (len(good) >= MIN_SAMPLES and med is not None and 40 <= med <= 75
             and p95 <= 100 and maximum <= 200 and age is not None and 0 <= age <= 250
             and max(host_intervals, default=math.inf) <= 250
             and good[-1][1] - good[0][1] >= 1.45)
    return dict(ready=ready, samples=len(good), rejected=rejected, epoch_resets=resets,
                median_ms=med, p95_ms=p95, max_ms=maximum, newest_age_ms=age,
                host_max_gap_ms=max(host_intervals) if host_intervals else None)


def negotiate(connection, consume, path=None, servo=False, clock=time.monotonic, sleep=time.sleep):
    """Receive all telemetry while negotiating. `consume` updates normal FC state."""
    target = (connection.target_system, connection.target_component)
    sender = (connection.source_system, connection.source_component)
    receipt = dict(schema=1, status='negotiating', target=list(target), sender=list(sender),
                   started_monotonic=clock(), started_wall=time.time(),
                   criteria=dict(observe_s=OBSERVE_S, min_samples=MIN_SAMPLES, median_ms=[40,75],
                                 p95_ms_max=100, source_gap_ms_max=200, newest_age_ms_max=250,
                                 host_gap_ms_max=250, min_host_span_s=1.45), requests=[], acks=[],
                   statustext=[], ambiguous=False, ready=False, recovery_ready=False)
    samples = []; pending = None; observe_start = None; poisoned = False

    def drain():
        nonlocal poisoned
        # Bound each drain so a busy FC cannot extend startup indefinitely.
        for _ in range(256):
            msg = connection.recv_match(blocking=False)
            if msg is None:
                break
            now = clock(); kind = msg.get_type()
            source = (msg.get_srcSystem(), msg.get_srcComponent())
            bound = source == target
            if kind == 'COMMAND_ACK' and int(msg.command) == 511:
                recipient = (getattr(msg, 'target_system', 0), getattr(msg, 'target_component', 0))
                addressed = all(v in (None, 0, expected) for v, expected in zip(recipient, sender))
                row = dict(received_monotonic=now, source=list(source), recipient=list(recipient),
                           result=int(msg.result), association='wrong_source_or_recipient')
                if bound and addressed:
                    if pending is None or poisoned or pending.get('ack') is not None or now > pending['deadline']:
                        row['association'] = 'unassociated_ambiguous'; poisoned = True
                    else:
                        row['association'] = 'sole_outstanding'; row['message_id'] = pending['message_id']
                        pending['ack'] = dict(row)
                receipt['acks'].append(row)
            elif kind == 'STATUSTEXT' and bound:
                receipt['statustext'].append(dict(received_monotonic=now, text=str(msg.text),
                                                  severity=int(msg.severity), id=getattr(msg, "id", None),
                                                  chunk_seq=getattr(msg, "chunk_seq", None)))
            elif kind == 'ATTITUDE' and bound and observe_start is not None and now >= observe_start:
                samples.append((msg.time_boot_ms, now, (msg.roll, msg.pitch, msg.yaw,
                                                       msg.rollspeed, msg.pitchspeed, msg.yawspeed)))
            consume(msg, now)

    def wait(seconds):
        until = clock() + seconds
        while clock() < until:
            drain(); sleep(min(.005, max(0, until - clock())))
        drain()

    try:
        # Drain old ACKs before any 511 request. A stale ACK poisons attribution.
        wait(QUIET_S)
        requests = [(30, ATTITUDE_INTERVAL_US), (35, 50000)]
        if servo:
            requests.append((36, 20000))
        for mid, interval in requests:
            now = clock()
            pending = dict(message_id=mid, interval_us=interval, sent_monotonic=now,
                           deadline=now + ACK_TIMEOUT_S, ack=None, timeout=False)
            receipt['requests'].append(pending)
            connection.mav.command_long_send(*target, 511, 0, mid, interval, 0, 0, 0, 0, 0)
            while clock() < pending['deadline'] and pending['ack'] is None:
                drain(); sleep(.005)
            pending['timeout'] = pending['ack'] is None
            if pending['timeout']:
                poisoned = True
            pending = None
            wait(QUIET_S)
        wait(SETTLE_S)
        observe_start = clock()
        wait(OBSERVE_S)
        receipt['cadence'] = cadence(samples, clock())
        first = receipt['requests'][0]
        accepted = first['ack'] is not None and first['ack']['result'] == 0 and not poisoned
        receipt.update(ambiguous=poisoned, accepted=accepted,
                       ready=bool(accepted and receipt['cadence']['ready']),
                       recovery_ready=bool(accepted and receipt['cadence']['ready']))
        receipt['status'] = 'ready' if receipt['ready'] else 'degraded'
        return receipt
    except BaseException as exc:
        receipt.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'error',
                       ambiguous=poisoned, error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        receipt['ended_monotonic'] = clock()
        persist(path, receipt)

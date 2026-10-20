# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""motion_signal.py -- ARKit-free "is the truck moving?" signals, SHADOW LOGGING ONLY (2026-10-09).

Nothing here reaches steering, throttle, recovery or the sweep. live_trail.py builds a MotionSignal only with
--log-motion and writes the result to the frame row as rec["motion"]; no control code reads it.

Offline evaluation and the rule this logs: the stall-signal report (not shipped)
(extract.py computes the signals, analyze.py the trigger). That evaluation ran on the SAVED JPGs, not live frames.

Signals (same definitions as extract.py, so its thresholds can be compared):
  img_mad   mean |a - b| of two ground crops 0.2 s apart. Ground crop = grayscale (BT.601, cv2 BGR2GRAY), rows
            from 420/720 of the image height to the bottom, full width, INTER_AREA to 160x50 uint8. 0-255 units.
  prob_dp1  mean |P - P_prev| of the row-probability map (NR x 34) against the previous FRESH frame.

Live vs saved-JPG scale (measured 2026-10-09 offline; STALL-SIGNAL.md "Limits" for why it matters):
  The definition is resolution-free: the crop is a FRACTION of the height and the output is always 160x50, so a
  480x360 stream frame, the 476x350 `small` feed image and a 960x720 saved JPG cover the same field of view and
  give 160x50 maps in the same units. No rescaling is applied to img_mad. What is NOT the same is the pre-blur
  and the noise: a saved JPG is cv2.resize(bgr 480x360 -> 960x720, INTER_LINEAR) then JPEG q85; live feeds
  `small` = cv2.resize(bgr -> 476x350, INTER_LINEAR) straight from the decoder. Offline estimate on 1630
  rsm-03 JPG pairs (1444 moving, 154 stationary by ARKit), with the 480x360 live frame reconstructed from each
  JPG (INTER_AREA down) and pushed through both paths: JPG-path / live-path img_mad median ratio 1.002 (IQR
  1.001-1.004) on moving pairs, 1.007 against the original JPGs; moving p1 6.51 live vs 6.59 JPG; no moving pair
  < 4.0 on either path; cv2 BGR2GRAY vs libjpeg gray differs by <= 0.006. So LIVE_TO_JPG_SCALE = 1.0 and the 4.0
  threshold is used unscaled. The reconstruction cannot contain detail the JPG round trip already removed, so
  live moving MAD may be a little higher than this (the safe direction for false stalls), and the live
  stationary floor lacks JPEG re-encode noise. 4.0 is a starting point: re-derive it from the shadow log.
Sampling differs too: the JPGs exist every 3rd frame, so analyze.py saw ~3 img_mad samples per 0.6 s window;
live gives one per frame (~9), and "every sample < thr" over 9 samples is stricter (later, rarer flags).
prob_dp1 offline used probs.npz (float16); live uses the float32 probs. The difference is < 1e-3 of the signal.
"""
from collections import deque
from dataclasses import dataclass
import math

import cv2
import numpy as np

CROP_Y0_FRAC = 420 / 720          # extract.py CROP_Y0 on a 960x720 JPG
CROP_W, CROP_H = 160, 50          # extract.py DS_MAD = 6 on the 960x300 crop
LAG_S = 0.2                       # extract.py pairs JPGs 3 frames apart = 0.2 s at 15 fps
LAG_MIN_S, LAG_MAX_S = 0.1, 0.35  # a reference outside this window gives img_mad None
RING = 8                          # ~0.5 s of frames at 15 fps; 8 KB each
# JPG-path / live-path img_mad, measured offline (see the docstring): 1.002, so no correction is applied.
LIVE_TO_JPG_SCALE = 1.0


def ground_crop(img):
    """BGR (H, W, 3) or gray (H, W) uint8 -> the 160x50 uint8 ground crop extract.py measures."""
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    y0 = int(round(g.shape[0] * CROP_Y0_FRAC))
    return cv2.resize(g[y0:], (CROP_W, CROP_H), interpolation=cv2.INTER_AREA)


def mad(a, b):
    return float(cv2.absdiff(a, b).mean())


@dataclass(frozen=True)
class StallShadowConfig:
    """The trigger analyze.py evaluated: driving held >= max(persist, grace) AND every sample in the last persist
    seconds < thr (at least one sample)."""
    signal: str = "img_mad"
    thr: float = 4.0
    persist_s: float = 0.6
    grace_s: float = 1.5


SHADOW_RULES = (StallShadowConfig("img_mad", 4.0, 0.6, 1.5), StallShadowConfig("prob_dp1", 0.04, 0.6, 1.5))
HISTORY_S = max(max(r.persist_s, r.grace_s) for r in SHADOW_RULES) + 1.0


def stall_shadow(history, cfg):
    """Would cfg's rule fire on the newest sample? history: dicts {t, driving, <cfg.signal>}, oldest first.

    Stateless. Same rule as analyze.trigger(): driving_since = t_now - t of the first sample of the trailing
    continuous driving run; None/NaN samples are skipped (a frozen frame never counts as 'not moving')."""
    if not history or not history[-1].get("driving"):
        return False
    t_now = history[-1]["t"]; t0 = t_now
    for h in reversed(history):
        if not h.get("driving"):
            break
        t0 = h["t"]
    if t_now - t0 < max(cfg.persist_s, cfg.grace_s):
        return False
    w = [h.get(cfg.signal) for h in history if h["t"] >= t_now - cfg.persist_s]
    w = [x for x in w if isinstance(x, (int, float)) and math.isfinite(x)]
    return len(w) >= 1 and all(x < cfg.thr for x in w)


class MotionSignal:
    """Per-frame img_mad / prob_dp1 against earlier frames. Shadow logging only."""

    def __init__(self, lag_s=LAG_S, ring=RING, rules=SHADOW_RULES):
        self.lag_s = lag_s; self.rules = rules
        self.ring = deque(maxlen=ring)          # (t_mono, t_pts_s or None, crop)
        self.history = deque()                  # stall_shadow input: {t, driving, img_mad, prob_dp1}
        self.reset()

    def reset(self):
        """Forget every earlier frame (motion_epoch change, stream restart)."""
        self.ring.clear(); self.history.clear()
        self.prev_probs = None; self.prev_probs_t = None; self.last_pts = None

    @staticmethod
    def _t(t_mono, t_pts, ref_mono, ref_pts):
        # capture clock when both frames have one, else the loop clock
        return (t_pts - ref_pts) if (t_pts is not None and ref_pts is not None) else (t_mono - ref_mono)

    def update(self, now_mono, img, probs=None, pts_us=None, driving=None):
        """img: BGR or gray uint8 frame (any size, same field of view as the stream). probs: (NR, 34) or None.
        pts_us: capture stamp of THIS image (None when there is no phone, e.g. --video). driving: the owner's
        OwnerView.driving() this frame, or None to skip the shadow rule.

        Returns {fresh, img_mad, dt_s, prob_dp1, prob_dt_s[, driving, would_stall]}. fresh=False when pts_us did
        not advance (frozen/duplicated decoder output gives MAD 0 and must not read as 'stopped')."""
        out = {"fresh": True, "img_mad": None, "dt_s": None, "prob_dp1": None, "prob_dt_s": None}
        if pts_us is not None and self.last_pts is not None and pts_us < self.last_pts:
            self.reset()                        # stamp went backwards: decoder reopened, old frames are another stream
            out["restart"] = True
        if pts_us is not None and self.last_pts is not None and pts_us <= self.last_pts:
            out["fresh"] = False
        else:
            if pts_us is not None:
                self.last_pts = pts_us
            t_pts = None if pts_us is None else pts_us / 1e6
            crop = ground_crop(img)
            best = None
            for (rm, rp, rc) in self.ring:
                age = self._t(now_mono, t_pts, rm, rp)
                if LAG_MIN_S <= age <= LAG_MAX_S and (best is None or abs(age - self.lag_s) < abs(best[0] - self.lag_s)):
                    best = (age, rc)
            if best is not None:
                out["img_mad"] = round(mad(crop, best[1]), 3); out["dt_s"] = round(best[0], 3)
            self.ring.append((now_mono, t_pts, crop))
            if probs is not None:
                p = np.asarray(probs, dtype=np.float32)
                if self.prev_probs is not None and self.prev_probs.shape == p.shape:
                    out["prob_dp1"] = round(float(np.abs(p - self.prev_probs).mean()), 5)
                    out["prob_dt_s"] = round(self._t(now_mono, t_pts, *self.prev_probs_t), 3)
                self.prev_probs = p; self.prev_probs_t = (now_mono, t_pts)
        if driving is not None:
            self.history.append({"t": now_mono, "driving": bool(driving), "img_mad": out["img_mad"], "prob_dp1": out["prob_dp1"]})
            while self.history and self.history[0]["t"] < now_mono - HISTORY_S:
                self.history.popleft()
            out["driving"] = bool(driving)
            out["would_stall"] = {r.signal: stall_shadow(self.history, r) for r in self.rules}
        return out

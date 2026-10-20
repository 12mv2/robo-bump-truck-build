# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Opt-in Blue #2 colour support gate for the OVAL4 head (default OFF).

The head scores every 14x14 patch of the 476x350 feed; it has learned what blue tape looks
like but nothing restricts it to blue pixels (run09, 2026-10-09: scattered >0.5 mass on box
contents at the storage shelf). This gate keeps a patch's learned probability only where the
SAME feed pixels the perception model saw contain enough blue; elsewhere the probability is capped at
0.5, i.e. it carries zero mass in line_from_probs. No learned score is ever increased.

Colour rule = the 2026-09-24 blue-tape gate (a separate C++ implementation, not shipped):
OpenCV HSV H 88..128, S >= 25, V >= 45, plus B - R >= 10. Patch support = blue pixel
fraction >= min_frac, dilated by `dilate` patches (tape edges, JPEG/resize blur and a
centre head whose mass can sit one patch off the tape).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np

VERSION = 1


@dataclass(frozen=True)
class BlueGateConfig:
    h_lo: int = 88
    h_hi: int = 128
    s_min: int = 25
    v_min: int = 45
    b_minus_r: int = 10
    min_frac: float = 0.04
    dilate: int = 1

    def validate(self):
        if not (0 <= self.h_lo <= self.h_hi <= 179):
            raise ValueError("blue gate: need 0 <= h_lo <= h_hi <= 179 (OpenCV hue)")
        if not (0 <= self.s_min <= 255 and 0 <= self.v_min <= 255 and -255 <= self.b_minus_r <= 255):
            raise ValueError("blue gate: s_min/v_min in 0..255, b_minus_r in -255..255")
        if not (0.0 < self.min_frac <= 1.0):
            raise ValueError("blue gate: min_frac must be in (0, 1]")
        if not (0 <= self.dilate <= 3):
            raise ValueError("blue gate: dilate must be 0..3 patches")
        return self

    def digest(self):
        blob = json.dumps(dict(version=VERSION, **asdict(self)), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    @classmethod
    def from_spec(cls, spec):
        """'' / 'default' -> defaults; else 'key=value,...' over the defaults."""
        values = {}
        for item in (spec or "").split(","):
            item = item.strip()
            if not item or item == "default":
                continue
            key, sep, value = item.partition("=")
            if not sep or key not in cls.__dataclass_fields__:
                raise ValueError(f"blue gate: unknown setting {item!r}")
            values[key] = float(value) if key == "min_frac" else int(value)
        return cls(**values).validate()


def blue_mask(rgb, cfg):
    """uint8 RGB (H, W, 3) -> bool (H, W) blue-tape pixels."""
    import cv2
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    b_r = rgb[..., 2].astype(np.int16) - rgb[..., 0].astype(np.int16)
    return ((h >= cfg.h_lo) & (h <= cfg.h_hi) & (s >= cfg.s_min) & (v >= cfg.v_min)
            & (b_r >= cfg.b_minus_r))


def support(rgb, cfg, r0, nr, patch=14):
    """-> (frac (nr, gw) float32, keep (nr, gw) bool). Patch grid identical to the steering head's."""
    h, w = rgb.shape[:2]
    gh, gw = h // patch, w // patch
    m = blue_mask(rgb[:gh * patch, :gw * patch], cfg)
    frac = m.reshape(gh, patch, gw, patch).mean(axis=(1, 3)).astype(np.float32)[r0:r0 + nr]
    keep = frac >= cfg.min_frac
    for _ in range(cfg.dilate):
        grown = keep.copy()
        grown[1:] |= keep[:-1]; grown[:-1] |= keep[1:]
        grown[:, 1:] |= keep[:, :-1]; grown[:, :-1] |= keep[:, 1:]
        keep = grown
    return frac, keep


def apply(rgb, probs, cfg, r0):
    """Gate head probabilities with blue support. Returns (gated probs, telemetry dict).

    Unsupported patches are capped at 0.5 (zero mass in line_from_probs); supported patches keep
    their learned value unchanged. probs is (NR, GW) for grid rows r0.. of `rgb`'s patch grid.
    """
    nr = probs.shape[0]
    frac, keep = support(rgb, cfg, r0, nr)
    if keep.shape != probs.shape:
        raise ValueError(f"blue gate: support {keep.shape} != probs {probs.shape}")
    gated = np.where(keep, probs, np.minimum(probs, 0.5)).astype(probs.dtype, copy=False)
    raw_mass = float((np.clip(probs - 0.5, 0, None) ** 2).sum())
    kept_mass = float((np.clip(gated - 0.5, 0, None) ** 2).sum())
    tel = dict(on=True, digest=cfg.digest(), supported=int(keep.sum()),
               hot_raw=int((probs > 0.5).sum()), hot_kept=int((gated > 0.5).sum()),
               mass_kept=round(kept_mass / raw_mass, 3) if raw_mass > 0 else None,
               support_bits=np.packbits(keep.ravel()).tobytes().hex())
    return gated, tel

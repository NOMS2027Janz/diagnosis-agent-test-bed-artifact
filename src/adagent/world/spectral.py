"""Spectral mode of the world: channel shapes through a cascade of WSS passbands (docs/campaign/spectral_design.md).

Off unless the network's `spectral` flag is set; every existing arm is unchanged. In spectral mode:
- a channel's signal is a raised-cosine spectrum at the lightpath's baud, roll-off ROLL_OFF, centred at its laser
  frequency: the grid centre plus the transmitter's offset (LASER_FREQ_DRIFT);
- each WSS the channel passes (one per OMS: the WSS at the ROADM after it, as the world already keys it) is a
  super-Gaussian passband of order ORDER, -3 dB bandwidth NOMINAL_BW_GHZ, centred at the grid plus the WSS's offset
  (WSS_FILTER_SHIFT), narrowed (WSS_FILTER_NARROWING: bandwidth reduced, order reduced);
- the filtering penalty is computed from the cascade: the fraction of signal power clipped and the asymmetry of what
  remains, less the nominal cascade's penalty (which line design already allows for). It replaces the summed
  0.02 dB per GHz^2 of the world's default mode, and is calibrated to agree with it for one shifted filter.
Stated assumptions, not a vendor model; the Annex says so.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache

import numpy as np

ROLL_OFF = 0.1


def on() -> bool:
    """Spectral mode is a property of the whole run (the world, the twin and the documentation together)."""
    return os.environ.get("ADAGENT_SPECTRAL") == "1"


ORDER = 4.0
NOMINAL_BW_GHZ = 70.0
F = np.arange(-80.0, 80.0001, 0.25)          # GHz about the grid centre
DF = F[1] - F[0]
CLIP_K = None                                 # set by _calibrate (see below)
ASYM_K = 1.0                                  # dB per unit of asymmetry (power fraction difference)


def signal_psd(baud: float, offset: float) -> np.ndarray:
    """Raised-cosine power spectral density of a channel at `baud` GBd centred at `offset` GHz (unit area)."""
    f = np.abs(F - offset)
    lo, hi = baud * (1 - ROLL_OFF) / 2, baud * (1 + ROLL_OFF) / 2
    s = np.where(f <= lo, 1.0, np.where(f >= hi, 0.0, 0.5 * (1 + np.cos(math.pi / (ROLL_OFF * baud) * (f - lo)))))
    return s / (s.sum() * DF)


def passband(offset: float, bw: float, order: float) -> np.ndarray:
    """Power transfer of a super-Gaussian passband: -3 dB at +-bw/2 about `offset`."""
    return np.exp(-math.log(2) * np.abs(2 * (F - offset) / bw) ** (2 * order))


def cascade(filters) -> np.ndarray:
    h = np.ones_like(F)
    for off, bw, order in filters:
        h = h * passband(off, bw, order)
    return h


def _raw(baud: float, laser: float, filters) -> dict:
    s = signal_psd(baud, laser)
    out = s * cascade(filters)
    kept = out.sum() * DF
    clipped = max(0.0, 1.0 - kept)
    left = out[F < laser].sum() * DF + 0.5 * out[F == laser].sum() * DF
    right = out[F > laser].sum() * DF + 0.5 * out[F == laser].sum() * DF
    asym = (right - left) / kept if kept > 0 else 0.0
    centroid = float((F * out).sum() / out.sum()) if out.sum() > 0 else 0.0
    peak = out.max()
    above = F[out >= peak / 2] if peak > 0 else np.array([0.0])
    width = float(above.max() - above.min()) if above.size else 0.0
    # edge steepness: width between the -3 dB and -20 dB points, averaged over both sides (smaller is steeper)
    a20 = F[out >= peak / 100] if peak > 0 else np.array([0.0])
    edge = float(((a20.max() - a20.min()) - width) / 2) if a20.size else 0.0
    return {"clipped": clipped, "asym": asym, "centroid": centroid, "width": width, "edge": edge,
            "loss_db": -10 * math.log10(kept) if kept > 0 else math.inf}


def _calibrate() -> float:
    """CLIP_K so that one filter shifted by 5 GHz on a 64 GBd channel costs 0.02 x 25 = 0.5 dB more than nominal,
    as the default mode's model does. The penalty is 10 log10(1 + CLIP_K x clipped) + ASYM_K x |asymmetry|: the
    clipped power acts as added distortion noise in proportion to its share."""
    nom = _raw(64.0, 0.0, [(0.0, NOMINAL_BW_GHZ, ORDER)])
    sh = _raw(64.0, 0.0, [(5.0, NOMINAL_BW_GHZ, ORDER)])

    def pen(k, r):
        return 10 * math.log10(1 + k * r["clipped"]) + ASYM_K * abs(r["asym"])
    lo, hi = 0.0, 1000.0
    for _ in range(80):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if pen(k, sh) - pen(k, nom) < 0.5 else (lo, k)
    return (lo + hi) / 2


def _penalty(raw: dict) -> float:
    global CLIP_K
    if CLIP_K is None:
        CLIP_K = _calibrate()
    return 10 * math.log10(1 + CLIP_K * raw["clipped"]) + ASYM_K * abs(raw["asym"])


@lru_cache(maxsize=4096)
def nominal(baud: float, n_filters: int) -> dict:
    r = _raw(baud, 0.0, [(0.0, NOMINAL_BW_GHZ, ORDER)] * n_filters)
    r["penalty"] = _penalty(r)
    return r


@lru_cache(maxsize=200000)
def shape(baud: float, laser: float, filters: tuple) -> dict:
    """The channel as seen after `filters` ((offset, bw, order), ...): features and the penalty over nominal."""
    r = _raw(baud, laser, list(filters))
    r["penalty"] = _penalty(r)
    r["penalty_over_nominal"] = max(0.0, r["penalty"] - nominal(baud, len(filters))["penalty"])
    return r


def wss_filter(fx, oms: str, slot: int) -> tuple:
    off = fx.wss_filter_offset_ghz.get((oms, slot), 0.0)
    narrow = fx.wss_filter_narrow_ghz.get((oms, slot), 0.0)
    order = ORDER - fx.wss_filter_edge.get((oms, slot), 0.0)
    return (round(off, 3), round(NOMINAL_BW_GHZ - narrow, 3), round(max(1.0, order), 3))


def nominal_filter() -> tuple:
    return (0.0, NOMINAL_BW_GHZ, ORDER)

"""World version 2: the hidden gap between the world and what the agent can know (decision D2, 2026-10-16).

Every element is given hidden true values drawn once per network seed, while the plant records, the knowledge
graph and therefore the twin keep the datasheet typical and planned values. Two kinds of difference are drawn:

- Parameter spread: each amplifier's noise figure (datasheet typical 5.5 dB, maximum 6.5 dB) and the residual
  error of its commissioned gain; each span's fibre attenuation (around 0.20 dB/km, within 0.19 to 0.23), its
  connector and splice loss (around 1.0 dB for both ends, within 0.6 to 1.6) and its PMD coefficient.
- Instrument calibration offsets: each power monitor, receiver and channel monitor reads with a fixed offset
  (within about plus or minus 0.5 dB), applied by the simulator to its optical readings.

There are no effects the twin does not model. The errors are independent per element, so the twin's error in
OSNR and margin accumulates along a path: a long path through many amplifiers carries a larger error than a
short one (tests/test_gap.py checks this).

`scale` multiplies every deviation: 0 reproduces world version 1 exactly; 1 is the reference gap; the
experiments vary it as an axis. The draw uses its own random stream, so the topology, services and history
schedule are the same at every scale.

`calibrated` (amendment 4): the spread is drawn centred on the datasheet and planned values, as the residual error of
a twin calibrated against the network's monitors would be. Uncalibrated (the default), fibre attenuation and noise
figures run slightly above the datasheet on average (0.205 dB/km against 0.20; 5.7 dB against 5.5), so the twin is
biased: about 1 dB optimistic in OSNR at scale 1.
"""
from __future__ import annotations

import random
import zlib

from .network import Network

NF_TYP_DB, NF_MAX_DB = 5.5, 6.5
ALPHA_NOM, ALPHA_RANGE = 0.20, (0.19, 0.23)
CONN_NOM, CONN_RANGE = 1.0, (0.6, 1.6)
PMD_NOM, PMD_RANGE = 0.05, (0.02, 0.10)
GAIN_ERR_SIGMA_DB = 0.3
CAL_RANGE_DB = 0.5


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def apply_gap(net: Network, scale: float, seed: int, calibrated: bool = False) -> Network:
    """Draw hidden true values for every element of `net` (in place) and return it. Scale 0 changes nothing."""
    net.gap_scale = float(scale)
    alpha_mean, nf_mean = (ALPHA_NOM, NF_TYP_DB) if calibrated else (0.205, NF_TYP_DB + 0.2)
    if scale <= 0:
        return net
    rng = random.Random(zlib.crc32(f"world-gap/{seed}".encode()))
    for sid in sorted(net.spans):
        sp = net.spans[sid]
        a = _clip(rng.gauss(alpha_mean, 0.008), *ALPHA_RANGE)
        c = _clip(rng.gauss(1.0, 0.25), *CONN_RANGE)
        p = _clip(rng.lognormvariate(-3.0, 0.35), *PMD_RANGE)
        sp.true_loss_db_per_km = ALPHA_NOM + scale * (a - ALPHA_NOM)
        sp.true_connector_loss_db = CONN_NOM + scale * (c - CONN_NOM)
        sp.pmd_ps_sqrt_km = PMD_NOM + scale * (p - PMD_NOM)
    for aid in sorted(net.amps):
        am = net.amps[aid]
        nf = _clip(rng.gauss(nf_mean, 0.3), NF_TYP_DB - 0.5, NF_MAX_DB)
        am.true_nf_db = NF_TYP_DB + scale * (nf - NF_TYP_DB)
    # constant-gain commissioning: each in-line amplifier and pre-amplifier is set to the loss of the span
    # before it as measured at turn-up, with a residual set-point error; the records keep the planned loss
    for o in net.oms.values():
        for i, e in enumerate(o.elements):
            if hasattr(e, "role") and e.index > 0:
                e.gain_db = o.elements[i - 1].actual_loss_db + scale * rng.gauss(0.0, GAIN_ERR_SIGMA_DB)
    cal = {}
    for aid in sorted(net.amps):
        cal[aid] = scale * rng.uniform(-CAL_RANGE_DB, CAL_RANGE_DB)
    for tid in sorted(net.transponders):
        cal[tid] = scale * rng.uniform(-CAL_RANGE_DB, CAL_RANGE_DB)
    for oid in sorted(net.oms):
        cal[f"{net.oms[oid].dst}:{oid}"] = scale * rng.uniform(-CAL_RANGE_DB, CAL_RANGE_DB)
    net.calibration_db = cal
    return net


# ------------------------------------------------------------------ structural mismatch (campaign, stage 1)
# Effects the twin does not model, in the world only. Level 1 and level 2:
#   nli: the world's nonlinear interference relative to the GN closed form the twin uses (x1.4 is about 1.5 dB more,
#        x2.0 about 3 dB);
#   ageing: each amplifier's noise figure raised by an amount drawn uniformly up to this (dB), unknown to the twin;
#   tilt: each in-line amplifier and pre-amplifier given a static gain tilt across the band, standard deviation (dB);
#   conn: connector and splice losses redrawn with this standard deviation (dB) around the nominal, in 0.3 to 3.0.
MISMATCH = {1: dict(nli=1.4, ageing=0.5, tilt=0.5, conn=0.5),
            2: dict(nli=2.0, ageing=1.0, tilt=1.0, conn=0.8)}


def apply_mismatch(net: Network, level: int, seed: int) -> Network:
    """Give the world structural errors the twin does not model (in place). Level 0 changes nothing. Drawn from a
    stream of its own, after the gap. Commissioning gains are re-set to the new span losses, as turn-up would."""
    net.mismatch = int(level)
    if not level:
        return net
    m = MISMATCH[int(level)]
    rng = random.Random(zlib.crc32(f"world-mismatch/{seed}/{level}".encode()))
    net.nli_factor = m["nli"]
    for sid in sorted(net.spans):
        sp = net.spans[sid]
        sp.true_connector_loss_db = _clip(rng.gauss(CONN_NOM, m["conn"]), 0.3, 3.0)
    for aid in sorted(net.amps):
        am = net.amps[aid]
        am.true_nf_db = am.actual_nf_db + rng.uniform(0.0, m["ageing"])
        if am.role != "booster":
            am.true_tilt_db = rng.gauss(0.0, m["tilt"])
    for o in net.oms.values():
        for i, e in enumerate(o.elements):
            if hasattr(e, "role") and e.index > 0:
                e.gain_db = o.elements[i - 1].actual_loss_db + rng.gauss(0.0, GAIN_ERR_SIGMA_DB)
    return net

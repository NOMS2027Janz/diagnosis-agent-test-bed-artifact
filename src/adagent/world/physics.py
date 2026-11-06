"""Steady-state optical physics for the world simulator and, with different parameters, the twin.

Per-channel power is propagated through each OMS with amplifiers in constant-gain mode.
Noise is accumulated as inverse SNR contributions, which are invariant to later uniform
gain and loss: amplified spontaneous emission (ASE) at every amplifier, and nonlinear
interference (NLI) per span from the closed-form Gaussian-noise model (Poggiolini, JLT 2012)
for a fully loaded band. At each ROADM the WSS re-levels the channel to the launch target
within its attenuation headroom. Pre-FEC BER follows from the generalised SNR (GSNR).

Effects are applied through an `Effects` object, built from active faults by faults.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .network import Amplifier, Network, Span

# ------------------------------------------------------------------ constants
H_NU_12G5_DBM = -58.0          # hν·12.5 GHz at 1550 nm, in dBm
LOS_PER_CH_DBM = -30.0         # amplifier input loss-of-signal threshold, per channel
OCH_LOS_DBM = -25.0            # transponder receiver loss-of-signal threshold
FEC_BER = 2.0e-2               # pre-FEC BER limit of the soft-decision FEC
WSS_NOMINAL_ATT_DB = 8.0       # nominal WSS attenuation; its headroom for re-levelling
GAMMA = 1.3e-3                 # 1/(W·m)
BETA2 = 21.7e-27               # s²/m, magnitude
ALPHA_DB_KM = 0.20
D_PS_NM_KM = 17.0
PMD_PS_SQRT_KM = 0.05
DGD_TOLERANCE_PS = 30.0
SOP_TRACKING_RAD_S = 1.0e6      # receiver polarisation-tracking limit; faster events break lock


def db2lin(x: float) -> float:
    return 10 ** (x / 10)


def lin2db(x: float) -> float:
    return 10 * math.log10(x) if x > 0 else -math.inf


def ber_from_snr(snr_lin: float, modulation: str) -> float:
    if snr_lin <= 0:
        return 0.5
    if modulation == "DP-QPSK":
        return 0.5 * math.erfc(math.sqrt(snr_lin / 2))
    return 0.375 * math.erfc(math.sqrt(snr_lin / 10))   # DP-16QAM, Gray-coded approximation


def required_snr_db(modulation: str) -> float:
    lo, hi = 0.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if ber_from_snr(db2lin(mid), modulation) > FEC_BER:
            lo = mid
        else:
            hi = mid
    return hi


def nli_eta_per_w2(span_km: float, baud_gbd: float, band_ghz: float) -> float:
    """Closed-form GN-model NLI coefficient: P_NLI = eta · P_ch³, per span (1/W²)."""
    alpha = ALPHA_DB_KM / (10 * math.log10(math.e)) / 1e3          # 1/m (power)
    L = span_km * 1e3
    l_eff = (1 - math.exp(-alpha * L)) / alpha
    l_eff_a = 1 / alpha
    rs = baud_gbd * 1e9
    btot = band_ghz * 1e9
    g_ref = (8 / 27) * GAMMA ** 2 * l_eff ** 2 * math.asinh(
        (math.pi ** 2 / 2) * BETA2 * l_eff_a * btot ** 2) / (math.pi * BETA2 * l_eff_a)
    # G_NLI = g_ref · G_ch³ with G_ch = P/Rs ; P_NLI = G_NLI · Rs = g_ref · P³ / Rs²
    return g_ref / rs ** 2


# ------------------------------------------------------------------ effects

@dataclass
class Effects:
    span_extra_loss_db: dict[str, float] = field(default_factory=dict)       # span id -> dB (inf = cut)
    span_dgd_extra_ps: dict[str, float] = field(default_factory=dict)
    span_sop_rate: dict[str, float] = field(default_factory=dict)            # rad/s during transient
    oms_input_loss_db: dict[str, float] = field(default_factory=dict)        # connector before booster
    oms_power_excursion_db: dict[str, float] = field(default_factory=dict)   # loading transient
    amp_nf_delta_db: dict[str, float] = field(default_factory=dict)
    amp_gain_delta_db: dict[str, float] = field(default_factory=dict)
    amp_tilt_db: dict[str, float] = field(default_factory=dict)
    amp_failed: set[str] = field(default_factory=set)
    amp_pump_current_pct: dict[str, float] = field(default_factory=dict)
    wss_att_delta_db: dict[tuple[str, int], float] = field(default_factory=dict)   # (oms_in, slot)
    wss_filter_penalty_db: dict[tuple[str, int], float] = field(default_factory=dict)
    trx_tx_failed: set[str] = field(default_factory=set)
    trx_rx_penalty_db: dict[str, float] = field(default_factory=dict)
    trx_laser_bias_pct: dict[str, float] = field(default_factory=dict)
    card_failed: set[str] = field(default_factory=set)                        # transponder ids
    protection_hit: set[str] = field(default_factory=set)                     # lightpath ids
    otn_switch_failed: set[str] = field(default_factory=set)                  # OTN switch ids
    client_port_failed: set[str] = field(default_factory=set)                 # client port ids
    xc_fault: set[tuple[str, str]] = field(default_factory=set)               # (switch, ODU path id)
    wss_port_failed: set[str] = field(default_factory=set)                    # OMS whose arriving WSS port failed
    # spectral mode only (world/spectral.py; the network's `spectral` flag): passband and laser offsets
    wss_filter_offset_ghz: dict[tuple[str, int], float] = field(default_factory=dict)
    wss_filter_narrow_ghz: dict[tuple[str, int], float] = field(default_factory=dict)
    wss_filter_edge: dict[tuple[str, int], float] = field(default_factory=dict)      # reduction of the passband order
    trx_laser_offset_ghz: dict[str, float] = field(default_factory=dict)


# ------------------------------------------------------------------ snapshot

@dataclass
class AmpReading:
    input_dbm_per_ch: float
    output_dbm_per_ch: float
    gain_db: float
    pump_current_pct: float
    failed: bool


@dataclass
class RxReading:
    lightpath: str
    direction: str              # "fwd" (a->z) or "rev"
    trx: str
    rx_power_dbm: float
    osnr_db: float              # ASE-only, 12.5 GHz reference
    gsnr_db: float
    margin_db: float
    pre_fec_ber: float
    cd_ps_nm: float
    dgd_ps: float
    sop_rate_rad_s: float
    tx_failed: bool


@dataclass
class Snapshot:
    amps: dict[str, AmpReading]
    span_loss_db: dict[str, float]
    wss_att_db: dict[tuple[str, int], float]                 # at the ROADM after each OMS
    ocm: dict[tuple[str, int], tuple[float, float]]          # (oms, slot) -> (power, osnr) at degree in
    rx: dict[tuple[str, str], RxReading]                     # (lightpath, direction)
    oms_los: dict[str, Optional[str]]                        # oms -> first amp with input LOS
    osc_los: set[str]                                        # spans whose OSC is lost
    laser_bias_pct: dict[str, float] = field(default_factory=dict)  # transponder -> bias, % of nominal (100 if absent)
    # spectral mode only: the channel as seen at the end of each span ((span, slot) -> features) and at each receiver
    # ((lightpath, direction) -> features); features from world/spectral.shape plus the channel power at that point
    spectra_span: dict = field(default_factory=dict)
    spectra_rx: dict = field(default_factory=dict)


def _propagate_oms(net: Network, oms_id: str, launch_dbm: float, slot: int, fx: Effects,
                   snap_amps: dict, snap_spans: dict, baud: float) -> tuple[float, float, float, Optional[str]]:
    """Return (power at OMS output, inverse SNR from ASE, inverse SNR from NLI, first LOS amp)."""
    o = net.oms[oms_id]
    band = net.channels_loaded * net.slot_ghz
    p = launch_dbm - fx.oms_input_loss_db.get(oms_id, 0.0) + fx.oms_power_excursion_db.get(oms_id, 0.0)
    inv_ase = inv_nli = 0.0
    first_los = None
    for e in o.elements:
        if isinstance(e, Span):
            extra = fx.span_extra_loss_db.get(e.id, 0.0)
            loss = e.actual_loss_db + extra
            snap_spans[e.id] = loss
            if p > -math.inf and loss < math.inf:
                p_w = db2lin(p) / 1e3
                inv_nli += net.nli_factor * nli_eta_per_w2(e.length_km, baud, band) * p_w ** 2
            p = p - loss
        else:
            amp: Amplifier = e
            p_in = p
            failed = amp.id in fx.amp_failed
            gain = amp.gain_db + fx.amp_gain_delta_db.get(amp.id, 0.0)
            if slot >= 0 and amp.id in fx.amp_tilt_db:          # gain tilt across the band, zero at mid-band
                gain += fx.amp_tilt_db[amp.id] * (slot - 31.5) / 63.0
            if slot >= 0 and amp.true_tilt_db:                  # hidden static tilt (structural mismatch)
                gain += amp.true_tilt_db * (slot - 31.5) / 63.0
            if amp.role == "booster":
                gain = 0.0 + fx.amp_gain_delta_db.get(amp.id, 0.0)   # booster referenced to launch
            if p_in < LOS_PER_CH_DBM and first_los is None and amp.role != "booster":
                first_los = amp.id
            p_out = -math.inf if failed else p_in + gain
            nf = amp.actual_nf_db + fx.amp_nf_delta_db.get(amp.id, 0.0)
            if not failed and p_in > -math.inf:
                if amp.role == "booster":     # booster output is the launch; its input is ~16 dB lower
                    g_eff, p_sig_out = 16.0, p_out
                else:
                    g_eff, p_sig_out = gain, p_out
                p_ase = H_NU_12G5_DBM + nf + g_eff + 10 * math.log10(baud / 12.5)
                inv_ase += db2lin(p_ase - p_sig_out)
            snap_amps.setdefault(amp.id, AmpReading(p_in, p_out, gain if not failed else -math.inf,
                                                    fx.amp_pump_current_pct.get(amp.id, amp.pump_current_pct),
                                                    failed))
            p = p_out
    return p, inv_ase, inv_nli, first_los


def evaluate(net: Network, fx: Optional[Effects] = None) -> Snapshot:
    fx = fx or Effects()
    amps: dict[str, AmpReading] = {}
    spans: dict[str, float] = {}
    wss: dict[tuple[str, int], float] = {}
    ocm: dict[tuple[str, int], tuple[float, float]] = {}
    rx: dict[tuple[str, str], RxReading] = {}
    oms_los: dict[str, Optional[str]] = {}
    spectra_span: dict = {}
    spectra_rx: dict = {}
    sp_keys = set(fx.wss_filter_offset_ghz) | set(fx.wss_filter_narrow_ghz) | set(fx.wss_filter_edge)
    span_wanted = getattr(net, "span_spectra_wanted", False)
    rx_wanted = getattr(net, "rx_spectra_wanted", False)

    # amplifier and span state does not depend on slot for a flat band; propagate a nominal
    # channel once per OMS to populate readings, then each lightpath for its own budget.
    for oid in net.oms:
        _, _, _, los = _propagate_oms(net, oid, net.launch_dbm_per_ch, -1, fx, amps, spans, 64.0)
        oms_los[oid] = los
    osc_los = {sid for sid, loss in spans.items()
               if loss == math.inf or loss - net.spans[sid].actual_loss_db > 12.0}

    for lp in net.lightpaths.values():
        for direction in ("fwd", "rev"):
            if direction == "fwd":
                path, tx_id, rx_id = lp.oms_path, lp.src_trx, lp.dst_trx
            else:
                nodes = lp.nodes[::-1]
                path = [net.oms_between(nodes[j], nodes[j + 1]) for j in range(len(nodes) - 1)]
                tx_id, rx_id = lp.dst_trx, lp.src_trx
            tx, rxt = net.transponders[tx_id], net.transponders[rx_id]
            tx_dead = tx_id in fx.trx_tx_failed or tx_id in fx.card_failed
            p = -math.inf if tx_dead else net.launch_dbm_per_ch
            inv_ase = inv_nli = 0.0
            cd = dgd2 = sop = 0.0
            spec = getattr(net, "spectral", False)
            laser = fx.trx_laser_offset_ghz.get(tx_id, 0.0) if spec else 0.0
            # fast path: a channel with nothing spectral on its path has the nominal cascade (no penalty); its
            # shapes are computed only when a rung reads them
            affected = spec and (laser != 0.0 or any((o, lp.slot) in sp_keys for o in path))
            need = spec and (affected or span_wanted)
            filt: list = []
            for oid in path:
                p_in_oms = p
                p_out, a, n, _ = _propagate_oms(net, oid, p, lp.slot, fx, {}, {}, tx.baud_gbd)
                if need and p_in_oms > -math.inf:
                    from . import spectral
                    sh = spectral.shape(tx.baud_gbd, round(laser, 3), tuple(filt))
                    # the channel at each span end: the cumulative filtering so far; its power from the OMS's
                    # nominal channel profile, offset by this channel's own launch into the OMS
                    # every span end of an OMS sees the same cascade; the test bed reads the first (see
                    # knowledge/ladder/telemetry.yaml, rung S)
                    for sp in (net.oms[oid].spans[:1] if getattr(net, "span_spectra_wanted", False) else ()):
                        nxt = amps.get(_amp_after(net, oid, sp))
                        pw = (nxt.input_dbm_per_ch + (p_in_oms - net.launch_dbm_per_ch)) if nxt else p_out
                        spectra_span[(sp.id, lp.slot)] = {**sh, "power": pw}
                    filt.append(spectral.wss_filter(fx, oid, lp.slot))
                inv_ase += a
                inv_nli += n
                for sp in net.oms[oid].spans:
                    cd += D_PS_NM_KM * sp.length_km
                    dgd2 += ((sp.pmd_ps_sqrt_km if sp.pmd_ps_sqrt_km is not None else PMD_PS_SQRT_KM) * math.sqrt(sp.length_km)) ** 2 + fx.span_dgd_extra_ps.get(sp.id, 0.0) ** 2
                    sop = max(sop, fx.span_sop_rate.get(sp.id, 0.0))
                # WSS at the ROADM after this OMS re-levels the channel to the launch target
                # within its attenuation headroom; a drift fault adds unwanted attenuation.
                osnr_here = lin2db(1 / inv_ase) + 10 * math.log10(tx.baud_gbd / 12.5) if inv_ase > 0 else 60.0
                ocm[(oid, lp.slot)] = (p_out, osnr_here)
                if p_out == -math.inf:
                    wss[(oid, lp.slot)] = WSS_NOMINAL_ATT_DB
                    p = -math.inf
                    continue
                if oid in fx.wss_port_failed:                  # the WSS port this OMS arrives on has failed
                    wss[(oid, lp.slot)] = math.inf
                    p = -math.inf
                    continue
                d = net.launch_dbm_per_ch - p_out
                att_set = max(0.0, WSS_NOMINAL_ATT_DB - d)
                att_actual = att_set + fx.wss_att_delta_db.get((oid, lp.slot), 0.0)
                wss[(oid, lp.slot)] = att_actual
                p = p_out + WSS_NOMINAL_ATT_DB - att_actual
            rx_power = (p - (net.launch_dbm_per_ch - net.drop_target_dbm)) if p > -math.inf else -math.inf
            dgd = math.sqrt(dgd2)
            if spec and affected:
                from . import spectral
                sh_rx = spectral.shape(tx.baud_gbd, round(laser, 3), tuple(filt))
                filt_pen = sh_rx["penalty_over_nominal"]
                spectra_rx[(lp.id, direction)] = dict(sh_rx)
            elif spec:
                filt_pen = 0.0
                if rx_wanted:
                    from . import spectral
                    nom = spectral.nominal(tx.baud_gbd, len(path))
                    spectra_rx[(lp.id, direction)] = {**nom, "penalty_over_nominal": 0.0}
            else:
                filt_pen = sum(fx.wss_filter_penalty_db.get((o, lp.slot), 0.0) for o in path)
            penalties = (fx.trx_rx_penalty_db.get(rx_id, 0.0)
                         + filt_pen
                         + max(0.0, (dgd - DGD_TOLERANCE_PS) * 0.2)
                         + (15.0 if sop > SOP_TRACKING_RAD_S else 3.0 if sop > 1e5 else 0.0)
                         + (20.0 if lp.id in fx.protection_hit else 0.0))
            if rx_id in fx.card_failed:                 # a failed card measures nothing
                rx_power = -math.inf
            if rx_power == -math.inf or rx_id in fx.card_failed:
                gsnr = osnr = -math.inf
                ber, margin = 0.5, -math.inf
            else:
                inv_total = inv_ase + inv_nli + 1 / db2lin(rxt.trx_snr_db)
                gsnr = lin2db(1 / inv_total) - penalties
                osnr = (lin2db(1 / inv_ase) + 10 * math.log10(tx.baud_gbd / 12.5)) if inv_ase > 0 else 60.0
                ber = ber_from_snr(db2lin(gsnr), rxt.modulation)
                margin = gsnr - required_snr_db(rxt.modulation)
            rx[(lp.id, direction)] = RxReading(lp.id, direction, rx_id, rx_power, osnr, gsnr, margin,
                                               ber, cd, dgd, sop, tx_dead)
    return Snapshot(amps, spans, wss, ocm, rx, oms_los, osc_los, laser_bias_pct=dict(fx.trx_laser_bias_pct),
                    spectra_span=spectra_span, spectra_rx=spectra_rx)


def _amp_after(net, oid: str, sp) -> Optional[str]:
    """The amplifier at the end of a span (its input sees the span's output), or None for the OMS's last span."""
    o = net.oms[oid]
    nxt = sp.index + 1
    for a in o.amps:
        if a.index == nxt:
            return a.id
    return None

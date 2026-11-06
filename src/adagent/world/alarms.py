"""Alarm model: defects detected from a physics snapshot, with suppression and propagation in
the manner of ITU-T G.798 at a vendor-neutral level of detail.

`defects(net, snap)` returns the set of standing defect conditions at an instant. The simulator
applies soak (raise) and clear timers to turn conditions into raised and cleared alarms, so a
transient shorter than the soak time never appears as an alarm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .network import Network
from .physics import LOS_PER_CH_DBM, OCH_LOS_DBM, FEC_BER, Snapshot

SOAK_RAISE_S = 2.5
SOAK_CLEAR_S = 10.0

SEVERITY = {
    "OTS_LOS": "critical", "OMS_LOS_P": "critical", "OSC_LOS": "major", "AMP_INPUT_LOS": "critical",
    "AMP_FAIL": "critical", "AMP_GAIN_OOR": "minor", "AMP_PUMP_EOL": "minor",
    "OCH_LOS": "critical", "OTU_LOF": "critical", "OTU_SD": "minor", "OTU_BDI": "not_alarmed",
    "ODU_AIS": "not_alarmed", "EQPT_TX_FAIL": "critical", "EQPT_CARD_FAIL": "critical",
    "LASER_BIAS_HIGH": "minor",
    "CLIENT_SF": "critical", "ODU_BDI": "not_alarmed", "ODU_TIM": "major", "PROT_ACTIVE": "minor",
    "PROT_UNAVAIL": "minor", "OTN_FABRIC_FAIL": "critical", "EQPT_PORT_FAIL": "critical",
}


@dataclass(frozen=True)
class Defect:
    type: str
    element: str

    @property
    def severity(self) -> str:
        return SEVERITY[self.type]


def defects(net: Network, snap: Snapshot, sd_margin_db: float = 1.0,
            laser_bias: dict[str, float] | None = None, cards: set[str] | None = None) -> set[Defect]:
    out: set[Defect] = set()
    # --- line side: the first amplifier in an OMS that sees input loss of signal raises it;
    # downstream amplifiers are suppressed by forward defect indication on the OSC.
    for oms_id, first in snap.oms_los.items():
        if first is None:
            continue
        amp = net.amps[first]
        out.add(Defect("OMS_LOS_P" if amp.role == "preamp" else "AMP_INPUT_LOS", first))
    for sid in snap.osc_los:
        out.add(Defect("OSC_LOS", sid))
    for aid, r in snap.amps.items():
        amp = net.amps[aid]
        if r.failed:
            out.add(Defect("AMP_FAIL", aid))
        elif r.input_dbm_per_ch > LOS_PER_CH_DBM and amp.role != "booster" \
                and abs(r.gain_db - amp.gain_db) > 2.0:
            out.add(Defect("AMP_GAIN_OOR", aid))
        if r.pump_current_pct > 120.0:
            out.add(Defect("AMP_PUMP_EOL", aid))
    # --- client side
    lost_paths: set[str] = set()
    for (lp_id, direction), rx in snap.rx.items():
        lp = net.lightpaths[lp_id]
        far_tx = lp.src_trx if direction == "fwd" else lp.dst_trx
        if rx.tx_failed and far_tx not in (cards or set()):
            out.add(Defect("EQPT_TX_FAIL", far_tx))
        if rx.trx in (cards or set()):             # a failed card's traffic defects are masked
            out.add(Defect("OTU_BDI", far_tx))
            continue
        if rx.rx_power_dbm < OCH_LOS_DBM:
            out.add(Defect("OCH_LOS", rx.trx))      # the far end loses signal whatever the reason
            lost_paths.add(lp_id)
            out.add(Defect("OTU_BDI", far_tx))
            if not net.odu_paths:         # single-lightpath networks: ODU per lightpath
                out.add(Defect("ODU_AIS", f"svc.{lp_id}@{rx.trx}"))
        elif rx.pre_fec_ber >= FEC_BER:
            out.add(Defect("OTU_LOF", rx.trx))
            out.add(Defect("OTU_BDI", far_tx))
            if not net.odu_paths:
                out.add(Defect("ODU_AIS", f"svc.{lp_id}@{rx.trx}"))
        elif rx.margin_db < sd_margin_db:
            out.add(Defect("OTU_SD", rx.trx))
    for trx in (cards or set()):
        out.add(Defect("EQPT_CARD_FAIL", trx))
    for trx, pct in (laser_bias or {}).items():
        if pct > 120.0:
            out.add(Defect("LASER_BIAS_HIGH", trx))
    return out

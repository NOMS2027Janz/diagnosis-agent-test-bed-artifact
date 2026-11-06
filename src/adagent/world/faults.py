"""Fault-mode catalogue (specification Appendix A.3) and the mapping from injected events to
physical effects at a given time.

An `Event` is what a scenario injects: a cause plus a temporal profile. `effects_at(t)`
combines all events active at time t into one `Effects` object for physics.evaluate().
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import FaultMode, Nature, ParameterSpec
from .physics import Effects

P = ParameterSpec

CATALOGUE: dict[str, FaultMode] = {fm.id: fm for fm in [
    FaultMode(id="FIBRE_CUT", applies_to=["FibreSpan"], nature=Nature.HARD,
              parameters=[P(name="position_km", unit="km", low=0, high=200)], base_rate_per_year=0.05,
              signature={"amp_input_power": "lost downstream", "osc": "lost on span",
                         "rx_power": "lost on every channel through the span"}),
    FaultMode(id="CONNECTOR_PULLED", applies_to=["Port", "PatchPanel", "OMS"], nature=Nature.HARD,
              base_rate_per_year=0.02, signature={"launch_power": "lost at OMS input"}),
    FaultMode(id="AMP_PUMP_FAIL", applies_to=["Amplifier"], nature=Nature.HARD, base_rate_per_year=0.02,
              signature={"amp_output_power": "lost at the amplifier", "amp_input_power": "normal at the amplifier, lost downstream"}),
    FaultMode(id="TX_LASER_FAIL", applies_to=["CoherentTransponder"], nature=Nature.HARD, base_rate_per_year=0.01,
              signature={"rx_power": "lost on one channel at the far end"}),
    FaultMode(id="CARD_FAIL", applies_to=["Card", "CoherentTransponder"], nature=Nature.HARD, base_rate_per_year=0.01),
    FaultMode(id="WSS_PORT_FAIL", applies_to=["WSS"], nature=Nature.HARD, base_rate_per_year=0.005),
    FaultMode(id="SPAN_LOSS_INCREASE", applies_to=["FibreSpan"], nature=Nature.SOFT,
              parameters=[P(name="added_loss_db", unit="dB", low=0.5, high=15),
                          P(name="position_km", unit="km", low=0, high=200)], base_rate_per_year=0.2,
              signature={"amp_input_power": "down at the next amplifier", "osnr": "down downstream",
                         "amp_gain": "unchanged"}),
    FaultMode(id="CONNECTOR_DIRTY", applies_to=["Port", "PatchPanel", "OMS"], nature=Nature.SOFT,
              parameters=[P(name="added_loss_db", unit="dB", low=0.5, high=6)], base_rate_per_year=0.3),
    FaultMode(id="AMP_NF_RISE", applies_to=["Amplifier"], nature=Nature.SOFT,
              parameters=[P(name="nf_increase_db", unit="dB", low=0.5, high=8)], base_rate_per_year=0.05,
              signature={"osnr": "down downstream", "amp_output_power": "unchanged"}),
    FaultMode(id="AMP_GAIN_DROP", applies_to=["Amplifier"], nature=Nature.SOFT,
              parameters=[P(name="gain_drop_db", unit="dB", low=0.5, high=10)], base_rate_per_year=0.05,
              signature={"amp_output_power": "down at the amplifier", "amp_input_power": "normal at the amplifier, down at the next",
                         "amp_gain": "down"}),
    FaultMode(id="AMP_GAIN_TILT", applies_to=["Amplifier"], nature=Nature.SOFT,
              parameters=[P(name="tilt_db", unit="dB", low=0.5, high=5)], base_rate_per_year=0.03),
    FaultMode(id="WSS_ATTEN_DRIFT", applies_to=["WSS"], nature=Nature.SOFT,
              parameters=[P(name="attenuation_db", unit="dB", low=0.5, high=10)], base_rate_per_year=0.05,
              signature={"rx_power": "down on one channel", "wss_attenuation": "up on that channel"}),
    FaultMode(id="WSS_FILTER_SHIFT", applies_to=["WSS"], nature=Nature.SOFT,
              parameters=[P(name="offset_ghz", unit="GHz", low=1, high=15)], base_rate_per_year=0.03,
              signature={"gsnr": "down on one channel", "rx_power": "unchanged"}),
    # spectral mode only (world/spectral.py): drawn only for the spectral case set
    FaultMode(id="WSS_FILTER_NARROWING", applies_to=["WSS"], nature=Nature.SOFT,
              parameters=[P(name="narrowing_ghz", unit="GHz", low=4, high=20)],
              base_rate_per_year=0.02, signature={"gsnr": "down on one channel", "rx_power": "about unchanged"}),
    FaultMode(id="LASER_FREQ_DRIFT", applies_to=["CoherentTransponder"], nature=Nature.SOFT,
              parameters=[P(name="offset_ghz", unit="GHz", low=3, high=12)],
              base_rate_per_year=0.02, signature={"gsnr": "down on the channel it transmits"}),
    FaultMode(id="RX_DEGRADATION", applies_to=["CoherentTransponder"], nature=Nature.SOFT,
              parameters=[P(name="penalty_db", unit="dB", low=0.5, high=8)], base_rate_per_year=0.05,
              signature={"gsnr": "down at one receiver only", "osnr_at_ocm": "unchanged"}),
    FaultMode(id="DGD_RISE", applies_to=["FibreSpan"], nature=Nature.SOFT,
              parameters=[P(name="dgd_increase_ps", unit="ps", low=5, high=80)], base_rate_per_year=0.01),
    FaultMode(id="SOP_TRANSIENT", applies_to=["FibreSpan"], nature=Nature.TRANSIENT,
              parameters=[P(name="peak_rate_rad_s", unit="rad/s", low=1e4, high=5e6, prior="log_uniform"),
                          P(name="duration_s", unit="s", low=0.001, high=5)], base_rate_per_year=2.0,
              signature={"sop_rate": "spike on all channels through the span", "ber": "burst"}),
    FaultMode(id="MECHANICAL_DISTURBANCE", applies_to=["FibreSpan", "PatchPanel"], nature=Nature.TRANSIENT,
              parameters=[P(name="loss_excursion_db", unit="dB", low=0.5, high=20),
                          P(name="duration_s", unit="s", low=0.1, high=60)], base_rate_per_year=1.0),
    FaultMode(id="PROTECTION_SWITCH_HIT", applies_to=["ProtectionGroup"], nature=Nature.TRANSIENT,
              parameters=[P(name="hit_ms", unit="ms", low=10, high=60)], base_rate_per_year=0.5),
    FaultMode(id="CHANNEL_LOADING_TRANSIENT", applies_to=["OMS"], nature=Nature.TRANSIENT,
              parameters=[P(name="excursion_db", unit="dB", low=-6, high=6),
                          P(name="duration_s", unit="s", low=0.01, high=2)], base_rate_per_year=1.0),
    FaultMode(id="PUMP_CURRENT_DRIFT", applies_to=["Amplifier"], nature=Nature.INCIPIENT,
              parameters=[P(name="pct_per_day", unit="%/day", low=0.1, high=5)], base_rate_per_year=0.05),
    FaultMode(id="LASER_BIAS_DRIFT", applies_to=["CoherentTransponder"], nature=Nature.INCIPIENT,
              parameters=[P(name="pct_per_day", unit="%/day", low=0.1, high=5)], base_rate_per_year=0.05),
    FaultMode(id="MARGIN_EROSION", applies_to=["OCh"], nature=Nature.INCIPIENT,
              parameters=[P(name="db_per_day", unit="dB/day", low=0.01, high=1)], base_rate_per_year=0.05),
    FaultMode(id="OTN_SWITCH_FAIL", applies_to=["OTNSwitch"], nature=Nature.HARD, base_rate_per_year=0.005),
    FaultMode(id="CLIENT_PORT_FAIL", applies_to=["ClientPort"], nature=Nature.HARD, base_rate_per_year=0.01),
    FaultMode(id="XC_MISCONNECT", applies_to=["OTNSwitch"], nature=Nature.HARD, base_rate_per_year=0.02),
    FaultMode(id="MAINTENANCE_ACTIVITY", applies_to=["Site", "FibreSpan"], nature=Nature.CONTEXT),
    FaultMode(id="PROVISIONING_CHANGE", applies_to=["OCh", "OMS"], nature=Nature.CONTEXT),
    FaultMode(id="WEATHER_EXPOSURE", applies_to=["FibreSpan"], nature=Nature.CONTEXT),
    FaultMode(id="INSTRUMENT_MISREPORT", applies_to=["Instrument"], nature=Nature.SUBSTRATE,
              parameters=[P(name="bias", unit="native", low=-10, high=10)], base_rate_per_year=0.02),
    FaultMode(id="TELEMETRY_STALE", applies_to=["Instrument"], nature=Nature.SUBSTRATE,
              parameters=[P(name="staleness_s", unit="s", low=60, high=86400)], base_rate_per_year=0.1),
    FaultMode(id="INVENTORY_DRIFT", applies_to=["Record"], nature=Nature.SUBSTRATE, base_rate_per_year=0.1),
]}


@dataclass
class Event:
    """An injected cause with a temporal profile (specification §9.1)."""
    fault_mode: str
    target: str                         # element id (span, amp, oms, transponder, (oms,slot) key as "oms|slot")
    params: dict[str, float] = field(default_factory=dict)
    onset: float = 0.0
    duration: Optional[float] = None    # None = persists
    profile: str = "step"               # step | pulse | ramp | recurring
    period: Optional[float] = None      # for recurring
    pulse_len: Optional[float] = None   # for recurring

    def level(self, t: float) -> float:
        """Fraction of full effect at time t (0..1); ramps return elapsed days."""
        if t < self.onset:
            return 0.0
        if self.duration is not None and t >= self.onset + self.duration:
            return 0.0
        if self.profile == "recurring" and self.period and self.pulse_len:
            return 1.0 if ((t - self.onset) % self.period) < self.pulse_len else 0.0
        if self.profile == "ramp":
            return (t - self.onset) / 86400.0
        return 1.0


def effects_at(events: list[Event], t: float) -> Effects:
    fx = Effects()
    for ev in events:
        lv = ev.level(t)
        if lv <= 0:
            continue
        m, x, p = ev.fault_mode, ev.target, ev.params
        if m == "FIBRE_CUT":
            fx.span_extra_loss_db[x] = math.inf
        elif m == "SPAN_LOSS_INCREASE":
            fx.span_extra_loss_db[x] = fx.span_extra_loss_db.get(x, 0) + p["added_loss_db"]
        elif m == "MECHANICAL_DISTURBANCE":
            fx.span_extra_loss_db[x] = fx.span_extra_loss_db.get(x, 0) + p["loss_excursion_db"]
        elif m == "CONNECTOR_PULLED":
            fx.oms_input_loss_db[x] = math.inf
        elif m == "CONNECTOR_DIRTY":
            fx.oms_input_loss_db[x] = fx.oms_input_loss_db.get(x, 0) + p["added_loss_db"]
        elif m == "AMP_PUMP_FAIL":
            fx.amp_failed.add(x)
        elif m == "AMP_NF_RISE":
            fx.amp_nf_delta_db[x] = p["nf_increase_db"]
        elif m == "AMP_GAIN_DROP":
            fx.amp_gain_delta_db[x] = -p["gain_drop_db"]
        elif m == "AMP_GAIN_TILT":
            fx.amp_tilt_db[x] = p["tilt_db"]
        elif m == "WSS_PORT_FAIL":                 # target: the OMS whose arriving WSS port failed
            fx.wss_port_failed.add(x)
        elif m == "WSS_ATTEN_DRIFT":
            oms, slot = x.split("|")
            fx.wss_att_delta_db[(oms, int(slot))] = p["attenuation_db"]
        elif m == "WSS_FILTER_SHIFT":
            oms, slot = x.split("|")
            fx.wss_filter_penalty_db[(oms, int(slot))] = 0.02 * p["offset_ghz"] ** 2
            fx.wss_filter_offset_ghz[(oms, int(slot))] = p["offset_ghz"]      # read in spectral mode only
        elif m == "WSS_FILTER_NARROWING":          # spectral mode only
            oms, slot = x.split("|")
            fx.wss_filter_narrow_ghz[(oms, int(slot))] = p["narrowing_ghz"]
            fx.wss_filter_edge[(oms, int(slot))] = p.get("edge_loss", 0.0)
        elif m == "LASER_FREQ_DRIFT":              # spectral mode only; target: the transmitting transponder
            fx.trx_laser_offset_ghz[x] = p["offset_ghz"]      # the laser's frequency off its grid point
        elif m == "TX_LASER_FAIL":
            fx.trx_tx_failed.add(x)
        elif m == "CARD_FAIL":
            fx.card_failed.add(x)
        elif m == "RX_DEGRADATION":
            fx.trx_rx_penalty_db[x] = p["penalty_db"]
        elif m == "DGD_RISE":
            fx.span_dgd_extra_ps[x] = p["dgd_increase_ps"]
        elif m == "SOP_TRANSIENT":
            fx.span_sop_rate[x] = p["peak_rate_rad_s"]
        elif m == "CHANNEL_LOADING_TRANSIENT":
            fx.oms_power_excursion_db[x] = p["excursion_db"]
        elif m == "PROTECTION_SWITCH_HIT":
            fx.protection_hit.add(x)
        elif m == "PUMP_CURRENT_DRIFT":
            fx.amp_pump_current_pct[x] = 100.0 * (1 + p["pct_per_day"] / 100 * lv)
        elif m == "LASER_BIAS_DRIFT":
            fx.trx_laser_bias_pct[x] = 100.0 * (1 + p["pct_per_day"] / 100 * lv)
        elif m == "MARGIN_EROSION":                 # target: the receiving transponder of the channel
            fx.trx_rx_penalty_db[x] = fx.trx_rx_penalty_db.get(x, 0) + p["db_per_day"] * lv
        elif m == "OTN_SWITCH_FAIL":
            fx.otn_switch_failed.add(x)
        elif m == "CLIENT_PORT_FAIL":
            fx.client_port_failed.add(x)
        elif m == "XC_MISCONNECT":                 # target "otn.<site>|<ODU path id>"
            sw, path = x.split("|")
            fx.xc_fault.add((sw, path))
        # context and substrate modes have no physical effect; the simulator handles them
    return fx

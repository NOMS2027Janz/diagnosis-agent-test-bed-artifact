"""World simulator checks: physics sanity, the masquerade, alarm logic, transients and drift."""
import math

import pytest

from adagent.world.faults import CATALOGUE, Event, effects_at
from adagent.world.network import reference_line, reference_mesh
from adagent.world.physics import db2lin, evaluate, lin2db, nli_eta_per_w2
from adagent.world.simulator import WorldSim


@pytest.fixture(scope="module")
def line():
    return reference_line()


def alarm_set(lake):
    return {(o.observable, o.element) for o in lake.query(source="alarm")}


def test_optimum_launch_near_one_dbm_for_80km_span():
    def gsnr(p):
        snr_nli = 1 / (nli_eta_per_w2(80, 64, 4800) * (db2lin(p) / 1e3) ** 2)
        osnr_ase = db2lin(p + 58 - 5.5 - 17 - 10 * math.log10(64 / 12.5))
        return lin2db(1 / (1 / snr_nli + 1 / osnr_ase))
    best = max(range(-3, 6), key=gsnr)
    assert best in (0, 1, 2)


def test_nominal_network_is_healthy(line):
    snap = evaluate(line)
    assert all(r.margin_db > 2.0 for r in snap.rx.values())
    assert all(r.rx_power_dbm == pytest.approx(line.drop_target_dbm) for r in snap.rx.values())
    lake = WorldSim(line, tier="full").run(0, 1800)
    assert not lake.query(source="alarm")


def test_mesh_lightpaths_all_have_margin():
    snap = evaluate(reference_mesh())
    assert min(r.margin_db for r in snap.rx.values()) > 1.0


def test_masquerade_span_loss_vs_amp_gain_drop(line):
    """Same receiver power and nearly the same OSNR; the amplifier input reading separates them."""
    span = evaluate(line, effects_at([Event("SPAN_LOSS_INCREASE", "oms.A-B.span2", {"added_loss_db": 4})], 1))
    gain = evaluate(line, effects_at([Event("AMP_GAIN_DROP", "oms.A-B.amp3", {"gain_drop_db": 4})], 1))
    r_s, r_g = span.rx[("och0", "fwd")], gain.rx[("och0", "fwd")]
    assert r_s.rx_power_dbm == pytest.approx(r_g.rx_power_dbm)
    assert abs(r_s.osnr_db - r_g.osnr_db) < 1.0
    assert span.amps["oms.A-B.amp4"].input_dbm_per_ch == pytest.approx(gain.amps["oms.A-B.amp4"].input_dbm_per_ch)
    # the discriminating observable: input power at the amplifier after the span
    assert span.amps["oms.A-B.amp3"].input_dbm_per_ch == pytest.approx(
        gain.amps["oms.A-B.amp3"].input_dbm_per_ch - 4.0)


def test_fibre_cut_alarm_pattern(line):
    w = WorldSim(line, tier="power")
    w.inject(Event("FIBRE_CUT", "oms.A-B.span2", {"position_km": 30}, onset=600))
    a = alarm_set(w.run(0, 1800))
    assert ("AMP_INPUT_LOS", "oms.A-B.amp3") in a          # first amplifier after the cut only
    assert ("AMP_INPUT_LOS", "oms.A-B.amp4") not in a       # suppressed downstream
    assert ("OSC_LOS", "oms.A-B.span2") in a
    assert ("OCH_LOS", "trx.och0.z") in a and ("OTU_BDI", "trx.och0.a") in a
    assert not any(el.startswith("oms.B-A") for _, el in a)  # the other fibre is untouched


def test_alarm_raise_respects_soak(line):
    w = WorldSim(line, tier="power")
    w.inject(Event("FIBRE_CUT", "oms.A-B.span2", {}, onset=600))
    lake = w.run(0, 1800)
    assert min(o.t_start for o in lake.query(source="alarm")) == pytest.approx(602.5)


def test_sub_second_sop_transient_raises_no_alarm_but_leaves_traces(line):
    w = WorldSim(line, tier="full")
    w.inject(Event("SOP_TRANSIENT", "oms.A-B.span1", {"peak_rate_rad_s": 2e6, "duration_s": 0.2},
                   onset=700.3, duration=0.2))
    lake = w.run(0, 1800)
    assert not lake.query(source="alarm")
    es = [o.value for o in lake.query(observable="errored_seconds_pm15") if o.value]
    assert es, "the burst must show in 15-minute errored seconds"
    caps = lake.query(observable="transient_capture")
    assert caps and caps[0].value["peak_sop_rate"] == pytest.approx(2e6)


def test_soft_fault_raises_signal_degrade_only(line):
    w = WorldSim(line, tier="power")
    w.inject(Event("RX_DEGRADATION", "trx.och0.z", {"penalty_db": 4.0}, onset=300))
    a = alarm_set(w.run(0, 1800))
    assert ("OTU_SD", "trx.och0.z") in a
    assert not any(t in ("OCH_LOS", "OTU_LOF") for t, _ in a)


def test_pump_current_drift_eventually_alarms(line):
    w = WorldSim(line, tier="baseline")
    w.inject(Event("PUMP_CURRENT_DRIFT", "oms.A-B.amp2", {"pct_per_day": 5.0}, onset=0, profile="ramp"))
    a = alarm_set(w.run(0, 5 * 86400))
    assert ("AMP_PUMP_EOL", "oms.A-B.amp2") in a


def test_otdr_locates_cut(line):
    w = WorldSim(line)
    w.inject(Event("FIBRE_CUT", "oms.A-B.span2", {"position_km": 31.4}, onset=600))
    trace = w.acquire_otdr("oms.A-B.span2", 900)
    assert trace["events"][0]["type"] == "end_of_fibre"
    assert trace["events"][0]["position_km"] == pytest.approx(31.4, abs=0.3)


def test_catalogue_covers_spec_version0_modes():
    spec_modes = {"FIBRE_CUT", "CONNECTOR_PULLED", "AMP_PUMP_FAIL", "TX_LASER_FAIL", "CARD_FAIL",
                  "WSS_PORT_FAIL", "SPAN_LOSS_INCREASE", "CONNECTOR_DIRTY", "AMP_NF_RISE",
                  "AMP_GAIN_TILT", "WSS_ATTEN_DRIFT", "WSS_FILTER_SHIFT", "RX_DEGRADATION", "DGD_RISE",
                  "SOP_TRANSIENT", "MECHANICAL_DISTURBANCE", "PROTECTION_SWITCH_HIT",
                  "CHANNEL_LOADING_TRANSIENT", "PUMP_CURRENT_DRIFT", "LASER_BIAS_DRIFT", "MARGIN_EROSION",
                  "MAINTENANCE_ACTIVITY", "PROVISIONING_CHANGE", "WEATHER_EXPOSURE",
                  "INSTRUMENT_MISREPORT", "TELEMETRY_STALE", "INVENTORY_DRIFT"}
    assert spec_modes <= set(CATALOGUE)

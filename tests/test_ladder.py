"""The capability ladder (world/ladder.py, tools/ladder.py; spec/PROTOCOL_CAMPAIGN.md, amendment "the capability
ladder"): each rung adds its observable to the world and nothing else, the agent reads it by its documented meaning
or through the twin, and with no rung switched on everything is as before."""
import math

import pytest

from adagent.world.faults import Event
from adagent.world.ladder import (HOLD_S, STREAM_LOST, excursions, logged_actions, segment_streams,
                                  signature_items, stopped_units, xcheck)
from adagent.world.network import reference_line
from adagent.world.checks import perform
from adagent.world.faults import effects_at
from adagent.world.simulator import WorldSim

T_END = 1200.0


@pytest.fixture(scope="module")
def line():
    return reference_line()


def world(net, events, ladder=(), seed=1):
    w = WorldSim(net, seed=seed, tier="full", ladder=frozenset(ladder))
    for e in events:
        w.inject(e)
    return w


# ---------------------------------------------------------------------- nothing changes without a rung

def test_no_rung_no_change(line):
    ev = [Event("TELEMETRY_STALE", "oms.A-B.amp1", {}, onset=600.0)]
    a = world(line, ev).run(0.0, T_END)
    b = WorldSim(line, seed=1, tier="full")
    for e in ev:
        b.inject(e)
    b = b.run(0.0, T_END)
    assert len(a.observations) == len(b.observations)
    assert all(o.seq is None for o in a.observations)
    assert not a.query(source="nms_alarm") and not a.query(source="log")
    assert [(o.observable, o.element, o.t_start, o.value) for o in a.observations[:500]] == \
        [(o.observable, o.element, o.t_start, o.value) for o in b.observations[:500]]
    assert signature_items(world(line, ev), [], T_END) == ()


def test_rungs_add_only_their_own_observations(line):
    ev = [Event("SPAN_LOSS_INCREASE", "oms.A-B.span1", {"added_loss_db": 3.0}, onset=600.0)]
    base = world(line, ev).run(0.0, T_END)
    full = world(line, ev, ladder=("L1", "L2", "L3", "L4", "L5", "L6")).run(0.0, T_END)
    key = lambda o: (o.source, o.observable, o.element, o.t_start, str(o.value))  # noqa: E731
    old = {key(o) for o in base.observations}
    new = [o for o in full.observations if key(o) not in old]
    assert old <= {key(o) for o in full.observations}
    assert {o.observable for o in new} <= {"span_dgd", "wss_port_power", "wss_filter_offset"}


# ---------------------------------------------------------------------- L1 stream liveness

def test_stale_stops_the_counter_and_raises_the_stream_alarm(line):
    el = "oms.A-B.amp1"
    lake = world(line, [Event("TELEMETRY_STALE", el, {}, onset=600.0)], ("L1",)).run(0.0, T_END)
    os_ = sorted(lake.query(observable="amp_input_power", element=el), key=lambda o: o.t_start)
    before = [o.seq for o in os_ if o.t_start < 600.0]
    after = [o.seq for o in os_ if o.t_start >= 600.0]
    assert before == list(range(1, len(before) + 1))
    assert set(after) == {before[-1]}
    other = sorted(lake.query(observable="amp_input_power", element="oms.A-B.amp2"), key=lambda o: o.t_start)
    assert [o.seq for o in other] == list(range(1, len(other) + 1))
    al = lake.query(source="nms_alarm")
    assert [(o.observable, o.element) for o in al] == [(STREAM_LOST, el)]
    assert al[0].t_start >= 600.0 + HOLD_S
    assert not [o for o in lake.query(source="alarm") if o.observable == STREAM_LOST]


def test_misreport_keeps_counting(line):
    el = "oms.A-B.amp1"
    lake = world(line, [Event("INSTRUMENT_MISREPORT", el, {"bias": 4.0}, onset=600.0)], ("L1",)).run(0.0, T_END)
    os_ = sorted(lake.query(observable="amp_input_power", element=el), key=lambda o: o.t_start)
    assert [o.seq for o in os_] == list(range(1, len(os_) + 1))
    assert not lake.query(source="nms_alarm")


def test_chunked_streams_carry_a_counter_per_sample(line):
    w = world(line, [Event("TELEMETRY_STALE", "trx.och0.z", {}, onset=600.0)], ("L1",))
    w.chunk_fast_streams = True
    lake = w.run(0.0, T_END)
    os_ = sorted(lake.query(observable="est_osnr", element="trx.och0.z"), key=lambda o: o.t_start)
    assert all(isinstance(o.seq, list) and len(o.seq) == len(o.value) for o in os_)
    live = [s for o in os_ if o.t_start < 600.0 for s in o.seq]
    assert live == list(range(1, len(live) + 1))
    assert {s for o in os_ if o.t_start >= 600.0 for s in o.seq} == {live[-1]}


def test_stopped_units_in_the_signature(line):
    w = world(line, [Event("TELEMETRY_STALE", "oms.A-B.amp1", {}, onset=600.0)], ("L1",))
    assert stopped_units(w, T_END) == {"oms.A-B.amp1"}
    assert stopped_units(w, 650.0) == frozenset()            # not yet seen to have stopped
    w2 = world(line, [Event("INSTRUMENT_MISREPORT", "oms.A-B.amp1", {"bias": 4.0}, onset=600.0)], ("L1",))
    assert signature_items(w2, w2.segments(0, T_END), T_END) == (("L1", frozenset()),)


# ---------------------------------------------------------------------- L2, L3 sampled streams

def test_span_dgd_rises_on_its_span_only(line):
    w = world(line, [Event("DGD_RISE", "oms.A-B.span1", {"dgd_increase_ps": 20.0}, onset=600.0)], ("L2",))
    segs = w.segments(0.0, T_END)
    s = segs[-1]
    v = segment_streams(line, s.snap, effects_at(w.events, s.t1 - 1), w.ladder)
    v0 = segment_streams(line, segs[0].snap, effects_at(w.events, 1.0), w.ladder)
    assert v[("span_dgd", "oms.A-B.span1")] == pytest.approx(math.hypot(v0[("span_dgd", "oms.A-B.span1")], 20.0))
    assert v[("span_dgd", "oms.A-B.span2")] == pytest.approx(v0[("span_dgd", "oms.A-B.span2")])


def test_wss_port_monitor_places_filter_shift_and_attenuation_drift(line):
    w = world(line, [Event("WSS_FILTER_SHIFT", "oms.A-B|1", {"offset_ghz": 8.0}, onset=600.0),
                     Event("WSS_ATTEN_DRIFT", "oms.A-B|0", {"attenuation_db": 3.0}, onset=600.0)], ("L3",))
    s = w.segments(0.0, T_END)[-1]
    v = segment_streams(line, s.snap, effects_at(w.events, s.t1 - 1), w.ladder)
    v0 = segment_streams(line, evaluate_nominal(line), effects_at([], 0.0), w.ladder)
    assert v[("wss_filter_offset", "oms.A-B|1")] == pytest.approx(8.0)
    assert v[("wss_filter_offset", "oms.A-B|0")] == pytest.approx(0.0)
    assert v[("wss_port_power", "oms.A-B|0")] == pytest.approx(v0[("wss_port_power", "oms.A-B|0")] - 3.0)
    assert v[("wss_port_power", "oms.A-B|1")] == pytest.approx(v0[("wss_port_power", "oms.A-B|1")])
    lake = w.run(0.0, T_END)
    assert lake.query(observable="wss_filter_offset", element="oms.A-B|1")


def evaluate_nominal(net):
    from adagent.world.physics import evaluate
    return evaluate(net)


# ---------------------------------------------------------------------- L4 located events, L5 logs

def test_brief_excursions_are_captured_on_their_span_and_lasting_ones_not(line):
    w = world(line, [Event("SOP_TRANSIENT", "oms.A-B.span2", {"peak_rate_rad_s": 2e5}, onset=600.0, duration=0.2),
                     Event("MECHANICAL_DISTURBANCE", "oms.A-B.span0", {"loss_excursion_db": 5.0}, onset=700.0,
                           duration=10.0),
                     Event("SPAN_LOSS_INCREASE", "oms.A-B.span3", {"added_loss_db": 3.0}, onset=800.0)], ("L4",))
    ex = excursions(w, w.segments(0.0, T_END), T_END)
    assert {(sp, k) for sp, k, *_ in ex} == {("oms.A-B.span2", "sop"), ("oms.A-B.span0", "loss")}
    lake = w.run(0.0, T_END)
    caps = lake.query(observable="span_transient_capture")
    assert {o.element for o in caps} <= {"oms.A-B.span2", "oms.A-B.span0"}


def test_logs_record_protection_switches_and_loading_changes(line):
    w = world(line, [Event("PROTECTION_SWITCH_HIT", "och0", {"hit_ms": 20}, onset=600.0, duration=0.02),
                     Event("CHANNEL_LOADING_TRANSIENT", "oms.A-B", {"excursion_db": 3.0}, onset=650.0,
                           duration=0.5)], ("L5",))
    acts = logged_actions(w, w.segments(0.0, T_END))
    assert [(a, b) for a, b, _ in acts] == [("LINE_PROTECTION_SWITCH", "och0"), ("CHANNEL_ADD_DROP", "oms.A-B")]
    lake = w.run(0.0, T_END)
    for o in lake.query(source="log"):
        assert o.value["t_arrived"] >= o.t_start
    hits = []
    for seed in range(40):                                  # about one entry in ten is lost
        lk = world(line, w.events, ("L5",), seed=seed).run(0.0, T_END)
        hits.append(len(lk.query(source="log")))
    assert 0.75 * 80 <= sum(hits) <= 80


# ---------------------------------------------------------------------- L6 cross-check

def test_cross_check_confirms_a_misreporting_instrument(line):
    el = "oms.A-B.amp1"
    w = world(line, [Event("INSTRUMENT_MISREPORT", el, {"bias": 4.0}, onset=600.0)], ("L6",))
    assert perform(w, {}, "xcheck_reading", {"unit": el}, T_END + 300, 1, noise=False, t_question=T_END).value == \
        {"unit": el, "agree": False}
    ok = world(line, [Event("SPAN_LOSS_INCREASE", "oms.A-B.span0", {"added_loss_db": 3.0}, onset=600.0)], ("L6",))
    assert perform(ok, {}, "xcheck_reading", {"unit": el}, T_END + 300, 1, noise=False,
                   t_question=T_END).value["agree"] is True
    no = world(line, [Event("INSTRUMENT_MISREPORT", el, {"bias": 4.0}, onset=600.0)])
    assert perform(no, {}, "xcheck_reading", {"unit": el}, T_END + 300, 1, noise=False,
                   t_question=T_END).status == "unavailable"
    assert xcheck(no, {"unit": el}, T_END, None, T_END) is None


def test_cross_check_reading_by_documented_meaning():
    from adagent.tools.interpret import DIRECT_NATS, answer_set, direct
    p = {"unit": "u1"}
    assert direct("xcheck_reading", p, {"agree": False}, "u1", "INSTRUMENT_MISREPORT") == 0.0
    assert direct("xcheck_reading", p, {"agree": True}, "u1", "INSTRUMENT_MISREPORT") == DIRECT_NATS
    assert direct("xcheck_reading", p, {"agree": False}, "span9", "SPAN_LOSS_INCREASE") == DIRECT_NATS
    assert direct("xcheck_reading", p, {"agree": True}, "span9", "SPAN_LOSS_INCREASE") == 0.0
    assert direct("xcheck_reading", p, {"agree": True}, "u1", "TELEMETRY_STALE") is None
    assert len(answer_set(None, "xcheck_reading", p)) == 2


# ---------------------------------------------------------------------- the arms

def test_ladder_arms_add_only_their_rung():
    from adagent.harness.exp2 import ARMS, LADDER_RUNGS
    base = ARMS["A-full-cd-fut"]
    for k in LADDER_RUNGS:
        a = dict(ARMS[f"A-{k}"])
        assert a.pop("ladder") == (k,) and a == base
    b = dict(ARMS["B-L1"])
    assert b.pop("ladder") == ("L1",) and b == ARMS["B-fut"]
    assert ARMS["A-Lall"]["ladder"] == LADDER_RUNGS == ARMS["B-Lall"]["ladder"]
    assert {k for k, v in ARMS.items() if "ladder" in v and set(v["ladder"]) & set(LADDER_RUNGS)} == \
        {f"A-{k}" for k in LADDER_RUNGS} | {"B-L1", "A-Lall", "B-Lall"}     # the spectral arms carry rungs S, R


# ---------------------------------------------------------------------- the agent on a case (slower)

@pytest.fixture(scope="module")
def stale_case():
    from adagent.harness.cases2 import make_case2
    return make_case2(2190.0, 2052, gap_scale=1.0, gap_calibrated=True, instrumentation="future", ladder=("L1",))


def test_liveness_reads_the_stopped_counter(stale_case):
    from adagent.tools.ladder import StreamLiveness, unit_instrument
    from adagent.tools.walks import single
    c = stale_case
    el = c.truth.faults[0].targets[0]
    assert c.truth.faults[0].mode == "TELEMETRY_STALE" and c.truth.labels["ladder"] == ["L1"]
    L = StreamLiveness(c.substrate)
    assert set(L.stopped) == {el}
    inst = unit_instrument(c.substrate.kg, el)
    p = L.payload()
    assert [x.hypothesis.key() for x in p.candidates] == [single("TELEMETRY_STALE", inst).key()]
    other = next(u for u, s in L.state.items() if s["live"] and unit_instrument(c.substrate.kg, u))
    hs = [single("TELEMETRY_STALE", inst), single("INSTRUMENT_MISREPORT", inst),
          single("TELEMETRY_STALE", unit_instrument(c.substrate.kg, other))]
    v = L.likelihood(hs)
    ll = [v.values[h.key()].log_likelihood for h in hs]
    assert ll[0] == 0.0 and ll[1] == -8.0 and ll[2] == -16.0 and v.background_log_likelihood == -8.0


def test_manual_troubleshooting_starts_from_the_stream_alarm(stale_case):
    from adagent.harness.cases import sources
    from adagent.tools.baseline import ManualTroubleshooting
    from adagent.tools.frame import DeterministicFramer
    from adagent.tools.ladder import LadderGrounding, ladder_library
    from adagent.tools.procedures import ProcedureLibrary
    c = stale_case
    frame = DeterministicFramer(c.substrate).build(c.question)
    plain = ManualTroubleshooting(c.substrate, ProcedureLibrary.from_sources(sources()))
    assert plain.select(frame) is None or "TELEMETRY" not in plain.select(frame)[2]
    lib = ladder_library(ProcedureLibrary.from_sources(sources()), ("L1",))
    b = ManualTroubleshooting(c.substrate, lib, grounding=LadderGrounding(c.substrate.kg))
    b.extra_alarm_sources = ("nms_alarm",)
    res = b.diagnose(frame)
    assert res["outcome"] == "commit" and res["hypothesis"].startswith("TELEMETRY_STALE@")
    assert c.truth.faults[0].targets[0] in res["hypothesis"]
    assert not res["checks"]


def test_port_monitor_places_filter_shift_and_port_loss(line):
    from types import SimpleNamespace
    from adagent.substrate.access import Substrate
    from adagent.tools.ladder import PortMonitor
    from adagent.tools.walks import single
    w = world(line, [Event("WSS_FILTER_SHIFT", "oms.A-B|1", {"offset_ghz": 8.0}, onset=600.0),
                     Event("WSS_ATTEN_DRIFT", "oms.A-B|0", {"attenuation_db": 3.0}, onset=600.0)], ("L3",))
    lake = w.run(0.0, T_END)
    kg = SimpleNamespace(has=lambda x: x in line.oms,
                         g=SimpleNamespace(nodes={o: {"dst": line.oms[o].dst} for o in line.oms}))
    pm = PortMonitor(Substrate(kg=kg, lake=lake, now=T_END), 600.0)
    assert pm.entries == [("WSS_ATTEN_DRIFT", "oms.A-B|0"), ("WSS_FILTER_SHIFT", "oms.A-B|1")]
    hs = [single("WSS_FILTER_SHIFT", "oms.A-B|1"), single("WSS_FILTER_SHIFT", "oms.A-B|0")]
    v = pm.likelihood(hs)
    assert v.values[hs[0].key()].log_likelihood == -6.0              # explains one entry, not the other
    assert v.values[hs[1].key()].log_likelihood == -3.0 - 12.0       # no shift there, and both entries unexplained
    assert v.background_log_likelihood == -12.0



def test_port_monitor_makes_no_entry_without_readings_after_the_onset(line):
    """Amendment 12: a drift's frame puts the onset at the question time, so no reading follows it and no port can
    show a change "since the onset"; before the fix the after window held one sample and noise across many ports
    could pass the threshold. A case with an onset is unchanged."""
    from types import SimpleNamespace
    from adagent.substrate.access import Substrate
    from adagent.tools.ladder import PortMonitor
    w = world(line, [Event("WSS_FILTER_SHIFT", "oms.A-B|1", {"offset_ghz": 8.0}, onset=1130.0)], ("L3",))
    lake = w.run(0.0, T_END)
    kg = SimpleNamespace(has=lambda x: x in line.oms,
                         g=SimpleNamespace(nodes={o: {"dst": line.oms[o].dst} for o in line.oms}))
    sub = Substrate(kg=kg, lake=lake, now=T_END)
    assert PortMonitor(sub, T_END).entries == []                     # onset at the question time: nothing after it
    assert PortMonitor(sub, 1100.0).entries == [("WSS_FILTER_SHIFT", "oms.A-B|1")]

def test_cross_check_is_beside_the_catalogue():
    from adagent.world.checks import CHECKS, check_spec
    assert "xcheck_reading" not in CHECKS
    assert check_spec("xcheck_reading")[:2] == ("remote", 5)
    assert check_spec("no_such_check") is None

"""WSS_PORT_FAIL at port grain: documented, grounded at "oms|port", propagated to every channel arriving on the
port, and simulated by the twin as the world simulates it (target: the OMS arriving on the failed port)."""
import pytest

from adagent.harness.cases import sources
from adagent.harness.cases2 import make_case2
from adagent.tools.frame import DeterministicFramer
from adagent.tools.grounding import GroundingTool, admits, is_port
from adagent.tools.propagation import Propagator
from adagent.tools.twin import GraphTwin
from adagent.tools.walks import AbductionWalk, single

SEED = 2152                 # day 2190, kind "hard": WSS_PORT_FAIL on the port oms.S05-S09 arrives on at S09


@pytest.fixture(scope="module")
def port_case():
    c = make_case2(2190.0, SEED, kind="hard")
    f = c.truth.faults[0]
    assert f.mode == "WSS_PORT_FAIL"
    frame = DeterministicFramer(c.substrate).build(c.question)
    return c, frame, f.targets[0]


def _slot_through(kg, oms):
    lp, _ = kg.lightpaths_through(oms)[0]
    return kg.g.nodes[lp]["slot"]


def test_grounding_admits_port_modes_at_ports_and_channel_modes_at_channels(port_case):
    c, _, oms = port_case
    kg = c.substrate.kg
    slot = _slot_through(kg, oms)
    assert admits(kg, f"{oms}|port", "WSS_PORT_FAIL")
    assert not admits(kg, f"{oms}|{slot}", "WSS_PORT_FAIL")
    assert not admits(kg, oms, "WSS_PORT_FAIL") and not admits(kg, f"roadm.{kg.g.nodes[oms]['dst']}", "WSS_PORT_FAIL")
    assert admits(kg, f"{oms}|{slot}", "WSS_ATTEN_DRIFT")
    assert not admits(kg, f"{oms}|port", "WSS_ATTEN_DRIFT")


def test_location_terms_name_the_port_upstream(port_case):
    c, _, oms = port_case
    kg = c.substrate.kg
    g = GroundingTool(kg)
    lp, d = kg.lightpaths_through(oms)[0]
    rx = kg.signal_path(lp, d)[-1]
    locs = g.resolve("upstream_on_path", rx)
    assert f"{oms}|port" in locs
    assert [x for x in locs if is_port(x)] and all(admits(kg, x, "WSS_PORT_FAIL") for x in locs if is_port(x))


def test_propagation_from_a_port_reaches_every_receiver_through_it(port_case):
    c, _, oms = port_case
    kg = c.substrate.kg
    roadm = f"roadm.{kg.g.nodes[oms]['dst']}"
    rxs = set()
    for lp, d in kg.lightpaths_through(oms):
        path = kg.signal_path(lp, d)
        if roadm in path:
            rxs.add(path[-1])
    assert len(rxs) > 1
    P = Propagator(kg).predict("WSS_PORT_FAIL", f"{oms}|port")
    for rx in rxs:
        assert P.get(("obs", "rx_power", rx)) == "required"
        assert ("alarm", "OCH_LOS", rx) in P
    # a channel fault at one of them reaches only its own receiver
    slot = _slot_through(kg, oms)
    P1 = Propagator(kg).predict("WSS_ATTEN_DRIFT", f"{oms}|{slot}")
    assert len({k[2] for k in P1 if k[:2] == ("obs", "rx_power")}) < len(rxs)


def test_twin_fits_the_true_port_better_than_a_wrong_one(port_case):
    c, frame, oms = port_case
    kg = c.substrate.kg
    o = kg.g.nodes[oms]
    wrong = f"oms.{o['dst']}-{o['src']}"                    # the port the reverse direction arrives on
    assert kg.has(wrong)
    tw = GraphTwin(c.substrate, sources(), frame.onset_estimate)
    good = tw.fit_cause("WSS_PORT_FAIL", f"{oms}|port")
    bad = tw.fit_cause("WSS_PORT_FAIL", f"{wrong}|port")
    assert "declined" not in good and "declined" not in bad
    assert good["scenario"] == "wss_port_blocked"
    assert good["ll"] > bad["ll"]
    # a per-channel scenario is declined at a port, not applied to it
    assert "declined" in tw.fit_cause("WSS_ATTEN_DRIFT", f"{oms}|port")


def test_gnn_declines_a_port_hypothesis_without_crashing(port_case):
    from adagent.tools.gnn import GNNRanker
    c, frame, oms = port_case
    try:
        gnn = GNNRanker(c.substrate, sources(), frame.onset_estimate, frame=frame)
    except FileNotFoundError:
        pytest.skip("no trained GNN model")
    port = single("WSS_PORT_FAIL", f"{oms}|port")
    assert gnn.score_cause("WSS_PORT_FAIL", f"{oms}|port") is None
    pl = gnn.likelihood([port])
    assert pl is None or pl.values[port.key()].declined


def test_abduction_proposes_the_true_port(port_case):
    c, frame, oms = port_case
    pl = AbductionWalk(c.substrate.kg).run(frame)
    keys = [x.hypothesis.key() for x in pl.candidates]
    assert single("WSS_PORT_FAIL", f"{oms}|port").key() in keys

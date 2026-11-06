"""Peeling (compound reading of checks) and the transient event recogniser's graph and features."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_compound_reading_picks_the_cause_the_check_concerns():
    from adagent.harness.cases2 import make_case2
    from adagent.schemas import Hypothesis, HypothesisKind
    from adagent.tools import interpret as I
    from adagent.tools.walks import single
    c = make_case2(2190.0, 2021, gap_scale=1.0, gap_calibrated=True)
    kg = c.substrate.kg
    lp = [l for l, _ in kg.inn("trx.och203.z", "endsAt")][0]
    a = single("CONNECTOR_PULLED", "oms.S07-S03").causes[0]
    rx = Hypothesis(kind=HypothesisKind.COMPOUND, causes=[a, single("RX_DEGRADATION", "trx.och203.z").causes[0]])
    ws = Hypothesis(kind=HypothesisKind.COMPOUND, causes=[a, single("WSS_FILTER_SHIFT", "oms.S05-S15|6").causes[0]])
    p = {"lightpath": lp, "end": "z"}
    assert I.compound_cause(kg, "retune_spare", p, rx).fault_mode == "RX_DEGRADATION"
    assert I.compound_cause(kg, "retune_spare", p, ws).fault_mode == "WSS_FILTER_SHIFT"
    clear = {"receiver": "trx.och203.z", "errors_on_spare": "clear"}
    assert I.direct("retune_spare", p, clear, "trx.och203.z", "RX_DEGRADATION", kg) == 0.0
    assert I.direct("retune_spare", p, clear, "oms.S05-S15|6", "WSS_FILTER_SHIFT", kg) > 0.0


def test_transient_features_see_a_polarisation_event_on_its_oms():
    import numpy as np
    import transient_gen as G
    from adagent.harness.cases2 import _events, _single
    from adagent.tools import transient as T
    from adagent.world.simulator import WorldSim
    net = G._net(*G._pool()[0])
    dep = {s: "full" for s in net.nodes}
    rng = random.Random(3)
    w = WorldSim(net, seed=3, deployment=dep)
    f = _single(rng, net, w, "SOP_TRANSIENT")
    for ev in _events(f):
        w.inject(ev)
    lake = w.run(0.0, 1200.0)
    g = T.build_graph(net, T.net_service_lightpaths(net))
    X = T.node_features(g, lambda ob: lake.query(observable=ob), 600.0)
    oms = f.targets[0].rsplit(".", 1)[0]
    sop_col = len(T.TYPES) + 9
    crossing = [i for t, i, j in g.edges if t == 5 and g.nodes[i] == oms]
    assert crossing and max(X[j, sop_col] for j in [j for t, i, j in g.edges if t == 5 and g.nodes[i] == oms]) > 0.5
    others = [i for i, t in enumerate(g.types) if t == "rx" and i not in
              {j for t, i2, j in g.edges if t == 5 and g.nodes[i2] == oms}]
    assert np.median(X[others, sop_col]) < 0.5


def test_peeling_fits_the_later_cause_onset():
    """s2013: a WSS attenuation drift starts 130 s after a fibre cut; fitted after the cut, it needs a later onset."""
    from adagent.harness.cases2 import make_case2
    from adagent.harness.exp2 import sources
    from adagent.schemas import Hypothesis, HypothesisKind
    from adagent.tools.frame import DeterministicFramer
    from adagent.tools.twin import GraphTwin
    from adagent.tools.walks import single
    c = make_case2(2190.0, 2013, gap_scale=1.0, gap_calibrated=True)
    fr = DeterministicFramer(c.substrate).build(c.question)
    tw = GraphTwin(c.substrate, sources(), fr.onset_estimate, seed=2013)
    tw.sequential = True
    h = Hypothesis(kind=HypothesisKind.COMPOUND, causes=[single("FIBRE_CUT", "oms.S02-S19.span3").causes[0],
                                                         single("WSS_ATTEN_DRIFT", "oms.S15-S04|24").causes[0]])
    tw.fit(h)
    later = tw.fits[("WSS_ATTEN_DRIFT", "oms.S15-S04|24", "given", (("FIBRE_CUT", "oms.S02-S19.span3"),))]
    assert later["onset_shift_s"] > 0
    assert tw.residual(h) == []

"""Multiple-fault attribution on the twin's residual (src/adagent/tools/multifault.py): features, abstention, the
payload's form, the training world's cases and the loop's flag (off by default). Offline: no trained model is
needed (fixture parameters stand in for one)."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _twin_and_leader(seed, **kw):
    from adagent.harness.cases2 import make_case2
    from adagent.harness.exp2 import sources
    from adagent.tools.frame import DeterministicFramer
    from adagent.tools.twin import GraphTwin
    from adagent.tools.walks import single
    c = make_case2(2190.0, seed, **kw)
    frame = DeterministicFramer(c.substrate).build(c.question)
    twin = GraphTwin(c.substrate, sources(), frame.onset_estimate, calc_err_db=0.18, seed=seed)
    f0 = c.truth.faults[0]
    loc = f"{f0.targets[0]}|port" if f0.mode == "WSS_PORT_FAIL" else f0.targets[0]
    return c, twin, single(f0.mode, loc)


@pytest.fixture(scope="module")
def coincident():
    """s2013: a fibre cut, then a WSS attenuation drift 130 s later; the leader is the cut."""
    return _twin_and_leader(2013, gap_scale=1.0, gap_calibrated=True)


def _fixture_model(noise_bias: float):
    from adagent.tools import multifault as MF
    p = MF.init_params(ranker=None, seed=1)
    p["bn"] = np.array([noise_bias])
    return p, {"temperature": 1.0, "abstain_threshold": 0.5}


def test_features_have_the_ranker_columns_residual_channels_and_leader_flag(coincident):
    from adagent.tools import gnn as G
    from adagent.tools import multifault as MF
    c, twin, leader = coincident
    f = MF.case_features(twin, leader)
    g, X = f["graph"], f["X"]
    assert X.shape == (len(g.nodes), MF.N_FEAT) == (len(g.nodes), G.N_FEAT + 17)
    assert any(nid.startswith("wss:") for nid in g.nodes) and len(g.nodes) > 100
    lead = MF.leader_nodes(g, twin.fit(leader)["events"])
    assert X[:, MF.LEADER_COL].sum() == len(lead) and all(X[i, MF.LEADER_COL] == 1.0 for i in lead)
    # the residual block: the WSS drift the cut leaves unexplained is in the graph, and its channel's receiver shows
    # it beyond the combined accuracy
    assert f["unexplained"] > 0
    oms, slot = c.truth.faults[1].targets[0].split("|")
    rx = [nid for nid in g.nodes if nid.startswith("wss:") and nid.endswith(f"|{slot}")]
    assert rx and np.abs(X[:, MF.F_RANK:MF.LEADER_COL]).max() > 1.0
    # the ranker's own columns are its features unchanged
    assert np.allclose(X[:, :MF.F_RANK], G.features(g, MF.readings_of(twin.obs.cont)))


def test_line_abstains_on_a_single_fault_case_with_a_model_that_calls_it_noise():
    from adagent.tools.multifault import MultiFaultAttribution
    c, twin, leader = _twin_and_leader(9104)            # a pulled connector at an OMS input, alone
    line = MultiFaultAttribution(c.substrate, twin, params=_fixture_model(50.0))
    p = line.run(leader)
    assert p.candidates == [] and p.residual_mass == 1.0
    assert line.last.get("abstained")


def test_payload_pairs_the_leader_with_each_proposal(coincident):
    from adagent.schemas import GeneratorPayload, HypothesisKind
    from adagent.tools.loop import canonical
    from adagent.tools.multifault import TOP_K, MultiFaultAttribution
    c, twin, leader = coincident
    line = MultiFaultAttribution(c.substrate, twin, params=_fixture_model(-50.0))
    p = line.run(leader)
    assert isinstance(p, GeneratorPayload)
    assert 1 <= len(p.candidates) <= TOP_K
    for cand in p.candidates:
        h = cand.hypothesis
        assert h.kind == HypothesisKind.COMPOUND and len(h.causes) == len(leader.causes) + 1
        assert [x.key() for x in h.causes[:len(leader.causes)]] == [x.key() for x in leader.causes]
        assert 0.0 < cand.prior_mass <= 1.0
    assert abs(sum(x.prior_mass for x in p.candidates) + p.residual_mass - 1.0) < 1e-6
    canonical(p, c.substrate.kg)                         # the loop's normal form accepts it
    assert len(line.last["top"]) == TOP_K


def test_training_world_case_and_one_training_step():
    import multifault_gen as MG
    from adagent.tools import multifault as MF
    c = next(x for x in (MG.one(s) for s in range(5_000_000, 5_000_010)) if x is not None and x["y_mode"])
    assert c["net"][0] != 24 and c["X"].shape[1] == MF.N_FEAT
    g = MG._graph(c["net"])
    pc = MF.prepare(c, g)
    assert pc["y"] and all(0 <= t < len(g.nodes) * len(MF.CLASSES) for t in pc["y"])
    p = MF.train([pc], epochs=1, batch=1, log=lambda s: None)
    r = MF.predict(p, pc)
    assert 0.0 <= r["p_noise"] <= 1.0 and len(r["top"]) == MF.TOP_K
    assert np.isfinite(float(MF._loss(p, [pc])))


def test_loop_flag_is_off_by_default_and_leaves_results_unchanged(monkeypatch):
    import inspect

    from adagent.harness.cases2 import make_case2
    from adagent.harness.exp2 import ARMS, sources
    from adagent.tools import multifault as MF
    from adagent.tools.frame import DeterministicFramer
    from adagent.tools.loop import WinnowingLoop
    from adagent.tools.walks import AbductionWalk, MinimumExplanation
    assert inspect.signature(WinnowingLoop).parameters["multifault"].default is False
    assert not any(cfg.get("multifault") for a, cfg in ARMS.items() if not a.startswith("A-full-mf"))
    assert ARMS["A-full-mf"] == {**ARMS["A-full"], "multifault": True}
    assert ARMS["A-full-mf-fut"] == {**ARMS["A-full-fut"], "multifault": True}

    def run(**kw):
        c = make_case2(2190.0, 9113)
        frame = DeterministicFramer(c.substrate).build(c.question)
        kg = c.substrate.kg
        abd = AbductionWalk(kg)
        loop = WinnowingLoop(c.substrate, sources(), frame, {"abduction": abd, "setcover": MinimumExplanation(kg, abd)},
                             case_seed=9113, **kw)
        return loop.run()

    def boom(*a, **k):
        raise AssertionError("the multiple-fault line ran with the flag off")
    monkeypatch.setattr(MF, "MultiFaultAttribution", boom)
    monkeypatch.setattr(MF, "load", boom)
    a = run()
    b = run(multifault=False)
    assert "multifault" not in a and "multifault" not in b
    assert a["decision"] == b["decision"] and a["outcome"] == b["outcome"]
    assert a["trajectory"] == b["trajectory"] and len(a["checks"]) == len(b["checks"])

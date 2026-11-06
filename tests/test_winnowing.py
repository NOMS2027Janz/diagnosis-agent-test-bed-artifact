"""Winnowing tools: the twin rebuilt from the graph, graph consistency, instrument interpreters and the advisor."""
import math

import pytest

from adagent.harness.cases import context, sources
from adagent.harness.cases2 import make_case2
from adagent.substrate.lifecycle import build_graph
from adagent.tools.advisor import checks_for
from adagent.tools.consistency import GraphConsistency
from adagent.tools.frame import DeterministicFramer
from adagent.tools.interpret import compare, direct
from adagent.tools.twin import GraphTwin, twin_deployment, twin_network
from adagent.tools.walks import single
from adagent.world.physics import evaluate


def test_twin_equals_world_when_records_are_complete_and_there_is_no_gap():
    src = sources()
    h = context(gap_scale=0.0, records_complete=True)
    kg = build_graph(h.events, 2190.0, src).kg
    tw = twin_network(kg, src.practice["line_design"]["design"])
    world = h.network_at(2190.0)
    assert sorted(tw.lightpaths) == sorted(world.lightpaths) and sorted(tw.services) == sorted(world.services)
    s1, s2 = evaluate(world), evaluate(tw)
    for k, r in s1.rx.items():
        assert math.isclose(r.osnr_db, s2.rx[k].osnr_db, abs_tol=1e-9) or not math.isfinite(r.osnr_db)
    assert twin_deployment(kg) == h.deployment_at(2190.0)


def test_twin_knows_only_the_records():
    """With the hidden gap the twin's OSNR differs from the world's; it never sees calibration offsets."""
    src = sources()
    h = context(gap_scale=1.0)
    kg = build_graph(h.events, 2190.0, src).kg
    tw = twin_network(kg, src.practice["line_design"]["design"])
    assert not tw.calibration_db and tw.gap_scale == 0.0
    assert all(a.true_nf_db is None for a in tw.amps.values())
    s1, s2 = evaluate(h.network_at(2190.0)), evaluate(tw)
    diffs = [abs(s2.rx[k].osnr_db - r.osnr_db) for k, r in s1.rx.items() if math.isfinite(r.osnr_db)]
    assert max(diffs) > 0.3


@pytest.fixture(scope="module")
def hard_case():
    c = make_case2(2190.0, 9104)            # a pulled connector at an OMS input
    frame = DeterministicFramer(c.substrate).build(c.question)
    return c, frame


def test_twin_ranks_the_truth_and_charges_what_it_cannot_simulate(hard_case):
    c, frame = hard_case
    tw = GraphTwin(c.substrate, sources(), frame.onset_estimate)
    oms = c.truth.faults[0].targets[0]
    hyps = [single("CONNECTOR_PULLED", oms), single("AMP_PUMP_FAIL", f"{oms}.amp1"),
            single("SPAN_LOSS_INCREASE", f"{oms}.span0"), single("AMP_GAIN_TILT", f"{oms}.amp0"),
            single("PUMP_CURRENT_DRIFT", f"{oms}.amp0")]
    pl = tw.likelihood(hyps)
    v = {k.split("@")[0]: x for k, x in pl.values.items()}
    lls = {m: x.log_likelihood for m, x in v.items() if not x.decline_reason}
    assert max(lls, key=lls.get) == "CONNECTOR_PULLED"
    assert pl.background_log_likelihood < lls["CONNECTOR_PULLED"]
    # C4 (amendment 8): a cause with no twin scenario (pump current drift) predicts no change in transmission; with
    # readings changed it scores below the background, not at it (it is no longer credited with every change)
    assert v["PUMP_CURRENT_DRIFT"].log_likelihood < pl.background_log_likelihood
    # gain tilt now has a scenario in the operator's twin practice, and is fitted (it explains a pulled connector badly)
    assert v["AMP_GAIN_TILT"].log_likelihood < v["CONNECTOR_PULLED"].log_likelihood


def test_graph_consistency_is_three_valued(hard_case):
    c, frame = hard_case
    gc = GraphConsistency(c.substrate.kg, frame)
    oms = c.truth.faults[0].targets[0]
    good = gc.evaluate(single("CONNECTOR_PULLED", oms))
    assert good.verdict == "supported"
    far = sorted(o for o in c.world.net.oms if o != oms and not o.endswith(oms.split(".")[1]))[0]
    bad = gc.evaluate(single("CONNECTOR_PULLED", far))
    assert bad.verdict == "contradicted"
    # shared risk: absence of a common duct is unresolved, with a request, never a contradiction
    spans = sorted(c.world.net.spans)
    from adagent.schemas import Cause, ElementLocation, Hypothesis, HypothesisKind
    comp = Hypothesis(kind=HypothesisKind.COMPOUND, causes=[
        Cause(fault_mode="FIBRE_CUT", location=ElementLocation(element=spans[0])),
        Cause(fault_mode="FIBRE_CUT", location=ElementLocation(element=spans[-1]))])
    r5 = next(r for r in gc.evaluate(comp).rules if r.rule == "R5")
    assert r5.verdict in ("unresolved", "supported") and (r5.verdict == "supported" or r5.requests)


def test_interpreter_compares_answers():
    # accuracy.py: OSNR sigma 0.69 dB (instrument and twin); an answer costs what its worst field costs
    assert compare({"est_osnr_db": 18.0, "signal": "x"}, {"est_osnr_db": 18.5, "signal": "x"}) < 0.5
    assert compare({"est_osnr_db": 18.0, "margin_db": 3.0}, {"est_osnr_db": 20.0, "margin_db": 3.1}) > 2.0
    assert compare({"tx_power_dbm": None}, {"tx_power_dbm": 0.1}) >= 5.0
    assert compare({"events": [{"type": "end_of_fibre", "position_km": 3.0}]},
                   {"events": [{"type": "end_of_fibre", "position_km": 40.0}]}) == 0.0    # position not fitted
    assert direct("swap_unit", {"unit": "a1"}, {"symptoms_after_swap": "cleared"}, "a1", "AMP_GAIN_TILT") == 0.0
    assert direct("swap_unit", {"unit": "a1"}, {"symptoms_after_swap": "unchanged"}, "a1", "AMP_GAIN_TILT") > 3


def test_checks_for_reads_availability_from_the_graph(hard_case):
    c, _ = hard_case
    kg = c.substrate.kg
    span = sorted(c.world.net.spans)[0]
    names = [n for n, _ in checks_for(kg, span)]
    assert "otdr_portable" in names and "span_loss" in names
    src_site = c.world.net.oms[c.world.net.spans[span].oms].src
    assert ("otdr_embedded" in names) == (kg.g.nodes[f"site.{src_site}"]["tier"] == "full")


def test_manual_troubleshooting_baseline_runs_the_procedure_tests():
    """Baseline B follows the procedure, has its tests done and counts their effort."""
    from adagent.tools.baseline import ManualTroubleshooting
    from adagent.tools.procedures import ProcedureLibrary
    c = make_case2(2190.0, 9113)                # a receiver degradation: the procedure asks to retune to a spare
    frame = DeterministicFramer(c.substrate).build(c.question)
    res = ManualTroubleshooting(c.substrate, ProcedureLibrary.from_sources(sources())).diagnose(frame)
    assert [x["check"] for x in res["checks"]] == ["retune_spare"]
    assert res["outcome"] == "commit" and res["hypothesis"].startswith("RX_DEGRADATION@")


def test_loop_with_a_stub_orchestrator_stops_when_told():
    """The LLM planner arm, with a stand-in for the model: the loop shows it the state and the checks available,
    obeys its choice, and the ledger alone decides commitment."""
    from adagent.tools.loop import WinnowingLoop
    from adagent.tools.orchestrator_llm import Decision, LLMOrchestrator
    from adagent.tools.walks import AbductionWalk, MinimumExplanation

    class Stub:
        def __init__(self):
            self.seen = []

        def parse(self, role, pv, system, user, schema):
            self.seen.append(user)
            if len(self.seen) == 1 and "[0]" in user:
                return Decision(action="check", check_index=0, reason="first listed")
            return Decision(action="stop", check_index=None, reason="enough")

    c = make_case2(2190.0, 9113)
    frame = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    abd = AbductionWalk(kg)
    stub = Stub()
    loop = WinnowingLoop(c.substrate, sources(), frame, {"abduction": abd, "setcover": MinimumExplanation(kg, abd)},
                         case_seed=9113, planner=LLMOrchestrator(stub))
    rec = loop.run()
    if rec["outcome"] != "commit":
        assert stub.seen and "Leading candidates" in stub.seen[0] and "Checks available" in stub.seen[0]
        assert len(rec["checks"]) <= 1
        assert rec["outcome"] == "no_informative_check" and rec["stop_reason"]


def test_rules_in_the_graph_agree_with_rules_in_code_and_respect_completeness(hard_case):
    """The consistency rules held as graph content and evaluated by a general engine give the same verdicts as the
    coded rules; a negation over a relation the records do not declare complete is never concluded."""
    from adagent.tools.rules_graph import Engine, GraphRuleConsistency
    c, frame = hard_case
    kg = c.substrate.kg
    oms = c.truth.faults[0].targets[0]
    code, graph = GraphConsistency(kg, frame), GraphRuleConsistency(kg, frame)
    assert any(d.get("cls") == "Rule" for _, d in kg.g.nodes(data=True))
    for h in [single("CONNECTOR_PULLED", oms), single("AMP_PUMP_FAIL", f"{oms}.amp1"),
              single("SPAN_LOSS_INCREASE", f"{oms}.span0"), single("CONNECTOR_PULLED", sorted(c.world.net.oms)[0])]:
        a = {r.rule: r.verdict for r in code.evaluate(h).rules}
        b = {r.rule: r.verdict for r in graph.evaluate(h).rules}
        assert a == b
    e = Engine({"x": "incomplete", "y": "complete"})
    assert e.holds([["s", ["A"]], ["x", ["A"], "not"]], {"s": {("a",)}}) is not True
    assert e.holds([["s", ["A"]], ["y", ["A"], "not"]], {"s": {("a",)}}) is True


def test_a_return_asked_for_when_none_is_left_is_refused_and_the_planner_asked_again():
    """Orchestrator v3: a return requested after all returns are used is not a stop; the planner is told and asked
    again, and the state shows the passes with their yield and the returns left."""
    from adagent.tools.loop import MAX_RETURNS, WinnowingLoop
    from adagent.tools.orchestrator_llm import Decision, LLMOrchestrator
    from adagent.tools.walks import AbductionWalk, MinimumExplanation

    class Stub:
        def __init__(self):
            self.seen = []

        def parse(self, role, pv, system, user, schema):
            self.seen.append(user)
            if "refused" in user:
                return Decision(action="stop", check_index=None, reason="told no returns are left")
            return Decision(action="return_to_generation", check_index=None, reason="always return")

    c = make_case2(2190.0, 9113)
    frame = DeterministicFramer(c.substrate).build(c.question)
    kg = c.substrate.kg
    abd = AbductionWalk(kg)
    stub = Stub()
    loop = WinnowingLoop(c.substrate, sources(), frame, {"abduction": abd, "setcover": MinimumExplanation(kg, abd)},
                         case_seed=9113, planner=LLMOrchestrator(stub))
    rec = loop.run()
    if rec["outcome"] != "commit":
        assert rec["returns"] == MAX_RETURNS
        assert any(p.get("refused") for p in rec["plans"])
        assert "Returns to generation left: 0" in stub.seen[-1] and "refused" in stub.seen[-1]
        assert "Generation passes: pass 1 (start)" in stub.seen[0]
        assert rec["stop_reason"] == "told no returns are left"


def test_gnn_graph_features_and_training_case():
    """The GNN's local graph covers every element on each lightpath's path in both directions, with observed-by
    edges to the receiver; generated training cases label faulted nodes with valid classes."""
    from adagent.tools.gnn import CLASSES, N_FEAT, VALID, build_graph, generate_case
    c = generate_case(123)
    g, X, y = c["graph"], c["X"], c["y"]
    assert X.shape == (len(g.nodes), N_FEAT) and len(y) == len(g.nodes)
    for i, t in enumerate(g.types):
        if y[i] > 0:
            assert CLASSES[y[i]] in VALID[t]
    assert {t for t, _, _ in g.edges} == {0, 1, 2, 3}


def test_direct_reading_values_contenders_elsewhere():
    """A negative finding at one place is what a fault elsewhere expects; a check must value every contender it
    speaks of, or the ledger's mean for declined contenders cancels a lone contradiction."""
    assert direct("swap_unit", {"unit": "a1"}, {"symptoms_after_swap": "unchanged"}, "a2", "AMP_GAIN_TILT") == 0.0
    assert direct("swap_unit", {"unit": "a1"}, {"symptoms_after_swap": "cleared"}, "a2", "AMP_GAIN_TILT") > 3
    assert direct("loopback", {"port": "p1"}, {"result": "pass"}, "p2", "CLIENT_PORT_FAIL") == 0.0
    assert direct("inspect_clean", {"oms": "o1"}, {"found": "connectors clean"}, "o2", "CONNECTOR_DIRTY") == 0.0

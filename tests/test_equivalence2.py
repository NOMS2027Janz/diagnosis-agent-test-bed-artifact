"""Equivalence for Experiment 2 cases and separability by checks (harness/equivalence.py, harness/separability.py)."""
from adagent.harness.cases2 import make_case2
from adagent.harness.equivalence import equivalence_classes_v2
from adagent.harness.separability import separating_checks


def test_drift_is_located_by_health_telemetry():
    c = make_case2(2190.0, 9103, kind="drift")
    t = c.truth.faults[0].targets[0]
    other = t[:-1] + ("a" if t.endswith("z") else "z")
    r = equivalence_classes_v2(c, [t, other])
    assert r["whole"] == [c.truth.faults[0].key()]


def test_coincident_faults_have_a_class_per_cause():
    c = make_case2(2190.0, 9105, kind="coincident")
    r = equivalence_classes_v2(c, [t for f in c.truth.faults for t in f.targets])
    assert len(r["components"]) == 2 and all(r["components"])


def test_contenders_the_data_already_separate():
    c = make_case2(2190.0, 9101, kind="soft")            # a DGD rise on a span
    el = c.truth.faults[0].targets[0]
    r = separating_checks(c, [f"SOP_TRANSIENT@{el}", f"SPAN_LOSS_INCREASE@{el}"], budget_hours=6.0, case_seed=9101)
    assert all(v == [("data_held", {}, 0.0)] for v in r.values())


def test_filter_shift_and_receiver_fault_are_separated_by_a_retune():
    c = make_case2(2190.0, 9217, kind="soft")            # a WSS filter shift: the classic look-alike of a receiver fault
    net = c.world.net
    oms, slot = c.truth.faults[0].targets[0].split("|")
    lp = next(l for l in net.lightpaths.values() if oms in l.oms_path and str(l.slot) == slot)
    r = separating_checks(c, [f"RX_DEGRADATION@{lp.dst_trx}"], budget_hours=6.0, case_seed=9217)
    found = {ch: h for ch, _, h in r[f"RX_DEGRADATION@{lp.dst_trx}"]}
    assert "retune_spare" in found and found["retune_spare"] < 1.0
    tight = separating_checks(c, [f"RX_DEGRADATION@{lp.dst_trx}"], budget_hours=0.1, case_seed=9217)
    assert tight[f"RX_DEGRADATION@{lp.dst_trx}"] == []


def test_record_completeness_is_declared():
    from adagent.harness.cases import context, sources
    from adagent.substrate.lifecycle import build_graph
    kg = build_graph(context().events, 2190.0, sources()).kg
    assert kg.completeness("traverses") == "complete_once_recorded"
    assert kg.completeness("inDuct") == "incomplete" and kg.completeness("monitoredBy") == "incomplete"
    assert kg.completeness("hasFaultMode") == "as_documented"
    assert all(kg.completeness(r) != "incomplete" or r in ("inDuct", "inCable", "monitoredBy", "observes")
               for r in kg.stats()["relations"])


def test_every_service_is_on_its_working_path_at_onset():
    """Walks follow the working path; in the world as simulated, every service is on it until the fault."""
    c = make_case2(2190.0, 9100, kind="hard")
    segs = c.world.segments(0.0, 1200.0)
    before = next(s for s in segs if s.t0 <= 599.0 < s.t1)
    assert all(st.active == "w" for st in before.svc.values() if st.up)

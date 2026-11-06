"""Experiment 2 cases (harness/cases2.py): every kind can be drawn, labelled and posed."""
import pytest

from adagent.harness.cases2 import KINDS, NotObservable, make_case2


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_at_maturity(kind):
    c = make_case2(2190.0, 9100 + KINDS.index(kind), kind=kind)
    L = c.truth.labels
    assert L["kind"] == kind and c.question.text and c.truth.faults
    assert set(L) >= {"how", "nature", "where", "multiplicity", "documentation", "tier", "alarms"}
    if kind == "drift":
        assert L["how"] == "trend" and not L["alarms"]
    if kind == "monitoring":
        assert L["documentation"] == ["monitoring"]
    if kind == "coincident":
        assert len(c.truth.faults) == 2
    if kind == "common_cause":
        assert len(c.truth.faults[0].targets) >= 4


def test_drift_needs_health_telemetry():
    with pytest.raises(NotObservable):
        make_case2(730.0, 9103, kind="drift")


def test_hidden_truth_stays_hidden():
    c = make_case2(2190.0, 9100, kind="hard")
    kg = c.substrate.kg
    amps = [n for n in kg.nodes_of("Amplifier")][:20]
    assert all("true_nf_db" not in kg.attrs(a) for a in amps)

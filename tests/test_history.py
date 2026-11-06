"""Simulated operating history (design note rev 3, §4.2)."""
import pytest

from adagent.world.history import (TIER_ORDER, generate_history, observed_summary, summary)
from adagent.world.physics import evaluate
from adagent.world.reference import build_reference_national


@pytest.fixture(scope="module")
def hist():
    return generate_history(build_reference_national(24), seed=1)


def test_deterministic(hist):
    h2 = generate_history(build_reference_national(24), seed=1)
    assert summary(h2) == summary(hist)


def test_events_ordered_and_tickets_carry_no_truth(hist):
    ts = [e.t for e in hist.events]
    assert ts == sorted(ts)
    for e in hist.events:
        if e.kind == "incident_closed":
            assert "truth" not in e.payload and "fault_mode" not in e.payload["ticket"]
            assert "explanation_quality" not in e.payload["ticket"]          # known only to the harness
    s = summary(hist)
    assert s["ticket_quality"].get("wrong", 0) > 0 and s["ticket_quality"].get("no_fault_found", 0) > 0
    assert s["lagged_records"] > 0 and s["unregistered_deployments"] > 0


@pytest.mark.parametrize("day", [200, 730, 1460, 2190])
def test_network_at_is_consistent(hist, day):
    net = hist.network_at(day)
    for lp in net.lightpaths.values():
        assert all(o in net.oms for o in lp.oms_path)
    for s in net.services.values():
        for p in filter(None, (s.working, s.protect)):
            assert all(h.lightpath in net.lightpaths for h in net.odu_paths[p].hops)
    if net.lightpaths:
        assert min(r.margin_db for r in evaluate(net).rx.values()) > 1.0


def test_instrumentation_only_improves(hist):
    prev = {}
    for day in range(0, 2190, 90):
        dep = hist.deployment_at(day)
        for site, tier in dep.items():
            assert TIER_ORDER.index(tier) >= TIER_ORDER.index(prev.get(site, "baseline"))
        prev = dep
    assert set(hist.deployment_at(2190).values()) & {"coherent", "full"}


def test_observed_summary_of_a_cut(hist):
    inc = next(i for i in hist.incidents if i.truth["fault_mode"] == "FIBRE_CUT"
               and i.truth["onset"] > 1000 and i.ticket["ticketed"])
    obs = observed_summary(hist, inc)
    assert obs["alarms"], inc.truth


def test_margin_erosion_has_physical_effect(hist):
    from adagent.world.faults import Event, effects_at
    inc = next(i for i in hist.incidents if i.truth["fault_mode"] == "MARGIN_EROSION")
    trx = inc.truth["targets"][0]
    assert trx in hist.net.transponders
    ev = Event("MARGIN_EROSION", trx, inc.truth["params"], onset=0.0, profile="ramp")
    nom = evaluate(hist.net)
    later = evaluate(hist.net, effects_at([ev], 30 * 86400.0))
    key = next(k for k, r in nom.rx.items() if r.trx == trx)
    assert later.rx[key].margin_db < nom.rx[key].margin_db - 0.2

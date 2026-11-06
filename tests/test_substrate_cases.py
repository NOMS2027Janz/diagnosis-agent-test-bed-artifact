"""Substrate interface and diagnosis cases on the reference network (build step 3)."""
import pytest

from adagent.harness.cases import context, make_case, sources
from adagent.substrate.access import Substrate
from adagent.substrate.lifecycle import build_graph
from adagent.world.simulator import WorldSim


@pytest.fixture(scope="module")
def mature_kg():
    return build_graph(context().events, 2190.0, sources()).kg


@pytest.fixture(scope="module")
def cases():
    return [make_case(2190.0, s) for s in range(4)]


def test_signal_path_runs_transmitter_to_receiver(mature_kg):
    kg, net = mature_kg, context().network_at(2190.0)
    for lp in list(net.lightpaths)[:20]:
        p = kg.signal_path(lp, "fwd")
        assert p[0] == net.lightpaths[lp].src_trx and p[-1] == net.lightpaths[lp].dst_trx
        oms_in_path = [x for x in p if kg.cls(x) == "OMS"]
        assert oms_in_path == net.lightpaths[lp].oms_path


def test_lightpaths_through_a_span_match_the_network(mature_kg):
    kg, net = mature_kg, context().network_at(2190.0)
    sp = sorted(net.spans)[17]
    oms = net.spans[sp].oms
    truth = set()
    for lp in net.lightpaths.values():
        if oms in lp.oms_path:
            truth.add((lp.id, "fwd"))
        n = lp.nodes
        if oms in [f"oms.{n[j + 1]}-{n[j]}" for j in range(len(n) - 1)]:
            truth.add((lp.id, "rev"))
    assert set(kg.lightpaths_through(sp)) == truth


def test_cases_are_noticeable_and_anchored(cases):
    for c in cases:
        assert c.question.text.endswith("What is the cause?")
        assert c.truth.services_down or c.truth.services_hit or c.truth.services_degraded or c.truth.alarms
        kg = c.substrate.kg
        if c.question.kind == "customer":
            assert kg.has(c.question.anchor)


def test_customer_anchor_depends_on_the_fault_location(cases):
    """The fault lies in the anchor service's closure (working or protection) for path faults."""
    for c in cases:
        if c.question.kind != "customer":
            continue
        kg, f = c.substrate.kg, c.truth.fault
        loc = f.targets[0].split("|")[0]
        if loc.startswith("otn.") or loc.startswith("cp."):
            assert c.question.anchor in kg.services_through(loc)
        else:
            assert loc in kg.service_closure(c.question.anchor) or \
                   any(loc in kg.signal_path(lp, d) for lp, d in kg.lightpaths_through(loc))


def test_substrate_never_returns_the_future(cases):
    sub = cases[0].substrate
    assert all(o.t_start <= sub.now for o in sub.alarms(include_unalarmed=True))
    el = next(iter(sub.elements_reporting("est_osnr")), None)
    if el:
        s = sub.samples("est_osnr", el)
        assert s and s[-1].t <= sub.now and s[0].evidence_id.startswith("ob")


def test_evidence_ids_are_stable_across_readers(cases):
    sub = cases[0].substrate
    sub.reader = "tool_a"
    a = [o.id for o in sub.alarms()]
    sub.reader = "tool_b"
    b = [o.id for o in sub.alarms()]
    assert a == b and {r for r, _ in sub.read_log} >= {"tool_a", "tool_b"}


def test_unregistered_monitoring_shows_as_unknown_sources():
    h = context()
    day = 730.0
    kg = build_graph(h.events, day, sources()).kg
    w = WorldSim(h.network_at(day), seed=2, deployment=h.deployment_at(day))
    sub = Substrate(kg, w.run(0.0, 120.0), now=120.0, day=day)
    unknown = sub.unknown_sources(["amp_input_power", "rx_power"])
    assert unknown


def test_failed_telemetry_shows_as_silent_streams(mature_kg):
    h = context()
    day = 2190.0
    dep = dict(h.deployment_at(day))
    site = sorted(dep)[0]
    dep[site] = "baseline"                      # this site's telemetry has stopped reporting
    w = WorldSim(h.network_at(day), seed=2, deployment=dep)
    sub = Substrate(mature_kg, w.run(0.0, 120.0), now=120.0, day=day)
    amp = next(a for a, _ in mature_kg.inn(f"site.{site}", "locatedAt") if mature_kg.cls(a) == "Amplifier")
    silent = sub.silent_streams(amp, 0.0, 120.0)
    assert ("amp_input_power" in {o for _, o in silent})

"""Checks the world answers (world/checks.py): each shows what it should, costs what it should, and repairs
only what it should."""
import pytest

from adagent.harness.cases import Fault, context, make_case
from adagent.world.checks import CHECKS, effort, perform, travel_hours

DAY = 2190.0


def _case(mode, target, params=None):
    return make_case(DAY, 1, fault=Fault(mode, [target], params or {}, 600.0, None))


@pytest.fixture(scope="module")
def net():
    return context().network_at(DAY)


@pytest.fixture(scope="module")
def dep():
    return context().deployment_at(DAY)


def _run(case, dep, check, params, seed=11):
    for s in range(seed, seed + 50):                 # skip the rare draw in which a remote test fails
        r = perform(case.world, dep, check, params, 1200.0, s)
        if r.status != "failed":
            return r
    raise AssertionError("every attempt failed")


def test_catalogue_matches_knowledge():
    import yaml
    from pathlib import Path
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "knowledge/practice/checks.yaml").read_text())
    assert {c["id"] for c in doc["checks"]} == set(CHECKS)
    for c in doc["checks"]:
        assert CHECKS[c["id"]][0] == c["class"] and CHECKS[c["id"]][1] == c["minutes"]


def test_portable_otdr_finds_a_cut_and_costs_a_visit(net, dep):
    span = sorted(net.spans)[5]
    c = _case("FIBRE_CUT", span)
    r = _run(c, dep, "otdr_portable", {"span": span})
    assert r.status == "done" and any(e["type"] == "end_of_fibre" for e in r.value["events"])
    assert r.dispatch_sites and effort([r], 1)["site_visits"] == 1
    assert 1.0 <= travel_hours(1, r.dispatch_sites[0]) <= 4.0


def test_cleaning_repairs_a_dirty_connector(net, dep):
    oms = sorted(o for o in net.oms if any(o in lp.oms_path for lp in net.lightpaths.values()))[3]
    c = _case("CONNECTOR_DIRTY", oms, {"added_loss_db": 5.0})
    before = _run(c, dep, "power_meter", {"oms": oms})
    r = _run(c, dep, "inspect_clean", {"oms": oms})
    after = _run(c, dep, "power_meter", {"oms": oms}, seed=99)
    assert r.repaired and "dirty" in r.value["found"]
    assert after.value["input_dbm_per_ch"] > before.value["input_dbm_per_ch"] + 4.0


def test_cross_connect_audit_finds_a_misconnection(net, dep):
    s = next(s for s in sorted(net.services.values(), key=lambda x: x.id)
             if s.working and len(net.odu_paths[s.working].hops) >= 2)
    hop = net.odu_paths[s.working].hops[1]
    c = _case("XC_MISCONNECT", f"otn.{hop.a}|{s.working}")
    r = _run(c, dep, "xc_audit", {"switch": f"otn.{hop.a}"})
    assert r.value["mismatches"] == [s.working]


def test_retune_separates_receiver_from_line(net, dep):
    lp = sorted(net.lightpaths)[2]
    trx = net.lightpaths[lp].dst_trx
    local = _run(_case("RX_DEGRADATION", trx, {"penalty_db": 6.0}), dep, "retune_spare", {"lightpath": lp})
    amp = [a for a in sorted(net.amps) if net.amps[a].oms == net.lightpaths[lp].oms_path[0]][1]
    line = _run(_case("AMP_NF_RISE", amp, {"nf_increase_db": 8.0}), dep, "retune_spare", {"lightpath": lp})
    assert local.value["errors_on_spare"] == "clear" and line.value["errors_on_spare"] == "persist"


def test_embedded_otdr_needs_full_instrumentation(net, dep):
    span = next(s for s in sorted(net.spans) if dep.get(net.oms[net.spans[s].oms].src) != "full")
    c = _case("FIBRE_CUT", span)
    assert _run(c, dep, "otdr_embedded", {"span": span}).status == "unavailable"


def test_remote_tests_sometimes_fail(net, dep):
    c = _case("CLIENT_PORT_FAIL", sorted(p for p in [f"cp.{sid}.{net.services[sid].src}" for sid in net.services])[0])
    statuses = [perform(c.world, dep, "tx_power", {"trx": sorted(net.transponders)[0]}, 1200.0, s).status
                for s in range(400)]
    assert 0 < statuses.count("failed") < 40

"""Scoring rules of Experiment 2 (harness/exp2.score, harness/separability.py): per-field instrument resolution,
ambiguity judged after the agent's own checks, duct cuts needing every cable section, and separating checks
drawn from the agent's catalogue."""
from functools import lru_cache
from types import SimpleNamespace as NS

from adagent.harness.cases2 import make_case2
from adagent.harness.equivalence import cable_sections
from adagent.harness.exp2 import score
from adagent.harness.separability import _same, done_checks, separating_checks
from adagent.tools.advisor import checks_for


def H(*pairs):
    """A hypothesis as score() reads it: causes with a fault mode and an element location."""
    return NS(causes=[NS(fault_mode=m, location=NS(kind="element", element=e)) for m, e in pairs])


@lru_cache(maxsize=None)
def _case(seed, kind):
    return make_case2(2190.0, seed, kind=kind)


def test_same_uses_the_documented_accuracy():
    """One standard with the agent (accuracy.alike): alike when the misfit is below 2 nats, one field within two
    standard deviations of the instrument's and the twin's documented accuracy."""
    # OSNR: sigma sqrt(0.42^2 + 0.55^2) = 0.69 dB, so 1.38 dB
    assert _same({"est_osnr_db": 18.0}, {"est_osnr_db": 19.2})
    assert not _same({"est_osnr_db": 18.0}, {"est_osnr_db": 19.6})
    # a predicted change adds 20 per cent of its size: 4 dB below the no-fault answer widens the threshold
    assert _same({"est_osnr_db": 14.0}, {"est_osnr_db": 15.8}, {"est_osnr_db": 20.0})
    # BER in log10: sigma 0.22 decade
    assert _same({"pre_fec_ber": 1.0e-3}, {"pre_fec_ber": 1.2e-3})
    assert not _same({"pre_fec_ber": 1.0e-3}, {"pre_fec_ber": 1.0e-2})
    # power meter: sigma 0.22 dB
    assert _same({"input_dbm_per_ch": -20.0}, {"input_dbm_per_ch": -20.4})
    assert not _same({"input_dbm_per_ch": -20.0}, {"input_dbm_per_ch": -20.6})
    # errored seconds: 2 plus 20 per cent
    assert _same({"bins": [{"t": 0, "errored_s": 100}]}, {"bins": [{"t": 0, "errored_s": 120}]})
    assert not _same({"bins": [{"t": 0, "errored_s": 0}]}, {"bins": [{"t": 0, "errored_s": 5}]})
    # OTDR event positions are not compared; event losses are (sigma 0.66 dB)
    ev = lambda p, l: {"events": [{"position_km": p, "type": "loss", "loss_db": l}]}  # noqa: E731
    assert _same(ev(10.0, 2.0), ev(30.0, 2.1))
    assert not _same(ev(10.0, 2.0), ev(10.0, 4.0))
    # strings, None and statuses exactly
    assert not _same({"found": "connectors clean"}, {"found": "dirty connector, cleaned"})
    assert not _same({"loss_db": None}, {"loss_db": 5.0})


def test_done_checks_reads_completed_checks():
    case = NS(substrate=NS(now=1200.0, acquisitions=[
        {"check": "coherent_diag", "trx": "trx.a", "status": "done", "minutes": 2, "dispatch_sites": []},
        {"check": "span_loss", "span": "s1", "status": "failed", "minutes": 45, "dispatch_sites": ["X"]},
        {"kind": "otdr", "cost": 1.0, "fulfilled": True},
        {"check": "coherent_diag", "trx": "trx.a", "status": "done", "minutes": 2, "dispatch_sites": []}]))
    assert done_checks(case) == [("coherent_diag", {"trx": "trx.a"}, 1200.0)]


def test_ambiguity_is_judged_after_the_checks_made():
    """A WSS filter shift and a receiver fault look alike on the data held; a retune to a spare port tells them
    apart. Committing to the truth is ambiguous without that check and unambiguous with it."""
    c = make_case2(2190.0, 9217, kind="soft")      # own copy: the test adds an acquisition
    net, f = c.world.net, c.truth.faults[0]
    oms, slot = f.targets[0].split("|")
    lp = next(l for l in net.lightpaths.values() if oms in l.oms_path and str(l.slot) == slot)
    hy = {"T": H((f.mode, f.targets[0])), "R": H(("RX_DEGRADATION", lp.dst_trx))}
    s = score(c, {"outcome": "commit", "hypothesis": "T"}, hy.get, list(hy))
    assert s["outcome"] == "commit_correct_ambiguous" and s["class_size"] == 2
    assert s["ambiguous_after_checks"] == [f"RX_DEGRADATION@{lp.dst_trx}"]
    c.substrate.acquisitions.append({"check": "retune_spare", "lightpath": lp.id, "end": "z", "status": "done",
                                     "minutes": 30, "dispatch_sites": []})
    s = score(c, {"outcome": "commit", "hypothesis": "T"}, hy.get, list(hy))
    assert s["outcome"] == "commit_correct" and s["ambiguous_after_checks"] == [] and s["class_size"] == 2


def test_duct_cut_needs_every_cable_section():
    c = _case(9300, "common_cause")
    f = c.truth.faults[0]
    secs = cable_sections(c.world.net, f.targets)
    assert len(secs) >= 2 and all(len(s) == 2 for s in secs)
    # one section only: names part of the cause; every section, by either fibre: the common cause
    hy = {"ONE": H(("FIBRE_CUT", secs[0][0])),
          "ALL": H(*[("FIBRE_CUT", s[i % 2]) for i, s in enumerate(secs)])}
    one = score(c, {"outcome": "commit", "hypothesis": "ONE"}, hy.get, list(hy))
    assert one["outcome"] == "commit_partial" and len(one["class_sections"]) == len(secs)
    hy1 = {"ONE": hy["ONE"]}
    alone = score(c, {"outcome": "commit", "hypothesis": "ONE"}, hy1.get, list(hy1))
    assert alone["outcome"] == "commit_partial" and alone["truth_present"] is False
    full = score(c, {"outcome": "commit", "hypothesis": "ALL"}, hy.get, list(hy))
    assert full["outcome"] in ("commit_correct", "commit_correct_ambiguous") and full["truth_present"]


def test_separating_checks_are_the_agents_own():
    c = _case(9217, "soft")
    net, f = c.world.net, c.truth.faults[0]
    oms, slot = f.targets[0].split("|")
    lp = next(l for l in net.lightpaths.values() if oms in l.oms_path and str(l.slot) == slot)
    key = f"RX_DEGRADATION@{lp.dst_trx}"
    r = separating_checks(c, [key], budget_hours=6.0, case_seed=9217)
    offered = [x for loc in (lp.dst_trx, f.targets[0]) for x in checks_for(c.substrate.kg, loc)]
    assert r[key] and all((ch, cp) in offered for ch, cp, _ in r[key])


def test_series_of_readings_judged_by_shared_offset_and_departures():
    """Amendment 4.2: a register of one-minute bins is judged by its common offset (once) and by its worst departure
    from that offset, mapped to one reading; a long correct series is not penalised for its length, and a missed
    event still costs the most."""
    import random
    from adagent.accuracy import misfit
    r = random.Random(5)
    pred = [{"t": 60 * i, "rx_min_dbm": -6.0, "errored_s": 0} for i in range(40)]
    ok = [{"t": 60 * i, "rx_min_dbm": round(-6.0 + 0.2 + r.gauss(0, 0.1), 2), "errored_s": 0} for i in range(40)]
    assert misfit({"bins": ok}, {"bins": pred}, "", {"bins": pred}) < 2.0
    missed = [dict(b) for b in ok]
    missed[20]["rx_min_dbm"] = -16.0
    assert misfit({"bins": missed}, {"bins": pred}, "", {"bins": pred}) >= 5.0
    one = [{"t": 0, "rx_min_dbm": -6.5, "errored_s": 0}]
    p1 = [{"t": 0, "rx_min_dbm": -6.0, "errored_s": 0}]
    assert abs(misfit({"bins": one}, {"bins": p1}, "", {"bins": p1}) - misfit(one[0], p1[0], "", p1[0])) < 0.5

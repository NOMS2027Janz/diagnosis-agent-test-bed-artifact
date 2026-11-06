"""World version 2: the hidden gap between the world and the records (world/gap.py, decision D2)."""
import math
import statistics as st

import pytest

from adagent.world.physics import evaluate
from adagent.world.reference import build_reference_national


@pytest.fixture(scope="module")
def nets():
    return build_reference_national(24), build_reference_national(24, gap_scale=1.0)


def test_scale_zero_is_version_one(nets):
    v1, _ = nets
    assert v1.gap_scale == 0 and not v1.calibration_db
    assert all(s.true_loss_db_per_km is None for s in v1.spans.values())
    assert all(a.true_nf_db is None for a in v1.amps.values())


def test_records_unchanged_and_truth_hidden(nets):
    v1, v2 = nets
    assert sorted(v1.lightpaths) == sorted(v2.lightpaths) and sorted(v1.services) == sorted(v2.services)
    for sid in v1.spans:                       # what the plant records say is the same in both versions
        assert v1.spans[sid].nominal_loss_db == v2.spans[sid].nominal_loss_db
    assert all(a.nf_db == 5.5 for a in v2.amps.values())
    nf = [a.actual_nf_db for a in v2.amps.values()]
    assert 5.0 <= min(nf) and max(nf) <= 6.5 and st.pstdev(nf) > 0.1
    alpha = [s.true_loss_db_per_km for s in v2.spans.values()]
    assert 0.19 <= min(alpha) and max(alpha) <= 0.23
    assert all(abs(v) <= 0.5 for v in v2.calibration_db.values()) and len(v2.calibration_db) > 400


def test_scale_scales_the_deviations():
    half = build_reference_national(24, gap_scale=0.5)
    full = build_reference_national(24, gap_scale=1.0)
    for aid in full.amps:
        assert math.isclose(half.amps[aid].true_nf_db - 5.5, 0.5 * (full.amps[aid].true_nf_db - 5.5), abs_tol=1e-9)


def test_records_based_prediction_error_by_path_length(nets):
    """A prediction from the records (version 1 physics on nominal values, as a twin built from the records
    computes) misses the world's OSNR by a roughly constant offset whose spread does not grow with path length,
    since independent errors average out in dB and the ROADMs re-level every channel; the margin error grows,
    because line noise weighs more in a long path's margin. Accepted by Chris, 2026-10-16."""
    v1, v2 = nets
    s1, s2 = evaluate(v1), evaluate(v2)
    pts = []
    for k, r1 in s1.rx.items():
        r2 = s2.rx[k]
        if math.isfinite(r1.osnr_db) and math.isfinite(r2.osnr_db):
            n_amps = sum(len(v1.oms[o].amps) for o in v1.lightpaths[k[0]].oms_path)
            pts.append((n_amps, r2.osnr_db - r1.osnr_db, r2.margin_db - r1.margin_db))
    pts.sort()
    q = len(pts) // 4
    short, long_ = pts[:q], pts[-q:]
    assert all(abs(e) < 3.0 for _, e, _ in pts)
    assert 0.2 < abs(st.mean(e for _, e, _ in pts)) < 1.5
    assert st.pstdev([e for _, e, _ in long_]) <= st.pstdev([e for _, e, _ in short])
    assert abs(st.mean(m for _, _, m in long_)) > abs(st.mean(m for _, _, m in short))


def test_records_complete_removes_mismatches():
    from adagent.world.history import generate_history
    net = build_reference_national(24)
    for sp in net.spans.values():
        sp.recorded_cable, sp.recorded_duct = sp.cable, sp.duct
    h = generate_history(net, seed=1, records_complete=True)
    h0 = generate_history(build_reference_national(24), seed=1)
    assert all(e.t_recorded == e.t for e in h.events)
    assert any(e.t_recorded is None for e in h0.events) and any((e.t_recorded or 0) > e.t for e in h0.events)
    assert [(e.t, e.kind) for e in h.events] == [(e.t, e.kind) for e in h0.events]     # same network and schedule
    assert [i.truth for i in h.incidents] == [i.truth for i in h0.incidents]

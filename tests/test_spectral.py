"""Spectral mode (world/spectral.py; docs/campaign/spectral_design.md): off by default and then without effect;
calibrated against the default filter model; documentation addenda only in spectral mode."""
import math
import os

import pytest

from adagent.world import spectral
from adagent.world.faults import Event, effects_at
from adagent.world.physics import evaluate
from adagent.world.reference import build_reference_national


@pytest.fixture
def no_spectral(monkeypatch):
    monkeypatch.delenv("ADAGENT_SPECTRAL", raising=False)


def test_off_by_default(no_spectral):
    assert not spectral.on()
    net = build_reference_national()
    assert net.spectral is False
    snap = evaluate(net)
    assert snap.spectra_rx == {} and snap.spectra_span == {}


def test_nominal_cascade_changes_nothing(no_spectral):
    net = build_reference_national()
    a = evaluate(net)
    net.spectral = True
    b = evaluate(net)
    for k, r in a.rx.items():
        if math.isfinite(r.gsnr_db):
            assert abs(r.gsnr_db - b.rx[k].gsnr_db) < 1e-9


def test_calibration_one_shifted_filter():
    r = spectral.shape(64.0, 0.0, ((5.0, spectral.NOMINAL_BW_GHZ, spectral.ORDER),))
    assert abs(r["penalty_over_nominal"] - 0.5) < 0.01          # the default model: 0.02 dB per GHz^2


def test_shift_seen_downstream_only(no_spectral):
    net = build_reference_national()
    net.spectral = True
    net.span_spectra_wanted = True
    lp = next(l for l in net.lightpaths.values() if len(l.oms_path) >= 3)
    oms = lp.oms_path[1]
    fx = effects_at([Event("WSS_FILTER_SHIFT", f"{oms}|{lp.slot}", {"offset_ghz": 6.0}, onset=0)], 10)
    snap = evaluate(net, fx)
    before = snap.spectra_span[(net.oms[lp.oms_path[1]].spans[0].id, lp.slot)]
    after = snap.spectra_span[(net.oms[lp.oms_path[2]].spans[0].id, lp.slot)]
    assert abs(before["centroid"]) < 0.05 and after["centroid"] > 1.0


def test_laser_offset_seen_from_the_first_span(no_spectral):
    net = build_reference_national()
    net.spectral = True
    net.span_spectra_wanted = True
    lp = next(iter(net.lightpaths.values()))
    fx = effects_at([Event("LASER_FREQ_DRIFT", lp.src_trx, {"offset_ghz": 6.0}, onset=0)], 10)
    snap = evaluate(net, fx)
    first = snap.spectra_span[(net.oms[lp.oms_path[0]].spans[0].id, lp.slot)]
    assert first["centroid"] > 5.0


def test_addenda_only_in_spectral_mode(monkeypatch):
    from adagent.substrate.lifecycle import Sources
    monkeypatch.delenv("ADAGENT_SPECTRAL", raising=False)
    plain = Sources.load()
    monkeypatch.setenv("ADAGENT_SPECTRAL", "1")
    sp = Sources.load()
    modes = lambda s: {f["id"] for t in s.types.values() for f in t["doc"].get("fault_modes", [])}   # noqa: E731
    assert "WSS_FILTER_NARROWING" not in modes(plain) and "LASER_FREQ_DRIFT" not in modes(plain)
    assert "WSS_FILTER_NARROWING" in modes(sp) and "LASER_FREQ_DRIFT" in modes(sp)


def test_spectral_seeds_need_the_flag(no_spectral):
    from adagent.harness.cases2 import make_case2
    with pytest.raises(ValueError):
        make_case2(2190.0, 5900)

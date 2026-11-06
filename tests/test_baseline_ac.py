"""Arm B-ac (amendment 11): alarm correlation's root alarm handed to manual troubleshooting."""
from adagent.harness.exp2 import run_job


def test_procedure_starts_at_correlations_root():
    r = run_job("B-ac", 2190.0, 2021)
    h = r["baseline"]["trace"]["handoff"]
    assert h["root"][0] == "AMP_INPUT_LOS" and h["start"].startswith("AMP_INPUT_LOS")
    assert "correlation's root" in r["baseline"]["trace"]["trigger"]
    assert r["score"]["outcome"] in ("commit_partial", "commit_correct")


def test_no_alarm_falls_back_to_the_procedures_own_starts():
    r = run_job("B-ac", 2190.0, 2004)
    assert r["baseline"]["trace"]["handoff"]["root"] is None


def test_same_as_b_where_the_start_is_the_same():
    a, b = run_job("B-ac", 2190.0, 2017), run_job("B", 2190.0, 2017)
    assert a["score"]["outcome"] == b["score"]["outcome"]

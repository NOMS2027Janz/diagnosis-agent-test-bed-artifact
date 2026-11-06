"""Hand-checkable tests of the synthesis ledger (specification Section 7)."""
import math

import pytest

from adagent.defaults import LedgerDefaults
from adagent.ledger import NONE_KEY, Ledger
from adagent.schemas import (
    Candidate, Cause, ElementLocation, GeneratorPayload, Hypothesis, HypothesisKind,
    LikelihoodPayload, LikelihoodValue, RankerPayload, SpanLocation,
)


def single(mode, element):
    return Hypothesis(kind=HypothesisKind.SINGLE,
                      causes=[Cause(fault_mode=mode, location=ElementLocation(element=element))])


A = single("FIBRE_CUT", "span-1")
B = single("AMP_PUMP_FAIL", "amp-2")
C = single("CONNECTOR_PULLED", "port-3")
KA, KB, KC = A.key(), B.key(), C.key()


def gen(*pairs, residual=0.0):
    return GeneratorPayload(candidates=[Candidate(hypothesis=h, prior_mass=m) for h, m in pairs],
                            residual_mass=residual)


def lik(values, background=None):
    return LikelihoodPayload(values={k: LikelihoodValue(log_likelihood=v) for k, v in values.items()},
                             background_log_likelihood=background)


def no_floor():
    return Ledger(cfg=LedgerDefaults(prior_floor_eps=0.0))


# ---------------------------------------------------------------- priors (7.1)

def test_single_generator_prior_with_floor():
    led = Ledger()
    led.add_generator("walk", gen((A, 0.6), (B, 0.3), residual=0.1))
    p = led.priors()
    # (1 - 0.01) * mass + 0.01 / 2 ; none gets 0.99 * 0.1
    assert p[KA] == pytest.approx(0.99 * 0.6 + 0.005)
    assert p[KB] == pytest.approx(0.99 * 0.3 + 0.005)
    assert p[NONE_KEY] == pytest.approx(0.099)
    assert sum(p.values()) == pytest.approx(1.0)


def test_mixture_of_two_generators_equal_weights():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.8), residual=0.2))
    led.add_generator("llm", gen((B, 0.6), residual=0.4))
    p = led.priors()
    assert p[KA] == pytest.approx(0.4)
    assert p[KB] == pytest.approx(0.3)
    assert p[NONE_KEY] == pytest.approx(0.3)


def test_duplicates_merge_and_keep_provenance():
    led = no_floor()
    led.add_generator("walk", gen((A, 1.0)))
    led.add_generator("llm", gen((A, 0.5), residual=0.5))
    assert led.named_keys() == [KA]
    assert [t for t, _, _ in led.provenance[KA]] == ["walk", "llm"]
    assert led.priors()[KA] == pytest.approx(0.75)


def test_generator_masses_cannot_exceed_one():
    with pytest.raises(ValueError):
        gen((A, 0.7), (B, 0.4))


def test_compound_prior_uses_cardinality_penalty():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    kc = led.form_compound([KA, KB])
    # explicit prior 0.5 * 0.5 * 0.05 = 0.0125, then renormalised with A and B
    p = led.priors()
    assert p[kc] == pytest.approx(0.0125 / 1.0125)


# ---------------------------------------------------------------- likelihoods (7.2)

def test_one_term_is_bayes_rule():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "power", lik({KA: math.log(0.9), KB: math.log(0.1)}), ["e1"], tau=1.0)
    post = led.posterior()
    assert post[KA] == pytest.approx(0.9)
    assert post[KB] == pytest.approx(0.1)


def test_tempering():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "twin", lik({KA: 0.0, KB: -2.0}), ["e1"], tau=0.5)
    post = led.posterior()
    assert post[KA] / post[KB] == pytest.approx(math.e)   # exp(0.5 * 2)


def test_cap_limits_ratio_to_about_3000():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "twin", lik({KA: 0.0, KB: -72.0}), ["e1"], tau=1.0)  # a 12-sigma residual
    post = led.posterior()
    assert post[KA] / post[KB] == pytest.approx(math.exp(8.0))


def test_declined_hypothesis_is_neutral():
    led = no_floor()
    led.add_generator("walk", gen((A, 1 / 3), (B, 1 / 3), (C, 1 / 3)))
    p = LikelihoodPayload(values={KA: LikelihoodValue(log_likelihood=0.0),
                                  KB: LikelihoodValue(log_likelihood=-2.0),
                                  KC: LikelihoodValue(declined=True, decline_reason="not a fibre fault")})
    led.add_likelihood("t1", "otdr", p, ["e1"], tau=1.0)
    lp = led.log_posterior_unnormalised()
    assert lp[KC] - lp[KA] == pytest.approx(-1.0)          # mean of 0 and -2


# ---------------------------------------------------------------- none-of-these (7.3)

def test_none_of_these_rises_when_named_explain_badly():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.45), (B, 0.45), residual=0.1))
    led.add_likelihood("t1", "power", lik({KA: -6.0, KB: -6.0}, background=-1.0), ["e1"], tau=1.0)
    post = led.posterior()
    assert post[NONE_KEY] > 0.9
    assert led.signals()["regenerate"]


def test_misfit_signal_when_all_named_clipped():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "power", lik({KA: -20.0, KB: -30.0}, background=0.0), ["e1"], tau=1.0)
    assert led.signals()["misfit"]


# ---------------------------------------------------------------- dependence (7.4)

def test_shared_evidence_rescales_tempering():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "bracket", lik({KA: 0.0, KB: -2.0}), ["p1", "p2"], tau=0.8)
    led.add_likelihood("t2", "gnn_like", lik({KA: 0.0, KB: -2.0}), ["p2"], tau=0.8)
    post = led.posterior()
    # each term capped at 1/2 because they share p2 and 0.8 + 0.8 > 1: total log-ratio 2.0
    assert post[KA] / post[KB] == pytest.approx(math.exp(2.0))


def test_independent_terms_multiply():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    led.add_likelihood("t1", "bracket", lik({KA: 0.0, KB: -1.0}), ["p1"], tau=1.0)
    led.add_likelihood("t2", "alarms", lik({KA: 0.0, KB: -1.0}), ["a1"], tau=1.0)
    post = led.posterior()
    assert post[KA] / post[KB] == pytest.approx(math.exp(2.0))


# ---------------------------------------------------------------- coverage (7.5)

def test_late_hypothesis_is_a_gap_and_blocks_commitment():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.9), (B, 0.1)))
    led.add_likelihood("t1", "power", lik({KA: 0.0, KB: -5.0}), ["e1"], tau=1.0)
    assert led.decision()["outcome"] == "commit"
    led.add_generator("llm", gen((C, 0.5), residual=0.0))
    assert "t1" in led.coverage_gaps()[KC]
    led.fill_term("t1", lik({KC: -6.0}))
    assert KC not in led.coverage_gaps()


def test_commit_blocked_while_runner_up_uncovered():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.95), (B, 0.05)))
    led.add_likelihood("t1", "power", lik({KA: 0.0}), ["e1"], tau=1.0)   # B never evaluated
    d = led.decision()
    assert led.coverage_gaps().get(KB) == ["t1"]
    assert d["outcome"] == "set" and d["blocked_by_coverage"]


# ---------------------------------------------------------------- rankers (7.6)

def test_chance_ranker_is_uninformative():
    assert Ledger.ranker_term(True, 0.25, 1, 4) == pytest.approx(0.0)
    assert Ledger.ranker_term(False, 0.25, 1, 4) == pytest.approx(0.0)


def test_ranker_term_values():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.25), (B, 0.25), (C, 0.25), residual=0.25))
    led.add_ranker("r1", "gnn", RankerPayload(ordering=[KA, KB, KC], k=1, recall_at_k=0.9), ["p1"])
    lp = led.log_posterior_unnormalised()
    # in: log(0.9 / (1/3)); out: log(0.1 / (2/3))
    assert lp[KA] - lp[KB] == pytest.approx(math.log(2.7) - math.log(0.15))
    assert lp[KB] == pytest.approx(lp[KC])
    assert lp[NONE_KEY] == pytest.approx(math.log(0.25))   # untouched by the ranker


def test_freezing_never_changes_belief():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.5)))
    before = led.posterior()
    led.freeze([KB])
    assert led.posterior() == pytest.approx(before)


# ---------------------------------------------------------------- refinement (4.3)

def test_refinement_splits_parent_mass():
    span = lambda lo, hi: Hypothesis(kind=HypothesisKind.SINGLE, causes=[Cause(
        fault_mode="SPAN_LOSS_INCREASE", location=SpanLocation(span="s1", km_low=lo, km_high=hi))])
    led = no_floor()
    parent = span(0, 80)
    led.add_generator("walk", gen((parent, 0.6), (B, 0.4)))
    kids = led.refine(parent.key(), [span(0, 20), span(20, 80)], shares=[0.25, 0.75])
    p = led.priors()
    assert p[kids[0]] == pytest.approx(0.15)
    assert p[kids[1]] == pytest.approx(0.45)
    assert parent.key() not in p


# ---------------------------------------------------------------- decision (7.7)

def test_abstention_returns_smallest_set():
    led = no_floor()
    led.add_generator("walk", gen((A, 0.5), (B, 0.45), (C, 0.05)))
    d = led.decision()
    assert d["outcome"] == "set"
    assert d["members"] == [KA, KB]          # 0.95 >= 0.90


def test_worked_example_fibre_cut_matches_from_alarms_to_inference_shape():
    """0.53 prior for the cut; an analogy-style term lifts it to about 0.82."""
    led = no_floor()
    amp = single("AMP_PUMP_FAIL", "amp-1")
    led.add_generator("cause_scoring", gen((A, 0.53), (amp, 0.47)))
    # likelihood ratio chosen to reproduce the 0.53 -> 0.82 illustration
    lr = (0.82 / 0.18) / (0.53 / 0.47)
    led.add_likelihood("analogy", "analogy", lik({KA: math.log(lr), amp.key(): 0.0}), ["cases"], tau=1.0)
    assert led.posterior()[KA] == pytest.approx(0.82)
    assert led.decision()["outcome"] == "commit"

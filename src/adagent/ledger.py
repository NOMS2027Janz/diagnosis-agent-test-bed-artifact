"""The synthesis ledger ("Diagnosis Agent Framework Specification" v0.1, Section 7).

A deterministic engine. It holds the active hypotheses, the terms applied to them and the
resulting posterior. No LLM writes to it. Section numbers in comments refer to the
specification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .defaults import LEDGER, LedgerDefaults
from .schemas import (
    INSTRUMENT_MISREPORT, Cause, ElementLocation, GeneratorPayload, Hypothesis,
    HypothesisKind, LikelihoodPayload, RankerPayload,
)

NONE_KEY = "NONE_OF_THESE"


def _logsumexp(xs: list[float]) -> float:
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))


@dataclass
class _Entry:
    hyp: Hypothesis
    frozen: bool = False
    retired: bool = False
    instrument_prior: float = 0.0      # for instrument-fault hypotheses
    explicit_prior: Optional[float] = None   # for ledger-formed compounds and refinements


@dataclass
class _LikTerm:
    term_id: str
    tool: str
    tau: float
    evidence: list[str]
    values: dict[str, float]               # hypothesis key -> raw log-likelihood (evaluated)
    declined: set[str]
    background: Optional[float]
    targets: set[str]                      # keys the term must cover (for coverage)


@dataclass
class _RankTerm:
    term_id: str
    tool: str
    pool: list[str]
    k: int
    recall: float
    evidence: list[str]
    mode: str = "likelihood_ratio"         # or "jeffrey"
    tau: float = 1.0                       # tempering, as for likelihood terms (shares of the same evidence)


@dataclass
class Ledger:
    cfg: LedgerDefaults = field(default_factory=lambda: LEDGER)
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _gen: dict[str, tuple[GeneratorPayload, float]] = field(default_factory=dict)  # tool -> (payload, weight)
    _lik: list[_LikTerm] = field(default_factory=list)
    _rank: list[_RankTerm] = field(default_factory=list)
    _evidence_terms: dict[str, list[str]] = field(default_factory=dict)
    _entropy_history: list[float] = field(default_factory=list)
    provenance: dict[str, list[tuple[str, float, str]]] = field(default_factory=dict)

    def __post_init__(self):
        self._entries[NONE_KEY] = _Entry(Hypothesis(id=NONE_KEY, kind=HypothesisKind.NONE_OF_THESE))

    # ------------------------------------------------------------------ hypotheses

    def _add(self, h: Hypothesis) -> str:
        key = h.key()
        if key not in self._entries:
            h = h.model_copy(update={"id": key})
            self._entries[key] = _Entry(h)
        return key

    def active_keys(self) -> list[str]:
        return [k for k, e in self._entries.items() if not e.retired]

    def named_keys(self) -> list[str]:
        return [k for k in self.active_keys()
                if self._entries[k].hyp.kind in (HypothesisKind.SINGLE, HypothesisKind.COMPOUND)]

    def hypothesis(self, key: str) -> Hypothesis:
        return self._entries[key].hyp

    # ------------------------------------------------------------------ Section 7.1 priors

    def add_generator(self, tool: str, payload: GeneratorPayload, weight: Optional[float] = None) -> list[str]:
        """Record a generator's proposal. Weight None means equal weighting (Appendix B)."""
        keys = []
        for c in payload.candidates:
            k = self._add(c.hypothesis)
            keys.append(k)
            self.provenance.setdefault(k, []).append((tool, c.prior_mass, c.rationale))
        self._gen[tool] = (payload, weight if weight is not None else float("nan"))
        return keys

    def add_instrument_fault(self, instrument: str, prior: float) -> str:
        h = Hypothesis(kind=HypothesisKind.INSTRUMENT_FAULT, causes=[
            Cause(fault_mode=INSTRUMENT_MISREPORT, location=ElementLocation(element=instrument))])
        k = self._add(h)
        self._entries[k].instrument_prior = prior
        return k

    def form_compound(self, keys: list[str]) -> str:
        """Form a compound from single hypotheses. Prior = product * kappa^(m-1)."""
        causes = [c for k in keys for c in self._entries[k].hyp.causes]
        h = Hypothesis(kind=HypothesisKind.COMPOUND, causes=causes)
        p0 = self.priors()
        prior = math.prod(p0[k] for k in keys) * self.cfg.cardinality_kappa ** (len(keys) - 1)
        k = self._add(h)
        self._entries[k].explicit_prior = prior
        return k

    def refine(self, parent: str, children: list[Hypothesis], shares: Optional[list[float]] = None) -> list[str]:
        """Section 4.3. Children partition the parent's location; mass split by shares."""
        if shares is None:
            shares = [1.0 / len(children)] * len(children)
        if abs(sum(shares) - 1.0) > 1e-9:
            raise ValueError("refinement shares must sum to one")
        parent_prior = self.priors()[parent]
        out = []
        for h, s in zip(children, shares):
            h = h.model_copy(update={"parent": parent})
            k = self._add(h)
            self._entries[k].explicit_prior = parent_prior * s
            out.append(k)
        self._entries[parent].retired = True
        for t in self._lik:                 # children must now be covered by every term
            t.targets |= set(out)
        return out

    def priors(self) -> dict[str, float]:
        """Mixture prior over active hypotheses, normalised (Section 7.1)."""
        eps = self.cfg.prior_floor_eps
        gens = list(self._gen.items())
        ws = [w for _, (_, w) in gens]
        if gens and all(math.isnan(w) for w in ws):
            ws = [1.0 / len(gens)] * len(gens)
        elif gens:
            s = sum(w for w in ws if not math.isnan(w))
            ws = [w / s for w in ws]
        named_from_gen = sorted({c.hypothesis.key() for _, (p, _) in gens for c in p.candidates})
        m = max(1, len(named_from_gen))
        raw: dict[str, float] = {}
        for key in named_from_gen:
            mix = sum(w * next((c.prior_mass for c in p.candidates if c.hypothesis.key() == key), 0.0)
                      for (_, (p, _)), w in zip(gens, ws))
            raw[key] = (1 - eps) * mix + eps / m
        raw[NONE_KEY] = (1 - eps) * sum(w * p.residual_mass for (_, (p, _)), w in zip(gens, ws)) if gens else 1.0
        for k, e in self._entries.items():
            if e.hyp.kind == HypothesisKind.INSTRUMENT_FAULT:
                raw[k] = e.instrument_prior
            if e.explicit_prior is not None:
                raw[k] = e.explicit_prior
        active = set(self.active_keys())
        raw = {k: v for k, v in raw.items() if k in active}
        z = sum(raw.values())
        return {k: v / z for k, v in raw.items()}

    # ------------------------------------------------------------------ Section 7.2 likelihoods

    def add_likelihood(self, term_id: str, tool: str, payload: LikelihoodPayload,
                       evidence: list[str], tau: float) -> None:
        values, declined = {}, set()
        for k, v in payload.values.items():
            if v.declined or v.log_likelihood is None:
                declined.add(k)
            else:
                values[k] = v.log_likelihood
        term = _LikTerm(term_id, tool, tau, list(evidence), values, declined,
                        payload.background_log_likelihood, set(self.active_keys()) - {NONE_KEY})
        self._lik.append(term)
        for e in evidence:
            self._evidence_terms.setdefault(e, []).append(term_id)
        self._entropy_history.append(self.entropy_bits())

    def fill_term(self, term_id: str, payload: LikelihoodPayload) -> None:
        """Close coverage gaps (Section 7.5) by adding values for late hypotheses."""
        t = next(t for t in self._lik if t.term_id == term_id)
        for k, v in payload.values.items():
            if v.declined or v.log_likelihood is None:
                t.declined.add(k)
                t.values.pop(k, None)
            else:
                t.values[k] = v.log_likelihood
                t.declined.discard(k)

    def _effective_tau(self, t: _LikTerm | _RankTerm, tau: float) -> float:
        """Section 7.4: per shared evidence item, consumers' taus sum to at most one."""
        eff = tau
        for e in t.evidence:
            consumers = self._evidence_terms.get(e, [])
            if len(consumers) > 1:
                taus = [self._term_tau(c) for c in consumers]
                if sum(taus) > 1.0 + 1e-12:
                    eff = min(eff, 1.0 / len(consumers))
        return eff

    def _term_tau(self, term_id: str) -> float:
        for t in self._lik:
            if t.term_id == term_id:
                return t.tau
        for r in self._rank:
            if r.term_id == term_id:
                return r.tau
        return 1.0

    def _term_contrib(self, t: _LikTerm) -> dict[str, float]:
        """Clipped, tempered contribution of one likelihood term to each active key."""
        active = self.active_keys()
        evaluated = {k: v for k, v in t.values.items() if k in active}
        candidates = list(evaluated.values()) + ([t.background] if t.background is not None else [])
        if not candidates:
            return {}
        best = max(candidates)
        clipped = {k: max(v, best - self.cfg.likelihood_cap_nats) for k, v in evaluated.items()}
        mean = sum(clipped.values()) / len(clipped) if clipped else 0.0
        out = {}
        for k in active:
            if k == NONE_KEY:
                v = max(t.background, best - self.cfg.likelihood_cap_nats) if t.background is not None else mean
            elif k in clipped:
                v = clipped[k]
            else:   # declined, uncovered or untargeted: neutral (Sections 6.3, 7.5)
                v = mean
            out[k] = v
        tau = self._effective_tau(t, t.tau)
        return {k: tau * v for k, v in out.items()}

    # ------------------------------------------------------------------ Section 7.6 rankers

    def add_ranker(self, term_id: str, tool: str, payload: RankerPayload,
                   evidence: list[str], mode: str = "likelihood_ratio", tau: float = 1.0) -> None:
        self._rank.append(_RankTerm(term_id, tool, list(payload.ordering), payload.k,
                                    payload.recall_at_k, list(evidence), mode, tau))
        for e in evidence:
            self._evidence_terms.setdefault(e, []).append(term_id)
        self._entropy_history.append(self.entropy_bits())

    @staticmethod
    def ranker_term(in_top: bool, recall: float, k: int, n: int) -> float:
        q = k / n
        if in_top:
            return math.log(recall / q)
        return math.log((1 - recall) / (1 - q)) if recall < 1 else -math.inf

    def freeze(self, keys: list[str]) -> None:
        for k in keys:
            self._entries[k].frozen = True

    # ------------------------------------------------------------------ posterior

    def log_posterior_unnormalised(self) -> dict[str, float]:
        p0 = self.priors()
        lp = {k: math.log(v) if v > 0 else -math.inf for k, v in p0.items()}
        for t in self._lik:
            for k, c in self._term_contrib(t).items():
                lp[k] += c
        for r in self._rank:
            if r.mode != "likelihood_ratio":
                continue
            n, top = len(r.pool), set(r.pool[:r.k])
            tau = self._effective_tau(r, r.tau)
            for k in r.pool:
                if k in lp:
                    lp[k] += tau * self.ranker_term(k in top, r.recall, r.k, n)
        return lp

    def posterior(self) -> dict[str, float]:
        lp = self.log_posterior_unnormalised()
        finite = [v for v in lp.values() if v != -math.inf]
        z = _logsumexp(finite)
        post = {k: (math.exp(v - z) if v != -math.inf else 0.0) for k, v in lp.items()}
        for r in self._rank:                     # Jeffrey conditioning, experiments only
            if r.mode == "jeffrey":
                top = set(r.pool[:r.k]) & set(post)
                rest = set(post) - top
                m_in, m_out = sum(post[k] for k in top), sum(post[k] for k in rest)
                for k in top:
                    post[k] = r.recall * post[k] / m_in if m_in > 0 else 0.0
                for k in rest:
                    post[k] = (1 - r.recall) * post[k] / m_out if m_out > 0 else 0.0
        return post

    def entropy_bits(self) -> float:
        return -sum(p * math.log2(p) for p in self.posterior().values() if p > 0)

    # ------------------------------------------------------------------ Section 7.5 coverage

    def coverage_gaps(self) -> dict[str, list[str]]:
        gaps: dict[str, list[str]] = {}
        for t in self._lik:
            for k in t.targets:
                if k in self.active_keys() and not self._entries[k].frozen \
                        and k not in t.values and k not in t.declined:
                    gaps.setdefault(k, []).append(t.term_id)
        # hypotheses added after a term was applied are also uncovered
        for t in self._lik:
            for k in self.named_keys():
                if k not in t.targets and k not in t.values and k not in t.declined \
                        and not self._entries[k].frozen:
                    gaps.setdefault(k, []).append(t.term_id)
        return {k: sorted(set(v)) for k, v in gaps.items()}

    # ------------------------------------------------------------------ Section 7.7 decision

    def decision(self) -> dict:
        post = self.posterior()
        gaps = self.coverage_gaps()
        ranked = sorted(post.items(), key=lambda kv: kv[1], reverse=True)
        leader, p_lead = ranked[0]
        runner = ranked[1][0] if len(ranked) > 1 else None
        p_none = post.get(NONE_KEY, 0.0)
        blocked = leader in gaps or (runner in gaps if runner else False)
        if (leader != NONE_KEY and p_lead >= self.cfg.theta_commit
                and p_none < self.cfg.theta_open and not blocked):
            return {"outcome": "commit", "hypothesis": leader, "probability": p_lead}
        target, mass, members = 1 - self.cfg.alpha_set, 0.0, []
        for k, p in ranked:
            members.append(k)
            mass += p
            if mass >= target:
                break
        return {"outcome": "set", "members": members, "mass": mass,
                "cause_probably_unconsidered": p_none >= self.cfg.theta_open,
                "blocked_by_coverage": blocked}

    # ------------------------------------------------------------------ Section 7.8 signals

    def signals(self) -> dict:
        post = self.posterior()
        regen = post.get(NONE_KEY, 0.0) > self.cfg.theta_regen
        n = self.cfg.stall_terms
        h = self._entropy_history + [self.entropy_bits()]
        stall = len(h) > n and (h[-1 - n] - h[-1]) < self.cfg.stall_entropy_bits
        misfit = False
        named = self.named_keys()
        for t in self._lik:
            if t.background is None or not named:
                continue
            cands = [t.values[k] for k in named if k in t.values]
            if cands and all(v <= t.background - self.cfg.likelihood_cap_nats for v in cands):
                misfit = True
        return {"regenerate": regen or stall, "regenerate_reason":
                ("none_of_these" if regen else "") + (" stall" if stall else ""),
                "coverage_gaps": self.coverage_gaps(), "misfit": misfit}

    # ------------------------------------------------------------------ projections

    def projection(self, by: str) -> dict[str, float]:
        """Posterior projected onto cause nature ('mode') or location ('location')."""
        out: dict[str, float] = {}
        for k, p in self.posterior().items():
            h = self._entries[k].hyp
            if not h.causes:
                label = NONE_KEY
            elif by == "mode":
                label = "+".join(sorted(c.fault_mode for c in h.causes))
            else:
                label = "+".join(sorted(c.location.key() for c in h.causes))
            out[label] = out.get(label, 0.0) + p
        return out

    def snapshot(self) -> dict:
        post = self.posterior()
        return {
            "posterior": post,
            "priors": self.priors(),
            "by_mode": self.projection("mode"),
            "by_location": self.projection("location"),
            "terms": [{"id": t.term_id, "tool": t.tool, "tau_effective": self._effective_tau(t, t.tau),
                       "contribution": self._term_contrib(t)} for t in self._lik],
            "rankers": [{"id": r.term_id, "tool": r.tool, "k": r.k, "n": len(r.pool),
                         "recall": r.recall, "mode": r.mode} for r in self._rank],
            "decision": self.decision(),
            "signals": self.signals(),
            "provenance": self.provenance,
        }

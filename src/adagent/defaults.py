"""Default values from "Diagnosis Agent Framework Specification" Appendix B.

Every value here is provisional. Change values only in this file, and record each change
in CHANGELOG.md, so that the engineering-stability measurement stays honest.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LedgerDefaults:
    prior_floor_eps: float = 0.01          # Section 7.1
    cardinality_kappa: float = 0.05        # Section 7.1, per additional coincident cause
    likelihood_cap_nats: float = 8.0       # Section 7.2, about 3000:1
    theta_commit: float = 0.80             # Section 7.7
    theta_open: float = 0.20               # Section 7.7
    alpha_set: float = 0.10                # Section 7.7
    theta_regen: float = 0.25              # Section 7.8
    stall_entropy_bits: float = 0.1        # Section 7.8
    stall_terms: int = 3                   # Section 7.8
    # Tempering until validated (Section 7.2, Appendix B)
    tau_deterministic: float = 1.0
    tau_twin: float = 0.7
    tau_interpreter: float = 0.8
    # Proposer qualitative strength to prior mass (Section 8, Appendix B)
    proposer_strength: dict = field(default_factory=lambda: {
        "strong": 0.5, "plausible": 0.3, "long_shot": 0.1})


@dataclass(frozen=True)
class ScoringDefaults:
    equivalence_sigma: float = 2.0         # Section 9.2
    span_location_tolerance_km: float = 2.0  # Section 9.2


LEDGER = LedgerDefaults()
SCORING = ScoringDefaults()

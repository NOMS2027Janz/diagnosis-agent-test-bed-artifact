"""The reasoning-stack interface.

A stack consumes two lifted semantic models (and, if it uses one, a shared
reference) and returns a Reconciliation: the correspondences it proposes, the
concepts it leaves as residual, and a tally of the mechanical work it did.

This is the seam. The two stacks shipped here are model-agnostic and run with no
model access. A language-model agent stack implements the same `reconcile`
signature and drops in behind it, which is where the cognition placement becomes
a live variable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from reconcile.model import SemanticModel
from reconcile.reference import Reference


@dataclass
class Reconciliation:
    stack: str
    uses_reference: bool
    placement: str
    proposed: list[frozenset] = field(default_factory=list)   # {a_id, b_id} pairs
    residual_a: list[str] = field(default_factory=list)
    residual_b: list[str] = field(default_factory=list)
    work: dict = field(default_factory=dict)    # candidates, bilateral_checks, binding_ops
    effort: dict = field(default_factory=dict)  # LM stacks only: tokens, reasoning_tokens, latency_s, model
    confidence: dict = field(default_factory=dict)  # LM stacks only: {a_id,b_id} pair -> confidence in [0,1]


class ReasoningStack(ABC):
    name: str = "stack"
    uses_reference: bool = False

    @abstractmethod
    def reconcile(
        self,
        a: SemanticModel,
        b: SemanticModel,
        reference: Reference | None = None,
        placement: str = "both_cognitive",
    ) -> Reconciliation: ...

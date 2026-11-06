"""Construct-then-bind: the end-to-end standard-free protocol (Track A / A1).

The cross-domain setting measures a single-pass binding with the shared reference
either pre-given or absent. This module closes the gap the study brackets: the agents
build the thin shared reference *themselves* from the two models, and then bind through
it, with none pre-given. Two agent steps:

  1. ``construct_reference(a, b, model)`` — read both models and propose a thin shared
     reference: identity-only entries (id, label, synonyms, class, definition, example)
     naming the concepts the two models appear to share. It does not resolve
     correspondences; it proposes the shared naming frame.

  2. ``bind_through_reference(a, b, reference, model, placement)`` — bind each side's
     concepts to that constructed reference by matching definitions and examples (not
     the models' own declared bindings, which are withheld), and emit the
     correspondences where the two sides bind to the same entry, refusing look-alikes.

The two steps are deliberately separate so the constructed reference is an auditable
artefact, and so the value of *constructing the ground* can be measured against the
reference-given upper bound and the reference-absent lower bound.

The OpenAI client is injectable for offline testing; nothing here calls the network
unless a real client is used.
"""
from __future__ import annotations

import time

from pydantic import BaseModel

from .reference import Reference, ReferenceEntry
from .stacks.agent_openai import _concept_line, _inert_sides


# --------------------------------------------------------------------------- step 1
class _Entry(BaseModel):
    id: str
    label: str
    synonyms: list[str] = []
    cls: str = ""
    definition: str = ""
    example: str = ""


class _ConstructedReference(BaseModel):
    entries: list[_Entry]


CONSTRUCT_SYSTEM = (
    "Two independently authored models of the same operational territory are given, each "
    "a set of concepts with labels, kinds, glosses, and examples. Build a THIN SHARED "
    "REFERENCE the two can bind through: a COMPACT set of identity-only entries, each with "
    "a stable id, a preferred label, synonyms, a shallow class, a one-line disambiguating "
    "definition, and one canonical example. The reference is SHARED. Where a concept in "
    "model A and a concept in model B plausibly denote the same thing, create ONE entry that "
    "both can bind to - do NOT make a separate entry for each side's version, or the two "
    "sides will never meet on a common anchor. Add a distinct entry only for a concept that "
    "is genuinely unique to one side. Aim for a compact frame with FAR FEWER entries than "
    "the total number of concepts across the two models, because most concepts pair up. Do "
    "not copy either model's internal identifiers as entry ids - invent neutral ids. Carry "
    "identity, a disambiguating definition, and an example, nothing more; the reference is a "
    "naming frame, not a model of the domain. Refuse to merge concepts that only share a "
    "surface word but differ in meaning - give those separate entries."
)


def _model_payload(model, *, inert: bool) -> dict:
    return {"system": getattr(model, "system", ""), "dialect": getattr(model, "dialect", ""),
            "concepts": [_concept_line(c, inert=inert, include_ref=False) for c in model.concepts]}


def construct_reference(a, b, model: str, client=None) -> tuple[Reference, dict]:
    """Agent step 1: build a thin shared reference from the two models."""
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    import json
    user = json.dumps({"model_a": _model_payload(a, inert=False),
                       "model_b": _model_payload(b, inert=False)}, indent=1)
    t0 = time.time()
    completion = client.chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": CONSTRUCT_SYSTEM},
                  {"role": "user", "content": user}],
        response_format=_ConstructedReference,
    )
    elapsed = time.time() - t0
    parsed = completion.choices[0].message.parsed
    ref = Reference(id="constructed", kind="constructed",
                    entries=[ReferenceEntry(id=e.id, label=e.label, definition=e.definition,
                                            example=e.example, cls=e.cls,
                                            synonyms=tuple(e.synonyms)) for e in parsed.entries])
    usage = getattr(completion, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    effort = {"construct_total_tokens": getattr(usage, "total_tokens", None),
              "construct_reasoning_tokens": getattr(details, "reasoning_tokens", None),
              "construct_latency_s": round(elapsed, 2), "entries": len(ref.entries)}
    return ref, effort


# --------------------------------------------------------------------------- step 2
class _Corr(BaseModel):
    a_id: str
    b_id: str
    confidence: float = 1.0


class _BindResult(BaseModel):
    correspondences: list[_Corr]


BIND_SYSTEM = (
    "Two models are given, together with a shared reference the agents have just built: "
    "identity-only entries, each with an id, definition, and canonical example. Bind each "
    "concept on each side to the reference entry whose definition and example fit it, "
    "matching on meaning rather than on surface labels, because a shared word can name "
    "different concepts and different words the same concept. Then emit the "
    "correspondences: two concepts correspond exactly when both bind to the SAME reference "
    "entry. Refuse look-alikes that share only a word, and leave a concept unmatched "
    "(in neither correspondence) rather than guess when no entry fits. Return only the "
    "correspondences, each as {a_id, b_id, confidence}."
)


def bind_through_reference(a, b, reference: Reference, model: str,
                           placement: str = "both_cognitive", inert_side: str = "b",
                           client=None) -> tuple[_BindResult, dict]:
    """Agent step 2: bind both sides to the constructed reference and derive correspondences."""
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    import json
    a_inert, b_inert = _inert_sides(placement, inert_side)
    payload = {
        "model_a": _model_payload(a, inert=a_inert),
        "model_b": _model_payload(b, inert=b_inert),
        "reference": [{"id": e.id, "label": e.label, "class": e.cls,
                       "definition": e.definition, "example": e.example,
                       "synonyms": list(e.synonyms)} for e in reference.entries],
    }
    user = json.dumps(payload, indent=1)
    t0 = time.time()
    completion = client.chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": BIND_SYSTEM},
                  {"role": "user", "content": user}],
        response_format=_BindResult,
    )
    elapsed = time.time() - t0
    parsed = completion.choices[0].message.parsed
    usage = getattr(completion, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    effort = {"bind_total_tokens": getattr(usage, "total_tokens", None),
              "bind_reasoning_tokens": getattr(details, "reasoning_tokens", None),
              "bind_latency_s": round(elapsed, 2), "model": model}
    return parsed, effort

"""The lift, performed by an agent (Brad's point 1).

The study reconciles *lifted* semantic models: each concept carries not only its lexical
surface (label, synonyms) and its schema structure (kind, relations) and data (instances),
but also the semantic explanation layer a cognitive side volunteers about itself - a gloss
and a worked example. In the benchmark cases that explanation layer is a pre-materialised
fixture standing for what a cognitive side produces when it lifts and explains itself.

This module instruments that step directly: given only a side's *data-model surface* - its
labels, synonyms, kinds, relations, and instances, with the explanation layer, the reference
binding, the other model, and the gold all withheld - an agent produces each concept's gloss
and worked example. The result is an agent-lifted model whose explanation layer was generated,
not authored, and is otherwise identical to the fixture. Reconciling over it, and comparing to
the fixture lift, measures whether the lift is agent-performable and whether reconciliation is
invariant to who performed it.

The OpenAI client is injectable for offline testing; nothing here touches the network unless a
real client is used.
"""
from __future__ import annotations

import dataclasses
import json
import re
import time

from pydantic import BaseModel

from .model import SemanticModel


class _Lifted(BaseModel):
    id: str
    gloss: str
    example: str


class _LiftResult(BaseModel):
    concepts: list[_Lifted]


class _LiftedTraced(BaseModel):
    id: str
    evidence: str
    gloss: str
    example: str


class _LiftResultTraced(BaseModel):
    concepts: list[_LiftedTraced]


# Appended to LIFT_SYSTEM only when trace=True: asks the agent to make its reasoning visible per
# concept, so a "lift in the act" can be shown. The evidence is captured for display only; it is not
# fed back into reconciliation, so the measured study is unaffected.
TRACE_EXTRA = (
    " In addition, for EACH concept give a one-line EVIDENCE note: name the specific surface signals you "
    "used to read it (its kind, which of its relations, which instances) and any look-alike concept in the "
    "same model you ruled out, so your reading can be followed step by step. Put it in the 'evidence' field. "
    "Return one {id, evidence, gloss, example} for every concept id given."
)


LIFT_SYSTEM = (
    "You are given ONE side of a network or service management data model: a set of concepts as they "
    "sit in a schema, each with its label, any synonyms, a shallow kind, structural relations to other "
    "concepts in the same model, and concrete instances (records) that realise it. You are NOT given any "
    "definitions, the other model, or any shared reference. LIFT this data model to a semantic model: for "
    "EACH concept, write a one-sentence GLOSS stating what the concept means in this domain, and ONE "
    "concrete worked EXAMPLE. Ground your reading in the concept's kind, its relations, and its instances; "
    "do not merely restate the label. Return one {id, gloss, example} for every concept id given, using the "
    "same ids. Be precise and disambiguating: the gloss is what another system would rely on to decide "
    "whether one of its own concepts means the same thing."
)


def _surface_line(c) -> dict:
    """The data-model surface handed to the lift: everything a schema and its data carry, but NOT the
    explanation layer (gloss/example) and NOT the reference binding (ref)."""
    return {
        "id": c.id,
        "label": c.label,
        "synonyms": list(c.synonyms),
        "kind": c.kind,
        "relations": list(c.relations),
        "instances": list(c.instances),
    }


# When a shared reference IS supplied to the lift (the reference-in-lift condition of the portability
# programme's Phase 3), this overrides LIFT_SYSTEM's "you are NOT given a shared reference" clause.
REF_OVERRIDE = (
    "\n\nOVERRIDE: A shared reference IS provided ('reference' in the payload) — a set of common-ground "
    "entries (id, label, class, definition, example). You MAY bind each concept to the entry whose meaning "
    "fits it and use that common ground to write a precise gloss, DISSOLVING the shared meaning into the "
    "concept's own gloss (do not merely cite the entry id). Disregard the earlier statement that no shared "
    "reference is given."
)


def lift_model(sm: SemanticModel, llm_model: str, client=None, trace: bool = False,
               reference=None) -> tuple[SemanticModel, dict]:
    """Agent-perform the lift on one side. Returns (agent-lifted SemanticModel, effort).

    The returned model copies every fixture field except gloss and example, which are replaced by the
    agent's output. A concept the agent fails to return keeps an empty explanation layer (an honest lift
    miss) and is counted against coverage. If `reference` is given (a Reference), the lifter may use its
    entries as common ground (reference-in-lift); default None reproduces the original standard-free lift.
    """
    if client is None:
        from openai import OpenAI
        client = OpenAI()
    payload = {
        "system": sm.system,
        "dialect": sm.dialect,
        "concepts": [_surface_line(c) for c in sm.concepts],
    }
    if reference is not None:
        payload["reference"] = [{"id": e.id, "label": e.label, "class": e.cls,
                                 "definition": e.definition, "example": e.example,
                                 "synonyms": list(e.synonyms)} for e in reference.entries]
    user = json.dumps(payload, indent=1)
    system = LIFT_SYSTEM + (TRACE_EXTRA if trace else "") + (REF_OVERRIDE if reference is not None else "")
    t0 = time.time()
    completion = client.chat.completions.parse(
        model=llm_model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format=(_LiftResultTraced if trace else _LiftResult),
    )
    elapsed = time.time() - t0
    parsed = completion.choices[0].message.parsed
    produced = {p.id: (p.gloss, p.example) for p in parsed.concepts}
    evidence = {p.id: getattr(p, "evidence", "") for p in parsed.concepts} if trace else {}

    lifted_concepts = []
    covered = 0
    for c in sm.concepts:
        if c.id in produced:
            gloss, example = produced[c.id]
            covered += 1
        else:
            gloss, example = "", ""
        lifted_concepts.append(dataclasses.replace(c, gloss=gloss, example=example))
    lifted = SemanticModel(system=sm.system, dialect=sm.dialect,
                           modules=sm.modules, concepts=lifted_concepts)

    usage = getattr(completion, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    n = len(sm.concepts)
    effort = {
        "lift_total_tokens": getattr(usage, "total_tokens", None),
        "lift_reasoning_tokens": getattr(details, "reasoning_tokens", None),
        "lift_latency_s": round(elapsed, 2),
        "lift_coverage": round(covered / n, 3) if n else 0.0,
        "concepts": n,
        "model": llm_model,
    }
    if trace:
        effort["trace"] = evidence
    return lifted, effort


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "that", "this",
         "is", "are", "be", "by", "with", "as", "it", "its", "at", "from", "into", "one",
         "which", "where", "within", "between", "each"}


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def gloss_fidelity(agent_gloss: str, fixture_gloss: str) -> float:
    """A lightweight, offline lexical proxy for how close an agent-produced gloss is to the fixture
    gloss: Jaccard overlap of content words. 1.0 = identical content words, 0.0 = disjoint. This is a
    coarse witness, not a semantic judge; the reconciliation-transfer delta is the real measure."""
    a, b = _content_words(agent_gloss), _content_words(fixture_gloss)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)

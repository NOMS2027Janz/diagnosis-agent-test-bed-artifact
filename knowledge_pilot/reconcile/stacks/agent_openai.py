"""OpenAI agent stack: a language-model reasoning stack behind ReasoningStack.

It runs the reconciliation as a cognitive agent. Given the two lifted semantic
models, it proposes correspondences, deciding by gloss and canonical example
rather than by names, and marks concepts with no counterpart as residual.

Cognition placement is a first-class variable:

* both_cognitive — both sides are live, interrogable agents. Each concept is
  presented in full, and (with the reference) carries its binding.
* one_inert — one side cannot explain itself. Its concepts are presented as label
  and kind only, with no gloss, synonyms, example, or binding. The live agent must
  reconstruct the inert side's meaning. With the reference, it does so by matching
  each inert concept to the reference entry whose definition and example fit, which
  is how a published anchor supplies the meaning the silent side cannot volunteer.

Running the SAME stack with and without the reference, at each placement, and
comparing quality and the effort the model spends, tests H1 and its sharper form:
the reference's benefit should grow as cognition recedes.

Requires `pip install openai` and an `OPENAI_API_KEY` in the environment. The key
is read only by the OpenAI SDK; this module never logs or transmits it elsewhere.
See SETUP_OPENAI.md.
"""
from __future__ import annotations

import json
import os
import time

from pydantic import BaseModel

from reconcile.model import SemanticModel
from reconcile.reference import Reference
from reconcile.stacks.base import ReasoningStack, Reconciliation

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6")

SYSTEM = (
    "You are a cognitive agent reconciling two independently authored semantic "
    "models of ONE network. Each concept has an id, a label, and a kind, and a live "
    "side also gives synonyms, a gloss, and a canonical example. Decide which "
    "concept in model A denotes the SAME thing as which concept in model B. Judge "
    "by meaning, not by the label alone: two concepts with similar names may denote "
    "different things (a false cognate), and two with different names may denote the "
    "same thing. A concept may have no counterpart; put its id in the residual list "
    "for its side. Propose each correspondence at most once, and use only ids that "
    "appear in the models."
)

INERT_NOTE = (
    "\n\nOne model is INERT: it cannot explain itself in words. Its concepts are "
    "given without gloss, synonyms, or example, but WITH their structural relations "
    "(edges to other concepts in the same model) and concrete instances (its actual "
    "data). Reconstruct what each inert concept means from that structure and those "
    "instances, and from the shared reference if one is provided. This is real work: "
    "a label alone can mislead (a false cognate), but the relations and instances "
    "reveal what a concept actually is."
)

BOTH_INERT_NOTE = (
    "\n\nBOTH models are INERT: neither can explain itself in words, and neither can "
    "confirm anything. Each concept is given without gloss, synonyms, or example, but "
    "WITH its structural relations and concrete instances. You are a third party: "
    "reconstruct BOTH sides' meanings from their structure and data, and where a "
    "shared reference is provided, bind each side to it. Because no side can confirm, "
    "you can only PROPOSE candidate correspondences for external adjudication; be "
    "conservative and leave a concept as residual when the evidence underdetermines "
    "it. A label alone can mislead; the relations and instances reveal what a concept "
    "actually is."
)


def _placement_note(placement: str) -> str:
    return {"one_inert": INERT_NOTE, "both_inert": BOTH_INERT_NOTE}.get(placement, "")


def _reference_note(placement: str) -> str:
    if placement == "both_inert":
        return (
            "\n\nYou are given a shared reference: identity-only entries, each with an "
            "id, definition, and canonical example. NEITHER model carries its binding, "
            "so decide which reference entry each concept on each side denotes by "
            "matching it to the entry whose definition and example fit, then propose a "
            "correspondence where the two sides map to the same entry. With both sides "
            "mute, the reference is the main anchor of meaning and the main guard "
            "against false cognates."
        )
    if placement == "one_inert":
        return (
            "\n\nYou are given a shared reference: identity-only entries, each with "
            "an id, definition, and canonical example. The LIVE model's concepts "
            "carry their reference binding ('ref'); the inert model's do not. Decide "
            "which reference entry each inert concept denotes by matching it to the "
            "entry whose definition and example fit, then correspond concepts that "
            "map to the same entry. This is how the published reference supplies the "
            "meaning the inert side cannot volunteer, and how it pre-empts false "
            "cognates: two labels that look alike but fit different entries do not "
            "correspond."
        )
    return (
        "\n\nYou are also given a shared reference: identity-only entries, each with "
        "an id, definition, and canonical example. Every concept has been bound to a "
        "reference entry (its 'ref'). Two concepts correspond exactly when they are "
        "bound to the SAME reference entry. Use this to settle correspondences "
        "directly and to pre-empt false cognates: two concepts whose labels look "
        "alike but are bound to different entries do NOT correspond."
    )


def _independent_reference_note(placement: str) -> str:
    """The reference note when the two sides carry INDEPENDENTLY authored lexicons
    (reference.kind == 'independent'). There is no shared id to key on; the agent must
    align the two lexicons by meaning -- the harder problem the drafts identify."""
    return (
        "\n\nEach side is bound to its OWN lexicon, and the two lexicons were authored "
        "independently: they use different entry ids and often different terms for the "
        "same concept. A concept's 'ref' names an entry in ITS side's lexicon only. Do "
        "NOT expect ids to match across sides, and do NOT correspond two concepts merely "
        "because their labels look alike. Instead ALIGN the two lexicons: read the entries' "
        "definitions and examples, decide which entry on side A denotes the same thing as "
        "which entry on side B, and correspond the concepts bound to a matched pair of "
        "entries. Beware cross-lexicon false cognates -- an entry whose term resembles a "
        "different-meaning entry in the other lexicon (for example a broadcast-domain "
        "'segment' versus an Ethernet Segment). Leave a concept in the residual when its "
        "meaning has no counterpart in the other lexicon."
    )


def _concept_line(c, *, inert: bool, include_ref: bool, concept_fields=None) -> dict:
    # concept_fields (the pre-lift baseline): when set, serialise each concept from an
    # explicit content mask instead of the inert/live default. The always-present base is
    # the lexical surface (label + synonyms); the lift's knowledge is added by factor:
    # 'class' (kind), 'explanation' (gloss + example), 'structure' (relations), 'instances'.
    if concept_fields is not None:
        d = {"id": c.id, "label": c.label, "synonyms": list(c.synonyms)}
        if "class" in concept_fields:
            d["kind"] = c.kind
        if "explanation" in concept_fields:
            d["gloss"] = c.gloss
            d["example"] = c.example
        if "structure" in concept_fields:
            d["relations"] = list(c.relations)
        if "instances" in concept_fields:
            d["instances"] = list(c.instances)
        if include_ref:
            d["ref"] = c.ref
        return d
    if inert:
        # inert: no self-explanation, but the structure and data are still present
        return {"id": c.id, "label": c.label, "kind": c.kind,
                "relations": list(c.relations), "instances": list(c.instances)}
    d = {"id": c.id, "label": c.label, "kind": c.kind,
         "synonyms": list(c.synonyms), "gloss": c.gloss, "example": c.example}
    if include_ref:
        d["ref"] = c.ref
    return d


def _inert_sides(placement: str, inert_side: str) -> tuple[bool, bool]:
    if placement == "both_cognitive":
        return False, False
    if placement == "one_inert":
        return (inert_side == "a"), (inert_side == "b")
    if placement == "both_inert":
        return True, True
    raise NotImplementedError(
        f"placement '{placement}' is not implemented by the agent stack "
        "(both_cognitive, one_inert, both_inert are)."
    )


# The five descriptive fields of a reference entry, for the ablation study. The id is
# always present (it is the coreference anchor, not descriptive evidence). ref_fields=None
# reproduces the original three-field payload the main study used.
REF_FIELDS_ALL = frozenset({"label", "synonyms", "class", "definition", "example"})


def _ref_entry(e, ref_fields) -> dict:
    """Serialise one reference entry, keeping id plus whichever descriptive fields the
    ablation mask admits."""
    d = {"id": e.id}
    if "label" in ref_fields:
        d["label"] = e.label
    if "synonyms" in ref_fields:
        d["synonyms"] = list(e.synonyms)
    if "class" in ref_fields:
        d["class"] = e.cls
    if "definition" in ref_fields:
        d["definition"] = e.definition
    if "example" in ref_fields:
        d["example"] = e.example
    return d


def _prompt(a, b, reference, use_reference, placement, inert_side,
            ref_fields=None, concept_fields=None) -> str:
    a_inert, b_inert = _inert_sides(placement, inert_side)
    payload = {
        "model_A": {"system": a.system, "dialect": a.dialect,
                    "inert": a_inert,
                    "concepts": [_concept_line(c, inert=a_inert, include_ref=use_reference,
                                               concept_fields=concept_fields)
                                 for c in a.concepts]},
        "model_B": {"system": b.system, "dialect": b.dialect,
                    "inert": b_inert,
                    "concepts": [_concept_line(c, inert=b_inert, include_ref=use_reference,
                                               concept_fields=concept_fields)
                                 for c in b.concepts]},
    }
    if use_reference and reference is not None:
        if ref_fields is None:
            # legacy payload: exactly what the main study fed, for reproducibility
            payload["reference"] = [
                {"id": e.id, "definition": e.definition, "example": e.example} for e in reference.entries
            ]
        else:
            payload["reference"] = [_ref_entry(e, ref_fields) for e in reference.entries]
    return json.dumps(payload, indent=2)


class _Correspondence(BaseModel):
    a_id: str
    b_id: str
    confidence: float
    rationale: str


class ReconcileResult(BaseModel):
    correspondences: list[_Correspondence]
    residual_a: list[str]
    residual_b: list[str]


VERIFY_SYSTEM = (
    "You are VERIFYING a proposed reconciliation of two semantic models of one "
    "network. For each proposed correspondence (concept a in model A said to denote "
    "the same thing as concept b in model B), decide whether it holds up under the "
    "semantic invariants a correct translation must preserve: endpoint identity, "
    "connectivity, capacity, layer relationships, switching constraints, and "
    "multiplexing structure. Verify by a worked round-trip: would a translation built "
    "on this correspondence preserve those invariants? Use the structural relations "
    "and the concrete instances to judge the roles the two concepts play. A "
    "correspondence PASSES only if the two concepts play the SAME role; if they merely "
    "share a label but their relations and instances show different roles (a link end "
    "versus a trail head, an optical signal grade versus a commercial service class, a "
    "protection group versus a protection role), it FAILS. You are checking "
    "consistency, not consulting an answer key. Return a verdict for every proposed "
    "correspondence."
)

VERIFY_NOTE = {
    "both_cognitive": "\n\nBoth systems are live and can be interrogated: you may treat "
                      "a correspondence as confirmed when both sides' detail agrees.",
    "one_inert": "\n\nOne system is inert and cannot confirm; verify by round-trip on the "
                 "invariants using both sides' structure and instances.",
    "both_inert": "\n\nNeither system can confirm; you can only check structural "
                  "consistency against the invariants from the relations and instances.",
}


class _Verdict(BaseModel):
    a_id: str
    b_id: str
    passes: bool
    reason: str


class VerifyResult(BaseModel):
    verdicts: list[_Verdict]


class OpenAIAgentStack(ReasoningStack):
    """A cognitive agent stack. Construct one per condition: use_reference on/off."""

    def __init__(self, use_reference: bool = False, model: str | None = None,
                 inert_side: str = "b", client=None, ref_fields=None, concept_fields=None):
        self.uses_reference = use_reference
        self.model = model or DEFAULT_MODEL
        self.inert_side = inert_side
        # ref_fields: None reproduces the main study's payload; a set of descriptive
        # field names (subset of REF_FIELDS_ALL) selects the reference-ablation mask.
        self.ref_fields = ref_fields
        # concept_fields: None reproduces the default concept serialisation; a set of
        # factor names selects the pre-lift content mask ('class','explanation',
        # 'structure','instances'; label+synonyms always present).
        self.concept_fields = concept_fields
        self.name = f"openai-agent({'ref' if use_reference else 'no-ref'})"
        self._client = client  # injectable for testing

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # imported lazily so the skeleton stays dependency-free
            self._client = OpenAI()
        return self._client

    def infer(self, a, b, reference, placement) -> tuple[ReconcileResult, dict]:
        """Call the model once; return the parsed result and an effort record."""
        client = self._get_client()
        system = SYSTEM + _placement_note(placement)
        if self.uses_reference:
            if reference is not None and getattr(reference, "kind", "") == "independent":
                note = _independent_reference_note(placement)
            else:
                note = _reference_note(placement)
            if self.ref_fields is not None:
                # keep the note honest under ablation: it must not promise fields the
                # mask has removed
                note = (note
                        .replace("each with an id, definition, and canonical example",
                                 "each with an id and the descriptive fields provided")
                        .replace("whose definition and example fit", "whose provided fields fit"))
            system += note
        user = _prompt(a, b, reference, self.uses_reference, placement, self.inert_side,
                       self.ref_fields, self.concept_fields)
        t0 = time.time()
        completion = client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format=ReconcileResult,
        )
        elapsed = time.time() - t0
        result = completion.choices[0].message.parsed
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        effort = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            "latency_s": round(elapsed, 2),
            "model": self.model,
        }
        return result, effort

    def to_reconciliation(self, result, a, b, effort, placement) -> Reconciliation:
        a_ids, b_ids = a.by_id, b.by_id
        proposed: list[frozenset] = []
        confidence: dict = {}
        matched_a: set[str] = set()
        matched_b: set[str] = set()
        for c in result.correspondences:
            if c.a_id in a_ids and c.b_id in b_ids:  # drop any hallucinated ids
                pair = frozenset((c.a_id, c.b_id))
                proposed.append(pair)
                confidence[pair] = float(getattr(c, "confidence", 0.0) or 0.0)
                matched_a.add(c.a_id)
                matched_b.add(c.b_id)
        residual_a = [c.id for c in a.concepts if c.id not in matched_a]
        residual_b = [c.id for c in b.concepts if c.id not in matched_b]
        binding_ops = (sum(1 for c in a.concepts if c.ref) + sum(1 for c in b.concepts if c.ref)) \
            if self.uses_reference else 0
        work = {"candidates": len(proposed), "bilateral_checks": 0, "binding_ops": binding_ops}
        return Reconciliation(
            stack=self.name, uses_reference=self.uses_reference, placement=placement,
            proposed=proposed, residual_a=residual_a, residual_b=residual_b,
            work=work, effort=effort, confidence=confidence,
        )

    def reconcile(self, a, b, reference: Reference | None = None,
                  placement: str = "both_cognitive") -> Reconciliation:
        _inert_sides(placement, self.inert_side)  # validates placement
        result, effort = self.infer(a, b, reference, placement)
        return self.to_reconciliation(result, a, b, effort, placement)

    def _split_pair(self, pr, a_by, b_by):
        ids = list(pr)
        aid = next((i for i in ids if i in a_by), None)
        bid = next((i for i in ids if i in b_by), None)
        return aid, bid

    def verify(self, a, b, reference, placement, proposed):
        """Verify a proposed reconciliation by round-trip on the invariants, using the
        placement-appropriate evidence (no gold access). Returns the correspondences
        that survive verification and an effort record."""
        client = self._get_client()
        a_by, b_by = a.by_id, b.by_id
        payload = json.loads(_prompt(a, b, reference, self.uses_reference, placement,
                                     self.inert_side, self.ref_fields, self.concept_fields))
        pairs = []
        for pr in proposed:
            aid, bid = self._split_pair(pr, a_by, b_by)
            if aid and bid:
                pairs.append({"a_id": aid, "a_label": a_by[aid].label,
                              "b_id": bid, "b_label": b_by[bid].label})
        payload["proposed_correspondences"] = pairs
        payload["invariants"] = ["endpoint-identity", "connectivity", "capacity",
                                 "layer-relationships", "switching-constraints", "multiplexing"]
        system = VERIFY_SYSTEM + VERIFY_NOTE.get(placement, "")
        t0 = time.time()
        completion = client.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps(payload, indent=2)}],
            response_format=VerifyResult,
        )
        elapsed = time.time() - t0
        result = completion.choices[0].message.parsed
        passed = {}
        for v in result.verdicts:
            passed[(v.a_id, v.b_id)] = v.passes
            passed[(v.b_id, v.a_id)] = v.passes
        kept = []
        for pr in proposed:
            aid, bid = self._split_pair(pr, a_by, b_by)
            if passed.get((aid, bid), True):  # keep if the verifier did not judge it
                kept.append(pr)
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        effort = {
            "verify_total_tokens": getattr(usage, "total_tokens", None),
            "verify_reasoning_tokens": getattr(details, "reasoning_tokens", None),
            "verify_latency_s": round(elapsed, 2),
        }
        return kept, effort

    def reconcile_and_verify(self, a, b, reference=None, placement="both_cognitive"):
        """Run reconcile, then verify-and-repair. Returns (pre, post) Reconciliations;
        post keeps only the correspondences that survived verification."""
        pre = self.reconcile(a, b, reference=reference, placement=placement)
        kept, veffort = self.verify(a, b, reference, placement, pre.proposed)
        a_by, b_by = a.by_id, b.by_id
        matched_a = {self._split_pair(p, a_by, b_by)[0] for p in kept}
        matched_b = {self._split_pair(p, a_by, b_by)[1] for p in kept}
        post = Reconciliation(
            stack=self.name, uses_reference=self.uses_reference, placement=placement,
            proposed=list(kept),
            residual_a=[c.id for c in a.concepts if c.id not in matched_a],
            residual_b=[c.id for c in b.concepts if c.id not in matched_b],
            work=dict(pre.work), effort={**pre.effort, **veffort},
            confidence={p: pre.confidence.get(p, 0.0) for p in kept},
        )
        return pre, post

"""Semantic models and cases: plain dataclasses over the benchmark JSON.

A semantic model is an ontology (concepts and relations) named by a lexicon,
carried with pragmatics and provenance. For the walking skeleton a concept
carries its lexical surface (label, synonyms), a shallow class (kind), a
disambiguating gloss and example, and the reference entry its author bound it to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Concept:
    id: str
    label: str
    kind: str
    gloss: str = ""
    example: str = ""
    synonyms: tuple[str, ...] = ()
    ref: str | None = None
    relations: tuple[dict, ...] = ()   # structural edges: {"rel": ..., "target": <concept id>}
    instances: tuple[str, ...] = ()    # concrete data the concept is realised by

    @property
    def surface_tokens(self) -> set[str]:
        """Tokens a reference-blind matcher may key on: label + synonyms."""
        text = " ".join([self.label, *self.synonyms]).lower()
        return {t for t in text.replace("/", " ").replace("_", " ").replace("-", " ").split() if t}


@dataclass
class SemanticModel:
    system: str
    dialect: str
    modules: tuple[str, ...]
    concepts: list[Concept]

    @property
    def by_id(self) -> dict[str, Concept]:
        return {c.id: c for c in self.concepts}

    @classmethod
    def from_json(cls, path: str | Path) -> "SemanticModel":
        d = json.loads(Path(path).read_text())
        concepts = [
            Concept(
                id=c["id"],
                label=c["label"],
                kind=c.get("kind", ""),
                gloss=c.get("gloss", ""),
                example=c.get("example", ""),
                synonyms=tuple(c.get("synonyms", [])),
                ref=c.get("ref"),
                relations=tuple(c.get("relations", [])),
                instances=tuple(c.get("instances", [])),
            )
            for c in d["concepts"]
        ]
        return cls(
            system=d.get("system", ""),
            dialect=d.get("dialect", ""),
            modules=tuple(d.get("modules", [])),
            concepts=concepts,
        )

    def to_dict(self, note: str | None = None) -> dict:
        """Serialise back to the benchmark's lifted-model JSON shape (same field order as the
        fixtures), so an agent-produced lift can be written out and diffed concept-for-concept
        against the fixture it stands in for. Round-trips with from_json."""
        d: dict = {"system": self.system, "dialect": self.dialect, "modules": list(self.modules)}
        if note is not None:
            d["note"] = note
        d["concepts"] = [
            {
                "id": c.id,
                "label": c.label,
                "synonyms": list(c.synonyms),
                "kind": c.kind,
                "gloss": c.gloss,
                "example": c.example,
                "ref": c.ref,
                "relations": [dict(r) for r in c.relations],
                "instances": list(c.instances),
            }
            for c in self.concepts
        ]
        return d


@dataclass
class Gold:
    """The gold-standard reconciliation for a case."""

    correspondences: list[dict]
    false_cognates: list[dict]
    residual: dict
    residual_by_placement: dict
    invariants: list[str]
    verification_by_placement: dict

    @property
    def correct_pairs(self) -> set[frozenset]:
        return {frozenset((c["a"], c["b"])) for c in self.correspondences}

    @property
    def false_cognate_pairs(self) -> set[frozenset]:
        return {frozenset((c["a"], c["b"])) for c in self.false_cognates}

    @classmethod
    def from_json(cls, path: str | Path) -> "Gold":
        d = json.loads(Path(path).read_text())
        return cls(
            correspondences=d["correspondences"],
            false_cognates=d.get("false_cognates", []),
            residual=d.get("residual", {}),
            residual_by_placement=d.get("residual_by_placement", {}),
            invariants=d.get("invariants", []),
            verification_by_placement=d.get("verification_by_placement", {}),
        )


@dataclass
class Case:
    name: str
    model_a: SemanticModel
    model_b: SemanticModel
    reference: "Reference"
    gold: Gold

    @classmethod
    def load(cls, case_dir: str | Path) -> "Case":
        from reconcile.reference import Reference  # local import to avoid cycle

        p = Path(case_dir)
        model_files = sorted(p.glob("model_a*.json")) + sorted(p.glob("model_b*.json"))
        a = SemanticModel.from_json(model_files[0])
        b = SemanticModel.from_json(model_files[1])
        return cls(
            name=p.name,
            model_a=a,
            model_b=b,
            reference=Reference.from_json(p / "reference.json"),
            gold=Gold.from_json(p / "gold.json"),
        )

"""Analogy search (a generator; "Reference Network, Data Sets and Knowledge Graph" §6.2, Table 4).

Takes the case frame's role form, retrieves the most similar past cases from the experience store, and
proposes their recorded causes, carried onto the current network by the grounding tool. Each retrieved case
counts in proportion to its similarity and its verification (repair-confirmed 1.0, engineer-concluded 0.7,
agent-concluded 0.3; refuted by a later finding 0). Cases closed as "no fault found", and cases whose recorded
cause cannot be grounded, add to the residual. The store must be the one as it stood on the question's day
(ExperienceStore.as_of), with only the closures and follow-up findings recorded by then. The tool names no fault mode.
"""
from __future__ import annotations

from collections import defaultdict

from ..schemas import Candidate, GeneratorPayload, ScenarioFrame
from .experience import ExperienceStore, signature
from .grounding import GroundingTool
from .walks import single


def frame_signature(frame: ScenarioFrame, kg):
    alarms = [(s.observable, s.element) for s in frame.symptoms if s.kind == "alarm" and s.detail != "not_alarmed"]
    n_down = sum(1 for s in frame.services if s.state == "down")
    n_hit = sum(1 for s in frame.symptoms if s.kind == "event")
    rx = {s.element for s in frame.symptoms if s.kind == "change" and kg.cls(s.element) == "LineInterface"}
    return signature(kg, alarms, n_down, n_hit, len(rx))


class AnalogySearch:
    name, version = "analogy", "0.1"

    def __init__(self, kg, store: ExperienceStore, grounding: GroundingTool, k: int = 20):
        self.kg, self.store, self.grounding, self.k = kg, store, grounding, k
        self.trace: dict = {}

    def run(self, frame: ScenarioFrame) -> GeneratorPayload:
        sig = frame_signature(frame, self.kg)
        hits = self.store.retrieve(sig, self.k)
        if not hits:
            self.trace = {"retrieved": 0}
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        abstract = defaultdict(float)
        unusable = 0.0
        total = 0.0
        for sim, c in hits:
            w = sim * c.weight
            total += w
            if c.outcome == "no_fault_found" or not c.mode or not c.relation:
                unusable += w
                continue
            abstract[(c.mode, c.relation)] += w
        grounded = defaultdict(float)
        for (mode, rel), w in abstract.items():
            locs = self.grounding.ground(frame, mode, relation=rel)
            if not locs:
                unusable += w
                continue
            for loc in locs:
                grounded[(mode, loc)] += w / len(locs)
        self.trace = {"retrieved": len(hits), "best_similarity": round(hits[0][0], 3),
                      "cases": [(c.id, round(s, 3), c.mode, c.relation) for s, c in hits[:5]],
                      "ungrounded_share": round(unusable / total, 3) if total else 1.0}
        if not grounded:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        residual = min(0.9, 0.05 + unusable / total) if total else 1.0
        z = sum(grounded.values())
        rows = sorted(grounded.items(), key=lambda x: (-x[1], x[0]))[:25]
        z = sum(w for _, w in rows)
        return GeneratorPayload(candidates=[Candidate(hypothesis=single(m, loc), prior_mass=(1 - residual) * w / z,
                                                      rationale="analogy") for (m, loc), w in rows],
                                residual_mass=residual)

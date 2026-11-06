"""Instrumented trace of one case under the frozen code (wrappers only; no change to src).

Records what the run records do not keep: the frame as built (question, onset, symptoms by kind and observable,
services, shared elements, quiet instruments, maintenance, scope), each generation line's proposal (candidates
with their prior mass, residual, weight), the pooled priors and the posterior after each step, and every likelihood
term per hypothesis (log-likelihood in nats). For the paper's worked example and the demonstrator.
Writes results/campaign/c4_trace_full/s<seed>.json. No API calls.

Run from the project root:   python scripts/trace_full.py --arm A-full-cd --seeds 2021,2039,2002
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "results" / "campaign" / "c4_trace_full"
EV: list = []


def _top(d: dict, n: int = 60):
    return [(k, round(v, 4)) for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]


def install():
    from adagent import ledger as L
    from adagent.tools import frame as F
    from adagent.tools import loop as LP

    ob = F.DeterministicFramer.build

    def build(self, question):
        fr = ob(self, question)
        by = collections.Counter((s.kind, s.observable) for s in fr.symptoms)
        EV.append({"ev": "frame", "widen": self.widen, "question": fr.question, "kind": fr.question_kind,
                   "anchors": fr.anchors, "reported_at": fr.reported_at, "onset": fr.onset_estimate,
                   "window": fr.window, "symptoms": len(fr.symptoms),
                   "symptoms_by_kind": dict(collections.Counter(s.kind for s in fr.symptoms)),
                   "symptoms_by_observable": [[k, o, n] for (k, o), n in by.most_common()],
                   "services": [s.model_dump() for s in fr.services][:40],
                   "affected_elements": len(fr.affected_elements), "shared_elements": fr.shared_elements,
                   "quiet_instruments": len(fr.quiet_instruments), "quiet_streams": len(fr.quiet_streams),
                   "silent_streams": len(fr.silent_streams), "unknown_sources": len(fr.unknown_sources),
                   "maintenance": fr.maintenance, "scope": len(fr.scope)})
        return fr
    F.DeterministicFramer.build = build

    oag = L.Ledger.add_generator

    def add_generator(self, tool, payload, weight=None):
        keys = oag(self, tool, payload, weight)
        tot = sum(c.prior_mass for c in payload.candidates) or 1.0
        EV.append({"ev": "line", "tool": tool, "weight": weight,
                   "candidates": [[k, round(c.prior_mass, 4), (c.rationale or "")[:160]]
                                  for k, c in sorted(zip(keys, payload.candidates), key=lambda kc: -kc[1].prior_mass)[:15]],
                   "n": len(payload.candidates), "residual": round(payload.residual_mass, 4), "mass": round(tot, 4),
                   "pooled_after": _top(self.priors())})
        return keys
    L.Ledger.add_generator = add_generator

    def wrap_term(name):
        orig = getattr(L.Ledger, name)

        def f(self, term_id, *a, **k):
            r = orig(self, term_id, *a, **k)
            payload = a[1] if name == "add_likelihood" else a[0]
            vals = {h: (round(v.log_likelihood, 3) if v.log_likelihood is not None else None)
                    for h, v in payload.values.items()}
            EV.append({"ev": "term", "op": name, "term": term_id,
                       "values": sorted(([h, v] for h, v in vals.items() if v is not None), key=lambda x: -x[1])[:60],
                       "declined": sum(1 for v in vals.values() if v is None),
                       "background": payload.background_log_likelihood,
                       "posterior_after": _top(self.posterior())})
            return r
        setattr(L.Ledger, name, f)
    wrap_term("add_likelihood")
    wrap_term("fill_term")

    osnap = LP.WinnowingLoop._snapshot

    def snap(self, label):
        osnap(self, label)
        EV.append({"ev": "snapshot", "label": label, "priors": _top(self.ledger.priors()),
                   "posterior": _top(self.ledger.posterior())})
    LP.WinnowingLoop._snapshot = snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="A-full-cd")
    ap.add_argument("--seeds", required=True)
    a = ap.parse_args()
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    install()
    from adagent.harness import exp2
    OUT.mkdir(parents=True, exist_ok=True)
    for s in [int(x) for x in a.seeds.split(",")]:
        EV.clear()
        row = exp2.run_job(a.arm, 2190.0, s)
        row["trace"] = list(EV)
        (OUT / f"s{s}.json").write_text(json.dumps(row, default=str))
        print(s, row["score"]["outcome"], len(EV), "events", flush=True)


if __name__ == "__main__":
    main()

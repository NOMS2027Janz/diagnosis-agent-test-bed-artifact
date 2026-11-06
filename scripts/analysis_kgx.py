"""The knowledge graph degraded: each degradation against the same arm with the true graph, on the same cases.

The agent (A-full-cd-kg-*) against A-full-cd (results/x3/exp2b); manual troubleshooting (B-kg-*) against B-kg-none,
the undegraded control through the same wrapper (results/campaign/kgx). Writes results/campaign/kgx.json and prints
right, partial, wrong, "I don't know", effort and visits, by kind of anomaly, and the wrong commitments that name the
right element with a wrong fault mode. Run from the repository root:   python scripts/analysis_kgx.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KGX = ROOT / "results" / "campaign" / "kgx"
BASE = {"A-full-cd": ROOT / "results" / "x3" / "exp2b" / "A-full-cd", "B": KGX / "B-kg-none"}
KINDS = ("inst", "flat", "sig", "modes")


def load(d: Path) -> dict:
    fs = [f for f in d.glob("s*.json") if f.stem[1:].isdigit()] if d.exists() else []   # not s<seed>.error.json
    return {j["seed"]: j for j in (json.loads(f.read_text()) for f in fs)}


def remote_h(row: dict) -> float:
    """Hours of remote checks (no site visited); the rest of the effort is field checks and travel."""
    ch = (row["baseline"].get("checks") or []) if "baseline" in row else (row.get("loop") or {}).get("checks", [])
    return sum(c.get("minutes") or MINUTES.get(c["check"], 0) for c in ch
               if "check" in c and not c.get("dispatch_sites") and MINUTES_CLASS.get(c["check"]) != "dispatch") / 60.0


import yaml   # noqa: E402
_CAT = yaml.safe_load((ROOT / "knowledge" / "practice" / "checks.yaml").read_text())
_LIST = next(v for v in _CAT.values() if isinstance(v, list))
MINUTES = {c["id"]: c["minutes"] for c in _LIST}
MINUTES_CLASS = {c["id"]: c["class"] for c in _LIST}


def cat(o: str) -> str:
    if o.startswith("commit_correct"):
        return "right"
    if o == "coarse_correct":
        return "coarse"
    if "partial" in o:
        return "partial"
    if "wrong" in o:
        return "wrong"
    return "no_procedure" if o == "no_procedure" else "dont_know"


def kind_of(case: str) -> str:
    return "common_cause" if case.endswith("common_cause") else case.split("-")[-1]


def summary(rows: dict, seeds: list) -> dict:
    c = Counter(cat(rows[s]["score"]["outcome"]) for s in seeds)
    by = Counter(kind_of(rows[s]["case"]) for s in seeds if cat(rows[s]["score"]["outcome"]) == "right")
    return {"cases": len(seeds), **{k: c.get(k, 0) for k in ("right", "partial", "wrong", "dont_know", "no_procedure")},
            "effort_h": round(sum(rows[s]["score"]["effort_h"] for s in seeds), 1),
            "remote_h": round(sum(remote_h(rows[s]) for s in seeds), 1),
            "field_h": round(sum(rows[s]["score"]["effort_h"] - remote_h(rows[s]) for s in seeds), 1),
            "site_visits": sum(rows[s]["score"]["site_visits"] for s in seeds), "right_by_kind": dict(sorted(by.items()))}


def wrong_place_right(row: dict) -> bool:
    """A wrong commitment at a true fault's element (the fault mode is wrong, the place is right)."""
    if cat(row["score"]["outcome"]) != "wrong":
        return False
    hyp = (row.get("loop", {}).get("decision") or {}).get("hypothesis") or ""
    return any(t in hyp for f in row["truth"] for t in f["targets"])


def main() -> None:
    out = {}
    for base, bdir in BASE.items():
        b = load(bdir)
        for k in KINDS:
            arm = f"{base}-kg-{k}"
            rows = load(KGX / arm)
            seeds = sorted(s for s in rows if s in b)
            if not seeds:
                continue
            gained = [s for s in seeds if cat(rows[s]["score"]["outcome"]) == "right" and cat(b[s]["score"]["outcome"]) != "right"]
            lost = [s for s in seeds if cat(b[s]["score"]["outcome"]) == "right" and cat(rows[s]["score"]["outcome"]) != "right"]
            wr = [s for s in seeds if cat(rows[s]["score"]["outcome"]) == "wrong"]
            out[arm] = {"degradation": rows[seeds[0]].get("kgx"), "without": summary(b, seeds), "with": summary(rows, seeds),
                        "gained": gained, "lost": lost, "wrong": wr,
                        "wrong_right_place": [s for s in wr if wrong_place_right(rows[s])]}
            w, x = out[arm]["without"], out[arm]["with"]
            print(f"{arm:20s} {len(seeds):4d}  right {w['right']:3d} -> {x['right']:3d}  wrong {w['wrong']:2d} -> {x['wrong']:2d} "
                  f"(right place {len(out[arm]['wrong_right_place'])})  I don't know {w['dont_know']:3d} -> {x['dont_know']:3d}  "
                  f"effort {w['effort_h']:6.1f} -> {x['effort_h']:6.1f} h (field {w['field_h']:.1f} -> {x['field_h']:.1f}, remote {w['remote_h']:.1f} -> {x['remote_h']:.1f})  visits {w['site_visits']:2d} -> {x['site_visits']:2d}")
    (ROOT / "results" / "campaign" / "kgx.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

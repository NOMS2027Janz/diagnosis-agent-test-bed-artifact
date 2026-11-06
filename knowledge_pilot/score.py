"""Pilot of an LLM-built graph, step 5: scoring the reconciliations against the gold standard (gold.py).

For each lift (v1: out/lifted, v2: out/lifted_v2), pair (VA-op, VB-op, VA-VB) and reference condition (none,
constructed, given):
  proposed            correspondences proposed
  correct / wrong     proposed pairs that are / are not gold pairs
  precision           correct / proposed
  forced              wrong pairs whose vendor concept has no counterpart at all (a one-to-zero forced into a match)
  confident errors    wrong pairs proposed with confidence at least 0.8 (the study's CONFIDENT_TAU)
  concept recall      of the vendor concepts that have a counterpart, the share given at least one correct pair
  meaning recall      of the meanings both sides share (operator concepts, or alarms between the vendors), the share
                      linked by at least one correct pair
Lift quality, per vendor: concepts, concepts with a counterpart, and mixed concepts (wordings of disjoint meanings
merged into one concept). Concept-level rather than pair-level recall, because the lifts keep several concepts for one
meaning (the gold pairs then multiply while one correct link per concept is what a user needs).

    python knowledge_pilot/score.py            # writes knowledge_pilot/out/scores.json and prints the table
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gold import GOLD  # noqa: E402

OUT = HERE / "out"
TAU = 0.8
LIFTS = {"v1": "", "v2": "_v2"}


def concept_targets(tag: str, vendor: str) -> tuple[dict, dict]:
    # a concept's wordings are its label and synonyms as the lifted model gives them (a concept whose label repeats
    # a wording another concept also claims keeps it: the reconciliation saw the label)
    w = json.loads((OUT / f"lifted{tag}" / f"{vendor}.wordings.json").read_text())
    model = json.loads((OUT / f"lifted{tag}" / f"{vendor}.json").read_text())
    ids = [c["id"] for c in model["concepts"]]
    by_concept = defaultdict(list)
    for c in model["concepts"]:
        for word in [c.get("label")] + list(c.get("synonyms") or []):
            if word and word not in by_concept[c["id"]]:
                by_concept[c["id"]].append(word)
    for word, cid in w["by_wording"].items():
        if word not in by_concept[cid]:
            by_concept[cid].append(word)
    tg = {cid: set().union(*[set(GOLD[vendor].get(x, [])) for x in by_concept.get(cid, [])]) if by_concept.get(cid)
          else set() for cid in ids}
    mixed = []
    for cid in ids:
        sets = [frozenset(GOLD[vendor].get(x, [])) for x in by_concept.get(cid, []) if GOLD[vendor].get(x)]
        if any(not (a & b) for i, a in enumerate(sets) for b in sets[i + 1:]):
            mixed.append(cid)
    return tg, {"concepts": len(ids), "with_counterpart": sum(1 for t in tg.values() if t), "mixed": mixed,
                "words": dict(by_concept)}


def op_targets() -> dict:
    from targets import concepts
    return {f"op.{c['id']}": {c["id"]} for cl in concepts().values() for c in cl}


def unmatched_words(tag: str, vendor: str) -> list:
    """Labels or synonyms in the lifted model that are not wordings of the inventory (the lift renamed them)."""
    model = json.loads((OUT / f"lifted{tag}" / f"{vendor}.json").read_text())
    return [(c["id"], w) for c in model["concepts"] for w in [c.get("label")] + list(c.get("synonyms") or [])
            if w and w not in GOLD[vendor]]


def score(tag: str, pair: str, cond: str, T: dict) -> dict:
    f = OUT / f"reconciled{tag}" / f"{pair}.{cond}.json"
    r = json.loads(f.read_text())
    if r.get("error"):
        return {"error": r["error"]}
    x, y = pair.split("-")
    A, B = T[x], T[y]
    ok = lambda a, b: bool(A.get(a, set()) & B.get(b, set()))
    props = [(c["a_id"], c["b_id"], float(c.get("confidence") or 0)) for c in r["correspondences"]]
    seen, rows = set(), []
    for a, b, cf in props:
        if (a, b) in seen:
            continue
        seen.add((a, b))
        rows.append((a, b, cf, ok(a, b)))
    correct = [p for p in rows if p[3]]
    wrong = [p for p in rows if not p[3]]
    unknown = [p for p in rows if p[0] not in A or p[1] not in B]
    # concept recall: vendor-side concepts (A side; both sides for VA-VB) that have a counterpart on the other side
    def has_cp(S, O, k):
        return bool(S.get(k)) and any(S[k] & t for t in O.values())
    forced = [p for p in wrong if not has_cp(A, B, p[0]) or not has_cp(B, A, p[1])]
    a_need = [k for k in A if has_cp(A, B, k)]
    a_got = {p[0] for p in correct}
    rec_a = sum(1 for k in a_need if k in a_got) / len(a_need) if a_need else 0.0
    out = {"proposed": len(rows), "correct": len(correct), "wrong": len(wrong),
           "precision": round(len(correct) / len(rows), 3) if rows else 0.0,
           "forced": len(forced), "confident_errors": sum(1 for p in wrong if p[2] >= TAU),
           "concept_recall_a": round(rec_a, 3), "unknown_ids": len(unknown)}
    if y != "op":
        b_need = [k for k in B if has_cp(B, A, k)]
        b_got = {p[1] for p in correct}
        out["concept_recall_b"] = round(sum(1 for k in b_need if k in b_got) / len(b_need), 3) if b_need else 0.0
    shared = set().union(*A.values()) & set().union(*B.values())
    linked = set()
    for a, b, _, good in correct:
        linked |= A[a] & B[b]
    out["meaning_recall"] = round(len(linked & shared) / len(shared), 3) if shared else 0.0
    out["shared_meanings"] = len(shared)
    out["wrong_examples"] = [(a, b, cf) for a, b, cf, _ in wrong]
    out["effort_tokens"] = sum(v for k, v in (r.get("effort") or {}).items() if k.endswith("total_tokens"))
    return out


def main():
    res = {}
    for lift, tag in LIFTS.items():
        T = {"op": op_targets()}
        lift_q = {}
        for v in ("VA", "VB"):
            T[v], q = concept_targets(tag, v)
            lift_q[v] = {k: (len(val) if k == "mixed" else val) for k, val in q.items() if k != "words"}
            lift_q[v]["mixed_ids"] = q["mixed"]
        res[lift] = {"lift": lift_q}
        for pair in ("VA-op", "VB-op", "VA-VB"):
            for cond in ("none", "constructed", "given"):
                res[lift][f"{pair}.{cond}"] = score(tag, pair, cond, T)
    (OUT / "scores.json").write_text(json.dumps(res, indent=1, default=list))
    for lift in res:
        print(f"\n== {lift}  lift: " + "; ".join(f"{v} {q['concepts']} concepts, {q['with_counterpart']} with a "
                                                 f"counterpart, {q['mixed']} mixed" for v, q in res[lift]["lift"].items()))
        print(f"{'pair.condition':22} prop  corr wrong  prec forced conf-err  c-rec(a) c-rec(b) m-rec  tokens")
        for k, s in res[lift].items():
            if k == "lift":
                continue
            if "error" in s:
                print(k, "ERROR", s["error"][:80]); continue
            print(f"{k:22} {s['proposed']:4} {s['correct']:5} {s['wrong']:5} {s['precision']:5.2f} {s['forced']:6} "
                  f"{s['confident_errors']:8} {s['concept_recall_a']:9.2f} {s.get('concept_recall_b', float('nan')):8.2f} "
                  f"{s['meaning_recall']:5.2f} {s['effort_tokens']:7}")


if __name__ == "__main__":
    main()

"""Experiment 1 (generation): analysis, after every run and the equivalence classes are in
(spec/PROTOCOL_EXP1_GENERATION.md §5 and §7).

Run from the project root:  python scripts/exp1_analysis.py
Reads results/exp1/offline.jsonl, every LLM arm file (L0_r*.jsonl, L1_r1.jsonl, L3_r1.jsonl) and
results/exp1/equivalence/; makes arm L2 by re-grounding L0 repeat 1 with the documentation-gap weight; builds the
pooled priors; and writes results/exp1/analysis.json and results/exp1/ANALYSIS.md (tables of every measure by line
and day, with bootstrap 95% intervals; paired differences; per family and per mode; frequency-reweighted; cost;
stability across L0 repeats; calibration of the LLM's stated probabilities).
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from adagent.harness.cases import CASE_MODES, context, make_case              # noqa: E402
from adagent.harness.metrics import expected_hit, expected_hit_any           # noqa: E402
from adagent.ledger import Ledger                                            # noqa: E402
from adagent.schemas import GeneratorPayload, Candidate                      # noqa: E402
from adagent.tools.frame import DeterministicFramer                         # noqa: E402
from adagent.tools.grounding import GroundingTool                           # noqa: E402
from adagent.tools.proposer_llm import LLMProposer                          # noqa: E402
from adagent.tools.walks import single                                       # noqa: E402
from exp1_llm import OUT                                                     # noqa: E402

W_DOC_GAP = 0.3                 # decision D6
UNLOCALISABLE = 10              # decision D1: classes larger than this are reported apart
BOOT = 2000
KEYS = ("fault_mode", "location_kind", "term", "anchor_symptom", "anchor_service", "element", "probability",
        "rationale")
FAMILY = {m: fam for fam, ms in CASE_MODES.items() for m in ms}


# ---------------------------------------------------------------------- measures

def measures(sc: dict, truths: list, members: set) -> dict:
    return {"present": float(any(t in sc for t in truths)),
            "top1": max(expected_hit(sc, t, 1) for t in truths), "top5": max(expected_hit(sc, t, 5) for t in truths),
            "mass": max(sc.get(t, 0.0) for t in truths),
            "logmass": math.log(max(max(sc.get(t, 0.0) for t in truths), 1e-6)),
            "class_present": float(any(m in sc for m in members)),
            "class_top1": expected_hit_any(sc, members, 1), "class_top5": expected_hit_any(sc, members, 5),
            "class_mass": sum(sc.get(m, 0.0) for m in members)}


def pooled(payloads: dict) -> dict:
    led = Ledger()
    for name, p in payloads.items():
        led.add_generator(name, p)
    out = {}
    for k, v in led.priors().items():
        causes = led.hypothesis(k).causes
        if causes:
            out["+".join(f"{c.fault_mode}@{c.location.element}" for c in causes)] = v
    return out


def payload(cands, residual) -> GeneratorPayload:
    cs = []
    for cid, m in cands:
        if "+" in cid:
            continue                          # compounds are scored but not pooled
        mode, loc = cid.split("@", 1)
        cs.append(Candidate(hypothesis=single(mode, loc), prior_mass=m))
    return GeneratorPayload(candidates=cs, residual_mass=min(1.0, residual))


def boot_ci(xs: list, rng: random.Random) -> tuple:
    if not xs:
        return (float("nan"),) * 3
    n = len(xs)
    means = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT))
    return sum(xs) / n, means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]


def paired_ci(a: list, b: list, rng: random.Random) -> tuple:
    d = [x - y for x, y in zip(a, b)]
    return boot_ci(d, rng)


# ---------------------------------------------------------------------- loading

def load():
    base = ROOT / OUT
    eq = {json.loads(f.read_text())["case"]: json.loads(f.read_text()) for f in (base / "equivalence").glob("*.json")}
    offline = {json.loads(x)["case"]: json.loads(x) for x in (base / "offline.jsonl").open()}
    arms = {}
    for f in sorted(base.glob("L*_r*.jsonl")):
        arms[f.stem] = {json.loads(x)["case"]: json.loads(x) for x in f.open()}
    return eq, offline, arms


def l2_from_l0(l0: dict) -> dict:
    """Arm L2: L0 repeat 1's answers re-grounded with the documentation-gap weight (no new calls)."""
    out = {}
    for cid, r in l0.items():
        if r.get("error") or "trace" not in r:
            continue
        day, seed = r["day"], r["seed"]
        case = make_case(day, seed)
        kg = case.substrate.kg
        frame = DeterministicFramer(case.substrate).build(case.question)
        prop = LLMProposer(None, kg, GroundingTool(kg, undocumented_weight=W_DOC_GAP))
        ans = SimpleNamespace(assessment=r["trace"]["assessment"], probability_other=r["trace"]["probability_other"],
                              proposals=[{k: q.get(k) for k in KEYS} for q in r["trace"]["proposals"]])
        p = prop.ground(frame, ans, r["refs"])
        out[cid] = {**r, "arm": "L2", "candidates": [[c.hypothesis.id, c.prior_mass] for c in p.candidates],
                    "residual": p.residual_mass}
    return out


# ---------------------------------------------------------------------- analysis

def main():
    rng = random.Random(20261015)
    eq, offline, arms = load()
    if "L0_r1" in arms:
        arms["L2_r1"] = l2_from_l0(arms["L0_r1"])
    # mode frequencies in the history, for reweighting
    freq = Counter(i.truth["fault_mode"] for i in context().incidents if i.ticket["ticketed"])
    rows = []
    for cid, off in sorted(offline.items()):
        if cid not in eq:
            continue
        e = eq[cid]
        members = set(e["members"])
        truths = [f"{e['mode']}@{t}" for t in e["truth_targets"]]
        base = {"case": cid, "day": off["day"], "mode": e["mode"], "family": FAMILY.get(e["mode"], "?"),
                "class_size": len(members), "unlocalisable": len(members) > UNLOCALISABLE}
        pays = {}
        for name, v in off["lines"].items():
            sc = {c: m for c, m in v["candidates"]}
            rows.append({**base, "line": name, **measures(sc, truths, members),
                         "nodes": v["nodes_touched"], "seconds": v["seconds"], "tokens_in": 0, "tokens_out": 0})
            pays[name] = payload(v["candidates"], v["residual"])
        rows.append({**base, "line": "pooled:offline", **measures(pooled(pays), truths, members)})
        for arm, recs in arms.items():
            r = recs.get(cid)
            if not r or r.get("error") or r.get("candidates") is None:
                if r is not None:
                    rows.append({**base, "line": arm, "error": True})
                continue
            sc = {c: m for c, m in r["candidates"]}
            top_p = r["candidates"][0][1] if r["candidates"] else 0.0       # the line's probability on its top candidate
            rows.append({**base, "line": arm, **measures(sc, truths, members),
                         "tokens_in": r.get("input_tokens", 0), "tokens_out": r.get("output_tokens", 0),
                         "tokens_reasoning": r.get("reasoning_tokens", 0), "seconds": r.get("seconds", 0),
                         "top": r["candidates"][0][0] if r["candidates"] else None, "top_stated": top_p})
            if arm in ("L0_r1", "L1_r1", "L3_r1"):
                rows.append({**base, "line": f"pooled:offline+{arm}",
                             **measures(pooled({**pays, "llm": payload(r["candidates"], r["residual"])}),
                                        truths, members)})
    digests = {off.get("manifest") for off in offline.values()} | {r.get("manifest") for recs in arms.values()
                                                                   for r in recs.values() if r.get("arm") != "L2"}
    if len(digests) > 1:
        print(f"WARNING: runs were made under different manifests: {sorted(map(str, digests))}")
    out = ROOT / OUT
    (out / "analysis_rows.json").write_text(json.dumps(rows, indent=0))
    md = report(rows, rng, freq)
    (out / "ANALYSIS.md").write_text(md)
    print(md)


def report(rows, rng, freq) -> str:
    L = ["# Experiment 1 (generation): results", "",
         "Measures: present (truth among candidates); top1, top5 (truth in the top 1 or 5, ties broken at random); "
         "mass (prior probability on the truth); the class_ forms count any member of the truth's equivalence "
         "class. Cases whose class has more than %d members are reported apart and excluded from the means." % UNLOCALISABLE, ""]
    ok = [r for r in rows if not r.get("error")]
    lines = sorted({r["line"] for r in ok})
    for day in sorted({r["day"] for r in ok}):
        sub = [r for r in ok if r["day"] == day and not r["unlocalisable"]]
        n_unloc = len({r["case"] for r in ok if r["day"] == day and r["unlocalisable"]})
        L += [f"## Day {day:.0f} ({len({r['case'] for r in sub})} cases; {n_unloc} unlocalisable reported apart)", "",
              "| Line | class top-1 | class top-5 | class mass | top-1 (strict) | top-5 (strict) | mass (strict) | present |",
              "|---|---|---|---|---|---|---|---|"]
        for ln in lines:
            rs = [r for r in sub if r["line"] == ln]
            if not rs:
                continue
            cells = []
            for k in ("class_top1", "class_top5", "class_mass", "top1", "top5", "mass", "present"):
                m, lo, hi = boot_ci([r[k] for r in rs], rng)
                cells.append(f"{m:.2f} [{lo:.2f}, {hi:.2f}]")
            L.append(f"| {ln} | " + " | ".join(cells) + " |")
        L.append("")
        # paired differences against the reference arm and the best offline line
        ref = "L0_r1"
        by_case = defaultdict(dict)
        for r in sub:
            by_case[r["case"]][r["line"]] = r
        L += [f"Paired differences in class top-1 against {ref} (positive: the line does better):", ""]
        for ln in lines:
            if ln == ref:
                continue
            pairs = [(c[ln]["class_top1"], c[ref]["class_top1"]) for c in by_case.values() if ln in c and ref in c]
            if pairs:
                m, lo, hi = paired_ci([a for a, _ in pairs], [b for _, b in pairs], rng)
                L.append(f"- {ln}: {m:+.2f} [{lo:+.2f}, {hi:+.2f}] (n={len(pairs)})")
        L.append("")
        # per family
        L += ["Class top-1 by fault family:", "", "| Line | " + " | ".join(sorted(CASE_MODES)) + " |",
              "|---|" + "---|" * len(CASE_MODES)]
        for ln in lines:
            cells = []
            for fam in sorted(CASE_MODES):
                rs = [r["class_top1"] for r in sub if r["line"] == ln and r["family"] == fam]
                cells.append(f"{sum(rs) / len(rs):.2f} ({len(rs)})" if rs else "-")
            L.append(f"| {ln} | " + " | ".join(cells) + " |")
        L.append("")
        # frequency-reweighted
        L += ["Class top-1 reweighted by how often each mode is ticketed in the history:", ""]
        for ln in lines:
            rs = [r for r in sub if r["line"] == ln]
            modes = Counter(r["mode"] for r in rs)
            wsum = sum(freq.get(m, 0) for m in modes)
            if not wsum:
                continue
            val = sum(freq.get(m, 0) / wsum * sum(r["class_top1"] for r in rs if r["mode"] == m) / modes[m]
                      for m in modes)
            L.append(f"- {ln}: {val:.2f}")
        L.append("")
    # cost
    L += ["## Cost per case", "", "| Line | seconds | graph nodes touched | input tokens | output tokens |", "|---|---|---|---|---|"]
    for ln in lines:
        rs = [r for r in ok if r["line"] == ln and "seconds" in r]
        if rs:
            L.append(f"| {ln} | {sum(r['seconds'] for r in rs) / len(rs):.2f} | "
                     f"{sum(r.get('nodes', 0) for r in rs) / len(rs):.0f} | "
                     f"{sum(r.get('tokens_in', 0) for r in rs) / len(rs):.0f} | "
                     f"{sum(r.get('tokens_out', 0) for r in rs) / len(rs):.0f} |")
    L.append("")
    # stability
    reps = sorted({r["line"] for r in ok if r["line"].startswith("L0_r")})
    if len(reps) > 1:
        by = defaultdict(dict)
        for r in ok:
            if r["line"] in reps:
                by[r["case"]][r["line"]] = r
        full = [c for c in by.values() if len(c) == len(reps)]
        same_top = sum(1 for c in full if len({c[x]["top"] for x in reps}) == 1)
        spread = [max(c[x]["class_top1"] for x in reps) - min(c[x]["class_top1"] for x in reps) for c in full]
        L += ["## Stability of the reference arm across repeats", "",
              f"- Cases with all {len(reps)} repeats: {len(full)}; same top candidate in every repeat: {same_top} "
              f"({same_top / max(1, len(full)):.0%}).",
              f"- Cases whose class top-1 differs between repeats: {sum(1 for s in spread if s > 0.01)}.", ""]
    # calibration
    L += ["## Calibration of the LLM: its probability on its top candidate against whether that candidate is in the "
          "truth's class", "",
          "| Arm | bin | cases | mean stated | observed |", "|---|---|---|---|---|"]
    for ln in [x for x in lines if x.startswith("L")]:
        rs = [r for r in ok if r["line"] == ln and "top_stated" in r]
        for lo, hi in ((0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.01)):
            b = [r for r in rs if lo <= r["top_stated"] < hi]
            if b:
                L.append(f"| {ln} | {lo:.2f}-{min(hi, 1):.2f} | {len(b)} | "
                         f"{sum(r['top_stated'] for r in b) / len(b):.2f} | {sum(r['class_top1'] for r in b) / len(b):.2f} |")
        if rs:
            brier = sum((r["top_stated"] - r["class_top1"]) ** 2 for r in rs) / len(rs)
            L.append(f"| {ln} | Brier | {len(rs)} | | {brier:.3f} |")
    errs = Counter(r["line"] for r in rows if r.get("error"))
    if errs:
        L += ["", "Errors (cases where the arm failed and which are excluded): " +
              ", ".join(f"{k}: {v}" for k, v in sorted(errs.items()))]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()

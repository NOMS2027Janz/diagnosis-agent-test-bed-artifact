"""Campaign stage 1: results under mixes of kinds other than the equal mix of the case sets.

Per kind, the share right and the share wrongly committed for the agent as built (A-full) and manual
troubleshooting (B) on the 320 sealed cases (C4 record runs); then the overall shares under (a) the equal mix, (b) named scenarios,
(c) 20,000 mixes drawn uniformly from the simplex (Dirichlet(1,...,1)), reporting the range and whether the agent is
ahead in every mix. Writes results/campaign/mix_c4.json."""
import collections
import glob
import json
import random

KINDS = ["hard", "drift", "common_cause", "maintenance", "soft", "monitoring", "coincident", "transient"]
# illustrative scenarios (weights by kind; to be replaced or bracketed by published prevalences)
SCEN = {
    "equal": {k: 1 for k in KINDS},
    "outage-heavy (cuts and hard faults dominate)": dict(hard=30, drift=5, common_cause=15, maintenance=20, soft=10,
                                                         monitoring=5, coincident=5, transient=10),
    "degradation-heavy (soft faults, drifts, transients)": dict(hard=10, drift=20, common_cause=5, maintenance=10,
                                                                soft=25, monitoring=10, coincident=5, transient=15),
}


def load(globs):
    out = {}
    for g in globs:
        for f in glob.glob(g):
            if not f.endswith(".error.json"):
                r = json.load(open(f))
                out[r["seed"]] = r
    return out


def rates(rows):
    c = collections.defaultdict(collections.Counter)
    for r in rows.values():
        o = r["score"]["outcome"]
        k = r["labels"]["kind"]
        c[k]["n"] += 1
        c[k]["right"] += o.startswith("commit_correct")
        c[k]["wrong"] += o.startswith("commit_wrong")
    return {k: {"right": c[k]["right"] / c[k]["n"], "wrong": c[k]["wrong"] / c[k]["n"]} for k in KINDS}


def overall(rt, w):
    z = sum(w.values())
    return {m: sum(w[k] * rt[k][m] for k in KINDS) / z for m in ("right", "wrong")}


A = rates(load(["results/campaign/c4/A-full-cd/s*.json"]))
B = rates(load(["results/campaign/c4/B/s*.json"]))
out = {"per_kind": {k: {"agent": A[k], "manual": B[k]} for k in KINDS}, "scenarios": {}}
for name, w in SCEN.items():
    out["scenarios"][name] = {"agent": overall(A, w), "manual": overall(B, w)}
rng = random.Random(7)
lo = {"agent": 1, "manual": 1}
hi = {"agent": 0, "manual": 0}
ahead = 0
gaps = []
for _ in range(20000):
    x = [rng.expovariate(1) for _ in KINDS]
    w = dict(zip(KINDS, x))
    a, b = overall(A, w)["right"], overall(B, w)["right"]
    lo["agent"], hi["agent"] = min(lo["agent"], a), max(hi["agent"], a)
    lo["manual"], hi["manual"] = min(lo["manual"], b), max(hi["manual"], b)
    ahead += a >= b
    gaps.append(a - b)
gaps.sort()
out["random_mixes"] = {"n": 20000, "agent_right_range": [lo["agent"], hi["agent"]],
                       "manual_right_range": [lo["manual"], hi["manual"]], "agent_ahead_share": ahead / 20000,
                       "gap_5_50_95": [gaps[1000], gaps[10000], gaps[19000]]}
json.dump(out, open("results/campaign/mix_c4.json", "w"), indent=1)
for k in KINDS:
    print(f"{k:<13} agent right {A[k]['right']:.2f} wrong {A[k]['wrong']:.2f} | manual right {B[k]['right']:.2f} "
          f"wrong {B[k]['wrong']:.2f}")
for n, v in out["scenarios"].items():
    print(f"{n:<52} agent {v['agent']['right']:.2f} ({v['agent']['wrong']:.3f} wrong)  manual "
          f"{v['manual']['right']:.2f} ({v['manual']['wrong']:.3f} wrong)")
print(out["random_mixes"])

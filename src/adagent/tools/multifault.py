"""Multiple-fault attribution on the twin's residual: a specialist GNN, used as a generation line ("Specialist GNN
Tools: Plan for Review", section 2).

The question: given the twin's fit of the leading cause, which element, under which fault class, accounts for what
the leader leaves unexplained? Its answer is a set of compound candidates, the leader paired with each of the model's
top five (element, fault class) proposals, with priors from the model's calibrated probabilities, or an abstention.
It does not commit and adds no likelihood term: the twin then fits each compound jointly.

Graph. The generalist ranker's element graph (tools/gnn.py), without a temporal axis: connectors, amplifiers, spans,
WSS channel ports and transponders, with signal-chain and observed-by edges. It is built from the records' network
(the twin's network): the whole network when it has at most gnn.REGION_MAX lightpaths, otherwise the lightpaths
touching the elements whose readings changed or which the leader leaves unexplained, as the ranker's region.

Inputs, per node:
- the ranker's change features (gnn.features), from the same lake summaries the twin is compared with
  (GraphTwin._observed), so the model reads what the agent holds;
- residual channels, laid out as the ranker's stream columns: for every stream the twin compares, the observed change
  minus the twin's predicted change under the fitted leader, divided by the stated combined accuracy of instrument and
  twin (exactly the z of GraphTwin.costs), a flag where the signal is lost in one and not the other, and a flag where
  the twin compares the stream;
- a flag on the leader's own elements.

Output. One logit per (node, fault class the node can host), as the ranker, and one graph-level logit for "no
second fault" (the residual is noise: twin and instrument error only), under one softmax. The trunk and the node head
are initialised from the trained ranker's weights and fine-tuned; the residual input rows and the "no second fault"
head are new.

Abstention. The line abstains when no compared stream is left unexplained beyond the combined accuracy (|z| above
RESID_Z, or a lost signal the leader does not predict), and when the calibrated probability of "no second fault"
exceeds a threshold set on held-out single-fault cases (abstain on at least 98 per cent of them).

Training data come from a deliberately different world (scripts/multifault_gen.py), never the reference network.
Implementation: numpy with autograd, as tools/gnn.py; adjacency is sparse (scipy), with its own vector-Jacobian
product, so that graphs of a thousand nodes train in numpy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import autograd.numpy as anp
import numpy as np
import scipy.sparse as sp
from autograd import grad
from autograd.extend import defvjp, primitive

from . import gnn as G
from .twin import POWER_OBS

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "gnn"
CLASSES = G.CLASSES                     # "none" (column 0) is not a node class here; NOISE is the graph-level class
F_RANK = G.N_FEAT                       # 21 ranker columns
F_RES = 16                              # residual block, laid out as the ranker's columns 5..20
N_FEAT = F_RANK + F_RES + 1             # plus the leader flag
LEADER_COL = N_FEAT - 1
RESID_Z = 3.0                           # a stream misfit by more than three combined standard deviations (4.5 nats)
Z_SCALE, Z_CLIP = 3.0, 5.0
TOP_K = 5
# ranker stream name -> (column of its change, column of its lost flag, column of its compared flag) in the residual
# block (offsets from F_RANK; the block mirrors gnn.features: amp 5..9, trx 10..16, wss 17..20)
RES_COLS = {"in": (0, 2, 4), "out": (1, 3, 4),
            "rx": (5, 9, 10), "osnr": (6, 9, 11), "ber": (7, 9, None), "dgd": (8, 9, None),
            "ocm_p": (12, 14, 15), "ocm_o": (13, 14, 15)}


# ---------------------------------------------------------------------- sparse message passing
@primitive
def spmm(A, h):
    return A @ h


defvjp(spmm, None, lambda ans, A, h: lambda g: A.T @ g)


def adjacency(g: G.LocalGraph) -> list:
    """Per edge type a row-normalised sparse (n x n) matrix, message from i to j at [j, i] (as gnn.adjacency)."""
    n = len(g.nodes)
    out = []
    for t in range(G.N_EDGE_TYPES):
        rows = [j for tt, i, j in g.edges if tt == t]
        cols = [i for tt, i, j in g.edges if tt == t]
        m = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
        m.sum_duplicates()
        m.data[:] = 1.0
        deg = np.asarray(m.sum(1)).ravel()
        out.append(sp.diags(1.0 / np.maximum(deg, 1.0)) @ m)
    return [x.tocsr() for x in out]


def class_mask(g: G.LocalGraph) -> np.ndarray:
    """(nodes, classes): 0 where the node can host the class, -1e9 otherwise; column 0 ("none") always masked."""
    M = G.class_mask(g)
    M[:, 0] = -1e9
    return M


# ---------------------------------------------------------------------- features
def readings_of(obs_cont: dict) -> dict:
    return {k: (v.get("b"), v.get("a")) for k, v in obs_cont.items()}


def residual_z(twin, pred) -> dict:
    """(observable, lake element) -> (z, lost mismatch) for every stream the twin compares: the observed change less
    the predicted change, over the combined accuracy of instrument and twin, as in GraphTwin.costs."""
    out = {}
    for k in twin.keys:
        o, p = twin.obs.cont[k], pred.cont.get(k)
        ob = k[0]
        if p is None or ob == "sop_rate":
            continue
        if o["a"] is None or p["a"] is None or o["b"] is None or p["b"] is None:
            continue
        lost_o, lost_p = o["a"] == -math.inf, p["a"] == -math.inf
        if lost_o != lost_p:
            out[k] = (0.0, 1.0)
            continue
        if lost_o or o["b"] == -math.inf or p["b"] == -math.inf:
            out[k] = (0.0, 0.0)
            continue
        fam = "power" if ob in POWER_OBS else ob
        floor, frac = twin.err[fam]
        gch = twin._change_gain(k)
        dp, do = gch * (p["a"] - p["b"]), o["a"] - o["b"]
        sig = math.hypot(math.hypot(o.get("se_a", 0.05), o.get("se_b", 0.05)), floor + frac * abs(dp))
        out[k] = ((do - dp) / sig, 0.0)
    return out


def leader_nodes(g: G.LocalGraph, events: list) -> set:
    """Graph nodes of the leader: the elements the twin applied its fitted changes to (both fibres of a cut, every
    channel port of a blocked WSS input port, the element of a misreporting instrument)."""
    out = set()
    for e in events:
        t, mode = str(e.target), e.fault_mode
        base = t.split("|")[0]
        cands = {t, base}
        if mode in G.KIND_OF:
            cands.add(G.node_of(mode, t))
        if mode == "WSS_PORT_FAIL":
            cands |= {n for n in g.index if n.startswith(f"wss:{base}|")}
        out |= {g.index[c] for c in cands if c in g.index}
    return out


def features(g: G.LocalGraph, readings: dict, resid: dict, lead: set) -> np.ndarray:
    X = np.zeros((len(g.nodes), N_FEAT))
    X[:, :F_RANK] = G.features(g, readings)
    for i, nid in enumerate(g.nodes):
        for name, key in g.streams[nid].items():
            cols = RES_COLS.get(name)
            r = resid.get(key)
            if cols is None or r is None:
                continue
            cz, cl, cc = cols
            z, lm = r
            X[i, F_RANK + cz] = max(-Z_CLIP, min(Z_CLIP, z / Z_SCALE))
            X[i, F_RANK + cl] = max(X[i, F_RANK + cl], lm)
            if cc is not None:
                X[i, F_RANK + cc] = 1.0
    for i in lead:
        X[i, LEADER_COL] = 1.0
    return X


def unexplained(resid: dict) -> list:
    """Streams the leader leaves unexplained beyond the combined accuracy."""
    return [k for k, (z, lm) in resid.items() if lm > 0 or abs(z) > RESID_Z]


def region(net, readings: dict, resid: dict, cap: int = G.REGION_MAX) -> Optional[list]:
    """None (the whole network) when it has at most `cap` lightpaths; otherwise the lightpaths touching elements
    the leader leaves unexplained, then those touching elements whose readings changed (the ranker's region), ranked
    by how many such elements each touches, at most `cap`. The residual comes first: it is what the model must
    place."""
    if len(net.lightpaths) <= cap:
        return None
    stub = G.GNNRanker.__new__(G.GNNRanker)
    stub.readings, stub.frame, stub.net = readings, None, net
    changed = {el.split("|")[0] for (ob, el) in readings if stub._changed((ob, el))}
    left = {el.split("|")[0] for (ob, el) in unexplained(resid)}
    hits = {}
    for lid, lp in net.lightpaths.items():
        nl = nc = 0
        for d, path, rx in G._paths(net, lp):
            els = {rx} | {G._elid(e) for oid in path for e in net.oms[oid].elements} | \
                  {f"{net.oms[oid].dst}:{oid}" for oid in path}
            nl, nc = max(nl, len(els & left)), max(nc, len(els & changed))
        if nl or nc:
            hits[lid] = (nl, nc)
    return sorted(hits, key=lambda k: (-hits[k][0], -hits[k][1], k))[:cap]


def case_features(twin, h, graph: Optional[G.LocalGraph] = None) -> Optional[dict]:
    """Features of a case from the twin (its observed summaries and its fit of the leader h). None if the twin
    declines the leader. graph: a prebuilt graph of the twin's network (training shares one per network)."""
    fit = twin.fit(h)
    if "declined" in fit:
        return None
    pred = twin.features(fit["events"])
    readings = readings_of(twin.obs.cont)
    resid = residual_z(twin, pred)
    if graph is None:
        graph = G.build_graph(twin.net, region(twin.net, readings, resid))
    lead = leader_nodes(graph, fit["events"])
    return {"graph": graph, "X": features(graph, readings, resid, lead), "unexplained": len(unexplained(resid)),
            "leader_ll": float(fit["ll"])}


# ---------------------------------------------------------------------- the model
def init_params(hidden: int = 32, layers: int = 3, seed: int = 0, ranker: Optional[str] = "ranker") -> dict:
    """The ranker's architecture with residual input rows and a "no second fault" head; with ranker, the trunk and
    the node head start from that model's weights."""
    r = np.random.default_rng(seed)

    def m(a, b, s=1.0):
        return s * r.normal(0.0, math.sqrt(2.0 / (a + b)), size=(a, b))
    p = {"W0": m(N_FEAT, hidden), "b0": np.zeros(hidden), "Wo": m(hidden, len(CLASSES)), "bo": np.zeros(len(CLASSES)),
         "Wn": m(2 * hidden, 1), "bn": np.zeros(1)}
    for l in range(layers):
        p[f"Ws{l}"] = m(hidden, hidden)
        p[f"b{l + 1}"] = np.zeros(hidden)
        for t in range(G.N_EDGE_TYPES):
            p[f"We{l}_{t}"] = m(hidden, hidden)
    if ranker:
        try:
            q, _ = G.load(ranker)
        except FileNotFoundError:
            q = None
        if q is not None and q["W0"].shape == (F_RANK, hidden) and int(q["_layers"]) == layers:
            for k, v in q.items():
                if k.startswith("_") or k == "W0":
                    continue
                p[k] = np.array(v, dtype=float)
            p["W0"][:F_RANK] = q["W0"]
            p["W0"][F_RANK:] = m(N_FEAT - F_RANK, hidden, 0.3)
    p["_layers"] = layers
    return p


def forward(p, X, A, M):
    """Node-class logits (masked) and the "no second fault" logit."""
    h = anp.maximum(0.0, anp.dot(X, p["W0"]) + p["b0"])
    for l in range(int(p["_layers"])):
        z = anp.dot(h, p[f"Ws{l}"]) + p[f"b{l + 1}"]
        for t in range(G.N_EDGE_TYPES):
            z = z + anp.dot(spmm(A[t], h), p[f"We{l}_{t}"])
        h = h + anp.maximum(0.0, z)
    logits = anp.dot(h, p["Wo"]) + p["bo"] + M
    pooled = anp.concatenate([anp.mean(h, axis=0), anp.max(h, axis=0)])
    noise = anp.dot(pooled, p["Wn"]) + p["bn"]
    return logits, noise


def _joint_lsm(logits, noise):
    """Log-probabilities over every (node, class) and "no second fault", under one softmax."""
    flat = anp.concatenate([anp.reshape(logits, (-1,)), noise])
    mx = anp.max(flat)
    lz = mx + anp.log(anp.sum(anp.exp(flat - mx)))
    return flat - lz


def _dense(X):
    return X.toarray() if sp.issparse(X) else np.asarray(X, dtype=float)


def _loss(p, batch):
    tot = 0.0
    for c in batch:
        logits, noise = forward(p, _dense(c["X"]), c["A"], c["M"])
        lp = _joint_lsm(logits, noise)
        tgt = c["y"]                    # flat indices into (node, class), or [] for "no second fault"
        if tgt:
            tot = tot - anp.log(anp.sum(anp.exp(lp[np.asarray(tgt)])) + 1e-12)
        else:
            tot = tot - lp[-1]
    return tot / len(batch)


def labels(g: G.LocalGraph, mode: Optional[str], targets: list) -> list:
    """Flat target indices of a second fault (several equivalent nodes, e.g. both fibres of a cut, share mass);
    [] for none."""
    if mode is None:
        return []
    k = CLASSES.index(mode)
    out = []
    for t in targets:
        nid = t if mode == "INSTRUMENT_MISREPORT" else G.node_of(mode, t)
        if nid in g.index:
            out.append(g.index[nid] * len(CLASSES) + k)
    return out


def prepare(case: dict, graph=None, cache: Optional[dict] = None) -> dict:
    """Sparse adjacency and class mask for a case (shared per graph key case["net"] with cache)."""
    g = graph if graph is not None else case["graph"]
    ck = case.get("net") if cache is not None else None
    if ck is not None and ck in cache:
        A, M = cache[ck]
    else:
        A, M = adjacency(g), class_mask(g)
        if ck is not None:
            cache[ck] = (A, M)
    out = {k: v for k, v in case.items() if k != "graph"}
    out.update({"A": A, "M": M, "nodes": g.nodes, "types": g.types})
    if "y" not in out:
        out["y"] = labels(g, case.get("y_mode"), case.get("y_targets") or [])
    return out


def load_cases(path) -> list[dict]:
    """Prepared cases from a file written by scripts/multifault_gen.py (graphs stored once, cases name theirs)."""
    import pickle
    d = pickle.loads(Path(path).read_bytes())
    cache: dict = {}
    return [prepare(c, d["graphs"][c["net"]], cache) for c in d["cases"]]


def train(cases: list[dict], epochs: int = 25, batch: int = 16, lr: float = 1e-3, hidden: int = 32,
          layers: int = 3, seed: int = 0, log=print, state: Optional[dict] = None, on_epoch=None,
          ranker: Optional[str] = "ranker") -> dict:
    """Adam on the summed loss, from the ranker's weights. state: a checkpoint to resume from (params, moments, step,
    epoch, rng); on_epoch(state) is called after every epoch with such a checkpoint."""
    p0 = init_params(hidden, layers, seed, ranker)
    p = {k: v for k, v in p0.items() if not k.startswith("_")}
    gl = grad(lambda q, b: _loss({**q, "_layers": layers}, b))
    m = {k: np.zeros_like(v) for k, v in p.items()}
    v = {k: np.zeros_like(x) for k, x in m.items()}
    rng = np.random.default_rng(seed)
    step, ep0 = 0, 0
    if state is not None:
        p, m, v = dict(state["p"]), dict(state["m"]), dict(state["v"])
        step, ep0 = state["step"], state["epoch"]
        rng.bit_generator.state = state["rng"]
    for ep in range(ep0, epochs):
        order = rng.permutation(len(cases))
        tot, nb = 0.0, 0
        for s in range(0, len(order), batch):
            b = [cases[i] for i in order[s:s + batch]]
            g_ = gl(p, b)
            step += 1
            for k in m:
                m[k] = 0.9 * m[k] + 0.1 * g_[k]
                v[k] = 0.999 * v[k] + 0.001 * g_[k] ** 2
                p[k] = p[k] - lr * (m[k] / (1 - 0.9 ** step)) / (np.sqrt(v[k] / (1 - 0.999 ** step)) + 1e-8)
            tot += float(_loss({**p, "_layers": layers}, b))
            nb += 1
        log(f"epoch {ep + 1}/{epochs} loss {tot / max(nb, 1):.4f}")
        if on_epoch is not None:
            on_epoch({"p": {k: x.copy() for k, x in p.items()}, "m": {k: x.copy() for k, x in m.items()},
                      "v": {k: x.copy() for k, x in v.items()}, "step": step, "epoch": ep + 1,
                      "rng": rng.bit_generator.state})
    return {**p, "_layers": layers}


def predict(p, case: dict, T: float = 1.0, k: int = TOP_K) -> dict:
    """p_noise and the top k (node index, class, probability), calibrated by temperature T."""
    logits, noise = forward(p, _dense(case["X"]), case["A"], case["M"])
    flat = np.concatenate([np.asarray(logits).reshape(-1), np.asarray(noise)]) / T
    flat = flat - flat.max()
    e = np.exp(flat)
    P = e / e.sum()
    nc = len(CLASSES)
    order = np.argsort(-P[:-1])[:k]
    return {"p_noise": float(P[-1]), "top": [(int(i // nc), CLASSES[int(i % nc)], float(P[i])) for i in order],
            "order": order}


def _flat(p, c) -> np.ndarray:
    logits, noise = forward(p, _dense(c["X"]), c["A"], c["M"])
    return np.concatenate([np.asarray(logits).reshape(-1), np.asarray(noise)])


def fit_temperature(p, held: list[dict]) -> float:
    """The temperature minimising the held-out negative log-likelihood (logits computed once per case)."""
    flats = [(_flat(p, c), c["y"]) for c in held]
    best, bt = math.inf, 1.0
    for T in np.exp(np.linspace(math.log(0.3), math.log(5.0), 40)):
        nll = 0.0
        for f, y in flats:
            z = f / T
            mx = z.max()
            lz = mx + math.log(float(np.exp(z - mx).sum()))
            if y:
                t = z[np.asarray(y)]
                nll -= float(t.max() + math.log(float(np.exp(t - t.max()).sum()))) - lz
            else:
                nll -= float(z[-1]) - lz
        if nll < best:
            best, bt = nll, float(T)
    return bt


def abstain_threshold(p, held: list[dict], T: float, keep: float = 0.98) -> float:
    """The probability of "no second fault" above which the line abstains: set so that it abstains on at least
    `keep` of the held-out single-fault cases (counting those the residual gate already silences)."""
    neg = sorted((predict(p, c, T)["p_noise"] if c.get("unexplained", 1) > 0 else 1.0)
                 for c in held if not c["y"])
    if not neg:
        return 0.5
    i = int(math.floor((1.0 - keep) * len(neg)))
    return float(neg[max(0, i)] - 1e-9)


def evaluate(p, held: list[dict], T: float, thr: float) -> dict:
    """Top-1 and top-5 of the true (element, class) among the model's proposals on held-out two-fault cases (over
    all of them, and when the line speaks), and the abstention rate on single-fault cases."""
    pos = [c for c in held if c["y"]]
    neg = [c for c in held if not c["y"]]
    nc = len(CLASSES)

    def speaks(c, r):
        return c.get("unexplained", 1) > 0 and r["p_noise"] <= thr
    ab = 0
    for c in neg:
        ab += not speaks(c, predict(p, c, T))
    t1 = t5 = s1 = s5 = spoke = 0
    by: dict = {}
    for c in pos:
        r = predict(p, c, T)
        order = list(r["order"])
        rank = min((order.index(t) for t in c["y"] if t in order), default=99)
        sp_ = speaks(c, r)
        spoke += sp_
        t1 += rank < 1
        t5 += rank < 5
        s1 += sp_ and rank < 1
        s5 += sp_ and rank < 5
        cl = CLASSES[c["y"][0] % nc]
        d = by.setdefault(cl, {"n": 0, "top1": 0, "top5": 0, "spoke": 0})
        d["n"] += 1
        d["top1"] += rank < 1
        d["top5"] += rank < 5
        d["spoke"] += sp_
    n = max(len(pos), 1)
    return {"cases": len(held), "two_fault": len(pos), "single_fault": len(neg),
            "abstain_on_single": round(ab / max(len(neg), 1), 3), "spoke_on_two": round(spoke / n, 3),
            "top1": round(t1 / n, 3), "top5": round(t5 / n, 3),
            "top1_when_spoke": round(s1 / n, 3), "top5_when_spoke": round(s5 / n, 3), "by_class": by}


def save(p: dict, meta: dict, name: str = "multifault") -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(MODEL_DIR / f"{name}.npz", **{k: v for k, v in p.items() if not k.startswith("_")},
             _layers=np.array(p["_layers"]))
    (MODEL_DIR / f"{name}.json").write_text(json.dumps(meta, indent=1))
    return MODEL_DIR / f"{name}.npz"


def load(name: str = "multifault") -> tuple:
    z = np.load(MODEL_DIR / f"{name}.npz")
    p = {k: z[k] for k in z.files}
    p["_layers"] = int(p["_layers"])
    return p, json.loads((MODEL_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------- the line at diagnosis
def hypothesis_location(kg, mode: str, nid: str) -> Optional[str]:
    """The location a hypothesis names for a model proposal at graph node nid."""
    if nid.startswith("conn:"):
        return nid[5:]
    if nid.startswith("wss:"):
        return nid[4:]
    if mode == "INSTRUMENT_MISREPORT":
        for i in kg.instruments_for_lake_element(nid):
            if any(o in ("amp_input_power", "amp_output_power", "rx_power")
                   for o in kg.g.nodes[i].get("observes", [])):
                return i
        return None
    return nid


class MultiFaultAttribution:
    """A generation line on return passes. run(leader hypothesis) -> GeneratorPayload of compounds (leader plus each
    proposal), empty when it abstains. The loop's twin is used as is: its observed summaries and its fit of the
    leader (cached by the twin), so the model reads what the agent holds."""
    name, version = "multifault", "0.1"

    def __init__(self, substrate, twin, model: str = "multifault", params: Optional[tuple] = None):
        self.sub, self.kg, self.twin = substrate, substrate.kg, twin
        self.p, self.meta = params if params is not None else load(model)
        self.T = float(self.meta.get("temperature", 1.0))
        self.thr = float(self.meta.get("abstain_threshold", 0.5))
        self.last: Optional[dict] = None

    def run(self, leader):
        from ..schemas import Candidate, GeneratorPayload, Hypothesis, HypothesisKind
        from .walks import single
        empty = GeneratorPayload(candidates=[], residual_mass=1.0)
        f = case_features(self.twin, leader)
        if f is None:
            self.last = {"leader": leader.key(), "abstained": "the twin declines the leader"}
            return empty
        if f["unexplained"] == 0:
            self.last = {"leader": leader.key(), "abstained": "residual within the combined accuracy"}
            return empty
        g = f["graph"]
        case = prepare({"graph": g, "X": f["X"]})
        r = predict(self.p, case, self.T)
        self.last = {"leader": leader.key(), "unexplained": f["unexplained"], "p_noise": round(r["p_noise"], 4),
                     "top": [(g.nodes[i], cl, round(pr, 4)) for i, cl, pr in r["top"]]}
        if r["p_noise"] > self.thr:
            self.last["abstained"] = "no second fault more probable than the threshold"
            return empty
        lead_keys = {(c.fault_mode, c.location.key()) for c in leader.causes}
        cands = []
        for i, cl, pr in r["top"]:
            loc = hypothesis_location(self.kg, cl, g.nodes[i])
            if loc is None:
                continue
            s = single(cl, loc)
            if (cl, s.causes[0].location.key()) in lead_keys:
                continue
            comp = Hypothesis(kind=HypothesisKind.COMPOUND, causes=list(leader.causes) + list(s.causes))
            cands.append(Candidate(hypothesis=comp, prior_mass=min(1.0, pr),
                                   rationale=f"multiple-fault attribution: {cl.lower()} where the leader's residual "
                                             f"points"))
        if not cands:
            return empty
        tot = sum(c.prior_mass for c in cands)
        if tot > 1.0:
            for c in cands:
                c.prior_mass /= tot
            tot = 1.0
        return GeneratorPayload(candidates=cands, residual_mass=max(0.0, 1.0 - tot))

"""Transient event recogniser: a specialist GNN, used as a generation line ("Specialist GNN Tools: Plan for
Review", section 1).

The question: did a brief event happen around the onset, and if so of which kind (a protection-switch hit, a
channel-loading transient on an OMS, a mechanical disturbance of a span, a polarisation transient), where (to the
resolution the data allow) and for how long. Its answer is a set of candidates with priors for the ledger, or an
abstention. It does not commit and adds no likelihood term: the ledger's other terms judge what it proposes.

Graph. Built from a Network: the records' network at diagnosis (twin_network), the training world's own in
training. Nodes: OMSs, their spans and amplifiers, receivers (one per lightpath end) and lightpaths. Edge types:
element to its OMS and back; the signal chain within an OMS, each way; receiver to every OMS its signal crosses and
back; lightpath to its receivers and back.

Inputs. Per node, summaries of its streams around the onset estimate, each relative to the stream's own level
before onset: dips and rises of 10 s power samples (receivers, amplifiers); within-bin dip of 15-minute PM power and
the errored seconds; per-second pre-FEC bit error rate and polarisation rate (coherent tier): the excursion and the
number of seconds affected; sub-second captures (full tier); protection switches of the services each lightpath
carries. Each summary has a presence flag, so a stream missing at a site's tier reads as absent, not as quiet.

Training data come from a deliberately different world (scripts/transient_gen.py): other reference-generator seeds
and sizes, parameter ranges widened, instrument noise scaled, tiers drawn at random per site, the onset estimate
shifted as the framer's estimates are. The test world's network (seed 24) and the experiment seeds are never used.

Implementation: numpy with autograd, as tools/gnn.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Optional

import autograd.numpy as anp
import numpy as np
from autograd import grad

MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "gnn"
KINDS = ("none", "PROTECTION_SWITCH_HIT", "CHANNEL_LOADING_TRANSIENT", "MECHANICAL_DISTURBANCE", "SOP_TRANSIENT")
HOST = {"PROTECTION_SWITCH_HIT": "lp", "CHANNEL_LOADING_TRANSIENT": "oms", "MECHANICAL_DISTURBANCE": "span",
        "SOP_TRANSIENT": "oms"}
TYPES = ("oms", "span", "amp", "rx", "lp")
N_EDGE = 8
F_RX, F_AMP, F_LP = 14, 5, 2
N_FEAT = len(TYPES) + F_RX + F_AMP + F_LP
PRE, POST = 300.0, 600.0            # seconds before and after the onset estimate
LEAD = 150.0                        # the onset estimate can be late by up to two minutes: the event window opens before it


class TGraph:
    def __init__(self):
        self.nodes: list[str] = []
        self.types: list[str] = []
        self.index: dict = {}
        self.edges: list = []
        self.oms_spans: dict = {}        # OMS -> its spans, in order
        self.svc_lps: dict = {}          # service -> lightpaths of its working path

    def add(self, nid: str, t: str) -> int:
        if nid not in self.index:
            self.index[nid] = len(self.nodes)
            self.nodes.append(nid)
            self.types.append(t)
        return self.index[nid]


def build_graph(net, svc_lps: Optional[dict] = None) -> TGraph:
    g = TGraph()
    E = set()
    for oid, o in net.oms.items():
        io = g.add(oid, "oms")
        prev = None
        spans = []
        for e in o.elements:
            eid = e.id
            t = "span" if hasattr(e, "length_km") else "amp"
            i = g.add(eid, t)
            if t == "span":
                spans.append(eid)
            E.add((0, i, io))
            E.add((1, io, i))
            if prev is not None:
                E.add((2, prev, i))
                E.add((3, i, prev))
            prev = i
        g.oms_spans[oid] = spans
    for lid, lp in net.lightpaths.items():
        il = g.add(lid, "lp")
        for rx, path in ((lp.dst_trx, list(lp.oms_path)), (lp.src_trx, _reverse_path(net, lp.oms_path))):
            ir = g.add(rx, "rx")
            E.add((6, il, ir))
            E.add((7, ir, il))
            for oid in path:
                if oid in g.index:
                    E.add((4, ir, g.index[oid]))
                    E.add((5, g.index[oid], ir))
    g.edges = sorted(E)
    g.svc_lps = dict(svc_lps or {})
    return g


def _reverse_path(net, path: list) -> list:
    out = []
    for oid in reversed(path):
        o = net.oms[oid]
        rev = next((k for k, x in net.oms.items() if x.src == o.dst and x.dst == o.src), None)
        if rev:
            out.append(rev)
    return out


def net_service_lightpaths(net) -> dict:
    out = {}
    for sid, s in (getattr(net, "services", None) or {}).items():
        p = (getattr(net, "odu_paths", None) or {}).get(getattr(s, "working", None) or "")
        lps = [h.lightpath for h in p.hops] if p else ([s.lightpath] if getattr(s, "lightpath", None) else [])
        out[sid] = lps
    return out


def kg_service_lightpaths(kg) -> dict:
    out = {}
    for n, d in kg.g.nodes(data=True):
        if d.get("cls") == "ClientService":
            out[n] = [x for x in kg.service_closure(n, "working") if kg.cls(x) == "OCh"]
    return out


# ---------------------------------------------------------------------- features
def _series(recs, el_of=lambda o: o.element):
    """{element: [(t0, t1, value)]} from raw records (chunks of fast streams expanded)."""
    out: dict = {}
    for o in recs:
        if o.period and isinstance(o.value, list):
            for k, v in enumerate(o.value):
                out.setdefault(el_of(o), []).append((o.t_start + k * o.period, o.t_start + (k + 1) * o.period, v))
        else:
            out.setdefault(el_of(o), []).append((o.t_start, o.t_end if o.t_end else o.t_start + 1.0, o.value))
    return out


def _num(v) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = float(v)
        return -math.inf if (v <= -900 or v == -math.inf) else v
    return None


def _power_summ(pts, onset):
    """(dip dB, rise dB, lost, present) of a power series against its level before the onset."""
    if not pts:
        return 0.0, 0.0, 0.0, 0.0
    before = [v for a, b, v in pts if b <= onset - LEAD and v is not None and v > -math.inf]
    after = [v for a, b, v in pts if onset - LEAD <= a <= onset + POST and v is not None]
    if not after:
        return 0.0, 0.0, 0.0, 1.0
    lost = 1.0 if any(v == -math.inf for v in after) else 0.0
    if not before:
        return 0.0, 0.0, lost, 1.0
    base = float(np.median(before))
    fin = [v for v in after if v > -math.inf]
    dip = max([base - v for v in fin], default=0.0)
    rise = max([v - base for v in fin], default=0.0)
    return min(max(dip, 0.0), 30.0), min(max(rise, 0.0), 10.0), lost, 1.0


def node_features(g: TGraph, records: Callable, onset: float) -> np.ndarray:
    """records(observable) -> raw lake observations. Only the window around the onset is read."""
    t0, t1 = onset - PRE, onset + POST
    win = lambda ob: [o for o in records(ob) if (o.t_end or o.t_start) >= t0 and o.t_start <= t1]  # noqa: E731
    X = np.zeros((len(g.nodes), N_FEAT))
    for i, t in enumerate(g.types):
        X[i, TYPES.index(t)] = 1.0
    o_rx = len(TYPES)
    o_amp = o_rx + F_RX
    o_lp = o_amp + F_AMP
    rxp = {el: [(a, b, _num(v)) for a, b, v in pts] for el, pts in _series(win("rx_power")).items()}
    for el, pts in rxp.items():
        if el in g.index:
            dip, rise, lost, pres = _power_summ(pts, onset)
            X[g.index[el], o_rx:o_rx + 4] = [dip / 10.0, rise / 5.0, lost, pres]
    for o in win("rx_power_pm15"):
        el = o.element
        if el not in g.index or not isinstance(o.value, dict):
            continue
        if not (o.t_start <= onset <= (o.t_end or o.t_start)):
            continue
        mn, av = _num(o.value.get("min")), _num(o.value.get("avg"))
        d = 3.0 if mn == -math.inf else (max(0.0, (av - mn)) / 10.0 if (mn is not None and av is not None and
                                                                       av > -math.inf) else 0.0)
        X[g.index[el], o_rx + 4] = max(X[g.index[el], o_rx + 4], min(d, 3.0))
    for o in win("errored_seconds_pm15"):
        el = o.element
        if el in g.index and o.t_start <= onset <= (o.t_end or o.t_start) and isinstance(o.value, (int, float)):
            X[g.index[el], o_rx + 5] = max(X[g.index[el], o_rx + 5], math.log1p(float(o.value)) / 5.0)
    ber = _series(win("pre_fec_ber"))
    for el, pts in ber.items():
        if el not in g.index:
            continue
        before = [v for a, b, v in pts if b <= onset - LEAD and isinstance(v, (int, float)) and v > 0]
        after = [v for a, b, v in pts if onset - LEAD <= a <= t1 and isinstance(v, (int, float)) and v > 0]
        i = g.index[el]
        X[i, o_rx + 6] = 1.0
        if before and after:
            base = float(np.median(before))
            X[i, o_rx + 7] = min(max(0.0, math.log10(max(after) / base)), 6.0) / 3.0
            X[i, o_rx + 8] = math.log1p(sum(1 for v in after if v > 10 * base)) / 4.0
    sop = _series(win("sop_rate"))
    for el, pts in sop.items():
        if el not in g.index:
            continue
        after = [v for a, b, v in pts if onset - LEAD <= a <= t1 and isinstance(v, (int, float))]
        i = g.index[el]
        if after:
            X[i, o_rx + 9] = math.log10(max(max(after), 1.0)) / 7.0
            X[i, o_rx + 10] = math.log1p(sum(1 for v in after if v > 2000.0)) / 4.0
    for o in win("transient_capture"):
        el = o.element
        if el not in g.index or not isinstance(o.value, dict):
            continue
        i = g.index[el]
        X[i, o_rx + 11] = min(X[i, o_rx + 11] + 1.0 / 2.0, 3.0)
        d = float(o.value.get("duration_s") or 0.0)
        X[i, o_rx + 12] = max(X[i, o_rx + 12], (math.log10(max(d, 1e-3)) + 3.0) / 4.0)
        X[i, o_rx + 13] = max(X[i, o_rx + 13], 1.0 if (o.value.get("peak_sop_rate") or 0) > 1e4 else 0.0)
    for ob, off in (("amp_input_power", 0), ("amp_output_power", 2)):
        ser = _series(win(ob))
        for el, pts in ser.items():
            if el not in g.index:
                continue
            dip, rise, lost, pres = _power_summ([(a, b, _num(v)) for a, b, v in pts], onset)
            i = g.index[el]
            X[i, o_amp + off:o_amp + off + 2] = [max(dip, 10.0 * lost) / 10.0, rise / 5.0]
            X[i, o_amp + 4] = max(X[i, o_amp + 4], pres)
    for o in win("PROT_SWITCH"):
        if not (onset - LEAD <= o.t_start <= t1):
            continue
        parts = str(o.element).split(".")
        svc = parts[1] if len(parts) >= 2 else None
        for lid in g.svc_lps.get(svc, []):
            if lid in g.index:
                X[g.index[lid], o_lp] = min(X[g.index[lid], o_lp] + 0.25, 3.0)
    for lid in g.index:
        if g.types[g.index[lid]] == "lp" and any(lid in v for v in g.svc_lps.values()):
            X[g.index[lid], o_lp + 1] = 1.0
    return X


GROUPS = ("oms", "el", "rx", "lp")
GROUP_OF = {"oms": "oms", "span": "el", "amp": "el", "rx": "rx", "lp": "lp"}
# edge type -> (source group, destination group)
BLOCKS = {0: ("el", "oms"), 1: ("oms", "el"), 2: ("el", "el"), 3: ("el", "el"), 4: ("rx", "oms"),
          5: ("oms", "rx"), 6: ("lp", "rx"), 7: ("rx", "lp")}


def adjacency(g: TGraph) -> dict:
    """Message passing by blocks between node groups (the full n x n matrices would not fit in memory for a
    training set): group -> node indices, and per edge type a row-normalised (destination x source) matrix."""
    idx = {gr: [i for i, t in enumerate(g.types) if GROUP_OF[t] == gr] for gr in GROUPS}
    pos = {gr: {i: k for k, i in enumerate(v)} for gr, v in idx.items()}
    A = {}
    for t, (sg, dg) in BLOCKS.items():
        m = np.zeros((len(idx[dg]), len(idx[sg])))
        for tt, i, j in g.edges:
            if tt == t and i in pos[sg] and j in pos[dg]:
                m[pos[dg][j], pos[sg][i]] = 1.0
        A[t] = m / np.maximum(m.sum(1, keepdims=True), 1.0)
    return {"idx": idx, "blocks": A}


def host_mask(g: TGraph) -> np.ndarray:
    """(nodes, kinds - 1): 0 where a node can host the kind, -1e9 otherwise."""
    M = np.full((len(g.nodes), len(KINDS) - 1), -1e9)
    for i, t in enumerate(g.types):
        for k, kind in enumerate(KINDS[1:]):
            if HOST[kind] == t:
                M[i, k] = 0.0
    return M


# ---------------------------------------------------------------------- the model
def init_params(hidden: int = 32, layers: int = 3, seed: int = 0) -> dict:
    r = np.random.default_rng(seed)

    def m(a, b):
        return r.normal(0.0, math.sqrt(2.0 / (a + b)), size=(a, b))
    nk = len(KINDS)
    p = {"W0": m(N_FEAT, hidden), "b0": np.zeros(hidden),
         "Wk": m(2 * hidden, nk), "bk": np.zeros(nk),
         "Wl": m(hidden, nk - 1), "bl": np.zeros(nk - 1),
         "Wd": m(2 * hidden, 1), "bd": np.zeros(1)}
    for l in range(layers):
        p[f"Ws{l}"] = m(hidden, hidden)
        p[f"b{l + 1}"] = np.zeros(hidden)
        for t in range(N_EDGE):
            p[f"We{l}_{t}"] = m(hidden, hidden)
    p["_layers"] = layers
    return p


def forward(p, X, A, M):
    """X and M in the group order of A (prepare); returns kind logits, location logits (same order), log10 duration."""
    idx = A["idx"]
    sizes = [len(idx[gr]) for gr in GROUPS]
    starts = np.cumsum([0] + sizes)
    h0 = anp.maximum(0.0, anp.dot(X, p["W0"]) + p["b0"])
    H = {gr: h0[starts[k]:starts[k + 1]] for k, gr in enumerate(GROUPS)}
    for l in range(int(p["_layers"])):
        Z = {gr: anp.dot(H[gr], p[f"Ws{l}"]) + p[f"b{l + 1}"] for gr in GROUPS}
        for t, (sg, dg) in BLOCKS.items():
            if sizes[GROUPS.index(sg)] and sizes[GROUPS.index(dg)]:
                Z[dg] = Z[dg] + anp.dot(anp.dot(A["blocks"][t], H[sg]), p[f"We{l}_{t}"])
        H = {gr: H[gr] + anp.maximum(0.0, Z[gr]) for gr in GROUPS}
    h = anp.concatenate([H[gr] for gr in GROUPS if sizes[GROUPS.index(gr)]], axis=0)
    pooled = anp.concatenate([anp.mean(h, axis=0), anp.max(h, axis=0)])
    kind = anp.dot(pooled, p["Wk"]) + p["bk"]
    loc = anp.dot(h, p["Wl"]) + p["bl"] + M
    dur = anp.dot(pooled, p["Wd"]) + p["bd"]
    return kind, loc, dur[0]


def _lsm(z, axis=None):
    m = anp.max(z, axis=axis, keepdims=True)
    return z - m - anp.log(anp.sum(anp.exp(z - m), axis=axis, keepdims=True))


def _loss(p, batch):
    tot = 0.0
    for c in batch:
        kind, loc, dur = forward(p, c["X"], c["A"], c["M"])
        y = int(c["y_kind"])
        tot = tot - _lsm(kind)[y]
        if y > 0:
            lp = _lsm(loc[:, y - 1])
            tgt = c["y_loc"]
            # several equivalent hosts (an SOP transient's OMS in both directions is not used: one OMS) share mass
            tot = tot - anp.log(anp.sum(anp.exp(lp[tgt])) + 1e-12)
            if c.get("y_dur") is not None:
                d = dur - c["y_dur"]
                tot = tot + 0.5 * anp.where(anp.abs(d) < 1.0, d * d, 2 * anp.abs(d) - 1.0)
    return tot / len(batch)


def prepare(case: dict, graph=None, cache: Optional[dict] = None) -> dict:
    """Adjacency blocks, and X, the host mask and the location labels put in group order (perm[k] is the graph
    node at position k). The graph is case["graph"] or, for stored training cases, graph; with cache, the
    adjacency, mask and order are computed once per network (cache key case["net"]) and shared."""
    g = graph if graph is not None else case["graph"]
    ck = case.get("net") if cache is not None else None
    if ck is not None and ck in cache:
        A, M, perm, inv = cache[ck]
    else:
        A = adjacency(g)
        perm = [i for gr in GROUPS for i in A["idx"][gr]]
        inv = {i: k for k, i in enumerate(perm)}
        M = host_mask(g)[perm]
        if ck is not None:
            cache[ck] = (A, M, perm, inv)
    out = {k: v for k, v in case.items() if k != "graph"}
    out.update({"A": A, "M": M, "X": np.asarray(case["X"], dtype=float)[perm], "perm": perm})
    if case.get("y_loc"):
        out["y_loc"] = [inv[i] for i in case["y_loc"]]
    return out


def load_cases(path) -> list[dict]:
    """Prepared cases from a file written by scripts/transient_gen.py (graphs stored once, cases name theirs)."""
    import pickle
    from pathlib import Path
    d = pickle.loads(Path(path).read_bytes())
    cache: dict = {}
    return [prepare(c, d["graphs"][c["net"]], cache) for c in d["cases"]]


def train(cases: list[dict], epochs: int = 25, batch: int = 16, lr: float = 3e-3, hidden: int = 32,
          layers: int = 3, seed: int = 0, log=print, state: Optional[dict] = None, on_epoch=None) -> dict:
    """Adam on the summed loss. state: a checkpoint to resume from (params, moments, step, epoch, rng);
    on_epoch(state) is called after every epoch with such a checkpoint."""
    p0 = init_params(hidden, layers, seed)
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


def predict(p, case: dict, T: float = 1.0) -> dict:
    kind, loc, dur = forward(p, case["X"], case["A"], case["M"])
    kind = np.asarray(kind) / T
    pk = np.exp(kind - kind.max())
    pk = pk / pk.sum()
    locs = {}
    for k, kd in enumerate(KINDS[1:]):
        z = np.asarray(loc[:, k])
        e = np.exp(z - z.max())
        locs[kd] = e / e.sum()
    return {"p_kind": pk, "p_loc": locs, "log10_dur": float(dur)}


def fit_temperature(p, held: list[dict]) -> float:
    best, bt = math.inf, 1.0
    for T in np.exp(np.linspace(math.log(0.3), math.log(5.0), 40)):
        nll = 0.0
        for c in held:
            pk = predict(p, c, T)["p_kind"]
            nll -= math.log(max(pk[int(c["y_kind"])], 1e-12))
        if nll < best:
            best, bt = nll, float(T)
    return bt


def abstain_threshold(p, held: list[dict], T: float, keep: float = 0.98) -> float:
    """The probability of 'no transient' above which the line abstains: set so that it abstains on at least `keep`
    of the held-out cases with no transient."""
    neg = sorted(predict(p, c, T)["p_kind"][0] for c in held if int(c["y_kind"]) == 0)
    if not neg:
        return 0.5
    i = int(math.floor((1.0 - keep) * len(neg)))
    return float(neg[max(0, i)] - 1e-9)


def evaluate(p, held: list[dict], T: float, thr: float) -> dict:
    out = {"cases": len(held)}
    pos = [c for c in held if int(c["y_kind"]) > 0]
    neg = [c for c in held if int(c["y_kind"]) == 0]
    out["abstain_on_negatives"] = round(sum(predict(p, c, T)["p_kind"][0] > thr for c in neg) / max(len(neg), 1), 3)
    by = {}
    for kd in KINDS[1:]:
        sub = [c for c in pos if KINDS[int(c["y_kind"])] == kd]
        if not sub:
            continue
        kind_ok = loc1 = loc3 = spoke = 0
        for c in sub:
            r = predict(p, c, T)
            if r["p_kind"][0] > thr:
                continue
            spoke += 1
            k = int(np.argmax(r["p_kind"][1:])) + 1
            if KINDS[k] == kd:
                kind_ok += 1
                order = list(np.argsort(-r["p_loc"][kd]))
                rank = min(order.index(t) for t in c["y_loc"])
                loc1 += rank < 1
                loc3 += rank < 3
        by[kd] = {"n": len(sub), "spoke": spoke, "kind_right": kind_ok, "loc_top1": loc1, "loc_top3": loc3}
    out["by_kind"] = by
    return out


def save(p: dict, meta: dict, name: str = "transient") -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(MODEL_DIR / f"{name}.npz", **{k: v for k, v in p.items() if not k.startswith("_")},
             _layers=np.array(p["_layers"]))
    (MODEL_DIR / f"{name}.json").write_text(json.dumps(meta, indent=1))
    return MODEL_DIR / f"{name}.npz"


def load(name: str = "transient") -> tuple:
    z = np.load(MODEL_DIR / f"{name}.npz")
    p = {k: z[k] for k in z.files}
    p["_layers"] = int(p["_layers"])
    return p, json.loads((MODEL_DIR / f"{name}.json").read_text())


# ---------------------------------------------------------------------- the line at diagnosis
class TransientRecogniser:
    """A generation line. run(frame) -> GeneratorPayload (empty when it abstains)."""
    name, version = "transient", "0.1"
    TOP_LOC = 3

    def __init__(self, substrate, sources, onset: float, model: str = "transient"):
        from .twin import twin_network
        self.sub, self.kg = substrate, substrate.kg
        self.p, self.meta = load(model)
        design = (sources.practice.get("line_design") or {}).get("design", {})
        self.net = twin_network(self.kg, design)
        self.g = build_graph(self.net, kg_service_lightpaths(self.kg))
        self.onset = float(onset)
        X = node_features(self.g, lambda ob: self.sub.records(ob), self.onset)
        self.case = prepare({"graph": self.g, "X": X})
        self.T = float(self.meta.get("temperature", 1.0))
        self.thr = float(self.meta.get("abstain_threshold", 0.5))
        self.last = None

    def run(self, frame=None):
        from ..schemas import Candidate, GeneratorPayload
        from .walks import single
        r = predict(self.p, self.case, self.T)
        self.last = {"p_kind": [round(float(x), 4) for x in r["p_kind"]], "log10_dur": round(r["log10_dur"], 2)}
        if r["p_kind"][0] > self.thr:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        cands = []
        for k, kd in enumerate(KINDS[1:], start=1):
            pk = float(r["p_kind"][k])
            if pk < 0.05:
                continue
            pl = r["p_loc"][kd]
            order = [i for i in np.argsort(-pl)[:self.TOP_LOC]]
            z = float(sum(pl[i] for i in order)) or 1.0
            for i in order:
                nid = self.g.nodes[self.case["perm"][i]]
                mass = pk * float(pl[i]) / z
                if kd == "SOP_TRANSIENT":
                    spans = self.g.oms_spans.get(nid, [])      # placed only to the OMS: every span, one class
                    for sp in spans:
                        cands.append(Candidate(hypothesis=single(kd, sp), prior_mass=mass / max(len(spans), 1),
                                               rationale="transient recogniser: polarisation event on this OMS"))
                else:
                    cands.append(Candidate(hypothesis=single(kd, nid), prior_mass=mass,
                                           rationale=f"transient recogniser: {kd.lower()} here"))
        if not cands:
            return GeneratorPayload(candidates=[], residual_mass=1.0)
        tot = sum(c.prior_mass for c in cands)
        return GeneratorPayload(candidates=cands, residual_mass=max(0.0, 1.0 - tot))

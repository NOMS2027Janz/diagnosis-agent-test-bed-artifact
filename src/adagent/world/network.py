"""Network model and generator for the world simulator ("Diagnosis Agent Framework
Specification" §11.1; "Diagnosis Agent Work: Synopsis and Plan" §5.1).

Generic, vendor-neutral optical and OTN equipment. A network is built from a topology of
ROADM nodes and fibre links. Each link becomes an OMS: a booster amplifier at the source
degree, one or more fibre spans with in-line amplifiers between them, and a pre-amplifier at
the sink degree. Links are directional pairs (A->Z and Z->A carried on separate fibres in the
same cable). Lightpaths (OCh) run between transponders over a sequence of OMSs, and client
services ride on ODUs mapped into the OTU of each lightpath.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx


@dataclass
class Span:
    id: str
    oms: str
    index: int                 # position within the OMS, 0-based
    length_km: float
    loss_db_per_km: float = 0.20
    connector_loss_db: float = 1.0      # both ends combined
    aerial: bool = False
    cable: str = ""
    duct: str = ""
    recorded_cable: Optional[str] = None   # what plant records say; None means same as cable
    recorded_duct: Optional[str] = None    # differs from duct when a shared duct is undisclosed
    # hidden true values (world version 2, decision D2); None means the nominal value holds
    true_loss_db_per_km: Optional[float] = None
    true_connector_loss_db: Optional[float] = None
    pmd_ps_sqrt_km: Optional[float] = None

    @property
    def nominal_loss_db(self) -> float:
        """The loss as planned and recorded: what the knowledge graph and the twin know."""
        return self.length_km * self.loss_db_per_km + self.connector_loss_db

    @property
    def actual_loss_db(self) -> float:
        """The loss the world applies: nominal unless world version 2 has drawn hidden true values."""
        a = self.true_loss_db_per_km if self.true_loss_db_per_km is not None else self.loss_db_per_km
        c = self.true_connector_loss_db if self.true_connector_loss_db is not None else self.connector_loss_db
        return self.length_km * a + c


@dataclass
class Amplifier:
    id: str
    oms: str
    role: str                  # booster | inline | preamp
    index: int                 # position along the OMS, 0 = booster
    nf_db: float = 5.5         # datasheet typical: what the knowledge graph and the twin know
    gain_db: float = 0.0       # set at commissioning (constant-gain mode)
    pump_current_pct: float = 100.0
    true_nf_db: Optional[float] = None   # hidden true noise figure (world version 2); None means nf_db
    true_tilt_db: float = 0.0            # hidden static gain tilt across the band (structural mismatch; twin: 0)

    @property
    def actual_nf_db(self) -> float:
        return self.true_nf_db if self.true_nf_db is not None else self.nf_db


@dataclass
class OMS:
    id: str
    src: str                   # ROADM node
    dst: str
    link: str                  # undirected link id (shared cable and duct)
    elements: list = field(default_factory=list)   # alternating Amplifier, Span, Amplifier, ...

    @property
    def spans(self) -> list[Span]:
        return [e for e in self.elements if isinstance(e, Span)]

    @property
    def amps(self) -> list[Amplifier]:
        return [e for e in self.elements if isinstance(e, Amplifier)]


@dataclass
class Transponder:
    id: str
    node: str
    modulation: str            # DP-QPSK | DP-16QAM
    baud_gbd: float = 64.0
    tx_power_dbm: float = 0.0
    trx_snr_db: float = 22.0   # implementation-limited SNR of the transceiver pair
    kind: str = "transponder"  # transponder | muxponder | otn_line (line port of an OTN switch)
    rate_g: int = 100
    host: Optional[str] = None # OTN switch id for otn_line ports


@dataclass
class Lightpath:
    id: str                    # OCh id; the OTU and ODU names derive from it
    src_trx: str
    dst_trx: str
    nodes: list[str]
    oms_path: list[str]
    slot: int                  # frequency slot index
    protection_group: Optional[str] = None
    rate_g: int = 100
    kind: str = "direct"       # direct | trunk (hub to hub) | access (site to hub) | express
    units: int = 10            # capacity in 10G tributary units


@dataclass
class ODUHop:
    lightpath: str
    a: str                     # site at one end of the hop
    z: str


@dataclass
class ODUPath:
    id: str
    odu: str                   # ODU4 | ODU2 | ODUflex
    hops: list                 # ODUHop, in order from service source to destination
    units: int = 10


@dataclass
class OTNSwitch:
    id: str
    site: str


@dataclass
class SRLG:
    id: str
    kind: str                  # duct | cable
    spans: list                # span ids (both directions)
    known: bool = True         # recorded in plant records


@dataclass
class ClientService:
    id: str
    lightpath: str             # first lightpath of the working path (kept for single-hop code)
    rate: str = "100GE"
    src: str = ""
    dst: str = ""
    working: Optional[str] = None      # ODUPath id
    protect: Optional[str] = None      # ODUPath id
    protection: str = "none"           # none | sncp | och1+1


@dataclass
class Network:
    name: str
    nodes: list[str]
    links: dict[str, tuple[str, str]]
    oms: dict[str, OMS]
    spans: dict[str, Span]
    amps: dict[str, Amplifier]
    transponders: dict[str, Transponder]
    lightpaths: dict[str, Lightpath]
    services: dict[str, ClientService]
    launch_dbm_per_ch: float = 1.0
    drop_target_dbm: float = -6.0
    channels_loaded: int = 64          # full-band loading assumed for NLI (ASE fill)
    slot_ghz: float = 75.0
    sites: dict = field(default_factory=dict)          # site -> (x_km, y_km)
    hubs: list = field(default_factory=list)
    otn_switches: dict = field(default_factory=dict)
    odu_paths: dict = field(default_factory=dict)
    srlgs: dict = field(default_factory=dict)
    link_km: dict = field(default_factory=dict)
    gap_scale: float = 0.0                              # world version 2: size of the hidden gap (0 = version 1)
    calibration_db: dict = field(default_factory=dict)  # instrument element -> calibration offset, dB (hidden)
    nli_factor: float = 1.0                             # structural mismatch: world NLI over the GN closed form
    mismatch: int = 0                                   # structural mismatch level (world/gap.py; 0 = none)
    spectral: bool = False                              # spectral mode (world/spectral.py): cascade filtering

    def oms_between(self, a: str, b: str) -> str:
        for o in self.oms.values():
            if o.src == a and o.dst == b:
                return o.id
        raise KeyError((a, b))


def _build_oms(oms_id: str, src: str, dst: str, link: str, span_lengths: list[float],
               cable: str, duct: str, aerial_flags: list[bool]) -> OMS:
    o = OMS(oms_id, src, dst, link)
    o.elements.append(Amplifier(f"{oms_id}.amp0", oms_id, "booster", 0))
    for i, L in enumerate(span_lengths):
        o.elements.append(Span(f"{oms_id}.span{i}", oms_id, i, L, aerial=aerial_flags[i],
                               cable=cable, duct=duct))
        role = "preamp" if i == len(span_lengths) - 1 else "inline"
        o.elements.append(Amplifier(f"{oms_id}.amp{i + 1}", oms_id, role, i + 1))
    return o


def build_network(name: str, topology: dict[str, tuple[str, str, float]], *, seed: int = 0,
                  max_span_km: float = 90.0, n_lightpaths: int = 8, aerial_fraction: float = 0.15,
                  shared_ducts: Optional[list[list[str]]] = None) -> Network:
    """Build a network from {link_id: (node_a, node_b, length_km)}.

    Links listed together in shared_ducts share a duct (a shared-risk group)."""
    rng = random.Random(seed)
    duct_of = {}
    for gi, group in enumerate(shared_ducts or []):
        for lk in group:
            duct_of[lk] = f"duct{gi}"
    nodes = sorted({n for a, b, _ in topology.values() for n in (a, b)})
    oms, spans, amps = {}, {}, {}
    g = nx.Graph()
    for lk, (a, b, L) in topology.items():
        n_sp = max(1, -(-int(L) // int(max_span_km)))
        base = L / n_sp
        lengths = [round(base * rng.uniform(0.9, 1.1), 1) for _ in range(n_sp)]
        scale = L / sum(lengths)
        lengths = [round(x * scale, 1) for x in lengths]
        aerial = [rng.random() < aerial_fraction for _ in range(n_sp)]
        cable, duct = f"cable.{lk}", duct_of.get(lk, f"duct.{lk}")
        for (s, d) in ((a, b), (b, a)):
            oid = f"oms.{s}-{d}"
            o = _build_oms(oid, s, d, lk, lengths if s == a else lengths[::-1], cable, duct,
                           aerial if s == a else aerial[::-1])
            oms[oid] = o
            spans.update({sp.id: sp for sp in o.spans})
            amps.update({am.id: am for am in o.amps})
        g.add_edge(a, b, weight=L, link=lk)

    # commission amplifiers in constant-gain mode: each in-line or pre-amp gain equals the
    # nominal loss of the span before it; the booster sets the launch power.
    for o in oms.values():
        els = o.elements
        for i, e in enumerate(els):
            if isinstance(e, Amplifier) and e.index > 0:
                e.gain_db = els[i - 1].nominal_loss_db

    trx, lps, svcs = {}, {}, {}
    pairs = [(a, b) for a in nodes for b in nodes if a < b]
    rng.shuffle(pairs)
    slot_use: dict[str, set[int]] = {o: set() for o in oms}
    for i in range(min(n_lightpaths, len(pairs) * 2)):
        a, b = pairs[i % len(pairs)]
        path = nx.shortest_path(g, a, b, weight="weight")
        oms_path = [f"oms.{path[j]}-{path[j + 1]}" for j in range(len(path) - 1)]
        length = sum(g[path[j]][path[j + 1]]["weight"] for j in range(len(path) - 1))
        mod = "DP-16QAM" if length <= 600 else "DP-QPSK"
        rev_path = [f"oms.{path[j + 1]}-{path[j]}" for j in range(len(path) - 1)][::-1]
        slot = 0
        while any(slot in slot_use[o] for o in oms_path + rev_path):
            slot += 1
        for o in oms_path + rev_path:
            slot_use[o].add(slot)
        lid = f"och{i}"
        trx[f"trx.{lid}.a"] = Transponder(f"trx.{lid}.a", a, mod)
        trx[f"trx.{lid}.z"] = Transponder(f"trx.{lid}.z", b, mod)
        lps[lid] = Lightpath(lid, f"trx.{lid}.a", f"trx.{lid}.z", path, oms_path, slot)
        svcs[f"svc.{lid}"] = ClientService(f"svc.{lid}", lid)
    return Network(name, nodes, {k: (v[0], v[1]) for k, v in topology.items()}, oms, spans,
                   amps, trx, lps, svcs)


# ------------------------------------------------------------------ reference instances

def reference_line(seed: int = 0) -> Network:
    """Two ROADMs joined by a 480 km line of six spans, as in the twin and GNN papers."""
    return build_network("line6", {"L1": ("A", "B", 480.0)}, seed=seed, max_span_km=80.0,
                         n_lightpaths=4, aerial_fraction=0.0)


def reference_mesh(seed: int = 0) -> Network:
    """Six ROADMs, eight links, with two links sharing a duct."""
    topo = {
        "L1": ("A", "B", 210.0), "L2": ("B", "C", 160.0), "L3": ("C", "D", 240.0),
        "L4": ("D", "E", 180.0), "L5": ("E", "F", 150.0), "L6": ("F", "A", 230.0),
        "L7": ("B", "E", 300.0), "L8": ("C", "F", 280.0),
    }
    return build_network("mesh6", topo, seed=seed, n_lightpaths=12, shared_ducts=[["L2", "L8"]])

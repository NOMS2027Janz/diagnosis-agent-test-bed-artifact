"""Twin-vs-world error on every receiver's margin and OSNR, no fault, day 2190: python scripts/twin_fidelity.py SCALE:CAL ...
(CAL 1 = calibrated gap). The twin is built from the records of the same world."""
import sys, math, statistics as st
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))
from adagent.harness.cases2 import make_case2
from adagent.harness.cases import context
from adagent.tools.twin import twin_network
from adagent.world.simulator import evaluate, effects_at
Effects = None
from adagent.harness.cases import sources
design = (sources().practice.get("line_design") or {}).get("design", {})
for scale, cal in [(float(a), b == "1") for a, b in (x.split(":") for x in sys.argv[1:])]:
    c = make_case2(2190.0, 9100, gap_scale=scale, gap_calibrated=cal)
    ts = evaluate(twin_network(c.substrate.kg, design), effects_at([], 0.0))
    net = context(gap_scale=scale, gap_calibrated=cal).network_at(2190.0)
    ws = evaluate(net, effects_at([], 0.0))
    dm, do = [], []
    for k, r in ws.rx.items():
        t = ts.rx.get(k)
        if t is None or not math.isfinite(r.margin_db) or not math.isfinite(t.margin_db):
            continue
        dm.append(t.margin_db - r.margin_db); do.append(t.osnr_db - r.osnr_db)
    a = sorted(abs(x) for x in dm)
    print(f"scale {scale} cal {cal}: n={len(dm)} margin mean {st.mean(dm):+.3f} sd {st.pstdev(dm):.3f} "
          f"p95|.| {a[int(.95*len(a))]:.2f} max {a[-1]:.2f}; osnr mean {st.mean(do):+.3f} sd {st.pstdev(do):.3f}")

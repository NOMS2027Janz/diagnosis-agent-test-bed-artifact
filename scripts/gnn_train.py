"""Train the preliminary GNN ranker on generated local topologies (src/adagent/tools/gnn.py).

Run from the project root (no API calls; the reference network is never used):
    python scripts/gnn_train.py --train 6000 --held 1000 --epochs 30
Generated cases use seeds 1,000,000 upwards (training) and 2,000,000 upwards (held-out), so they never coincide with
experiment seeds. Writes models/gnn/ranker.npz and ranker.json (settings, training loss, held-out recall at k).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from adagent.tools.gnn import evaluate_ranking, generate_case, save, train
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=6000)
    ap.add_argument("--held", type=int, default=1000)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument("--name", default="ranker")
    a = ap.parse_args()
    t0 = time.time()
    tr = [generate_case(1_000_000 + i) for i in range(a.train)]
    he = [generate_case(2_000_000 + i) for i in range(a.held)]
    print(f"generated {a.train} + {a.held} cases in {time.time() - t0:.0f} s", flush=True)
    losses = []
    from adagent.tools.gnn import MODEL_DIR
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    p = train(tr, epochs=a.epochs, hidden=a.hidden, layers=a.layers, checkpoint=MODEL_DIR / f"{a.name}.ckpt",
              log=lambda s: (losses.append(s), print(s, flush=True)))
    ev = evaluate_ranking(p, he)
    by_class = {}
    from adagent.tools.gnn import CLASSES
    for cl in CLASSES[1:]:
        sub = [c for c in he if cl in c["labels"].values()]
        if sub:
            r = evaluate_ranking(p, [{**c, "labels": {k: v for k, v in c["labels"].items() if v == cl}} for c in sub])
            by_class[cl] = {"faults": r["faults"], "recall_at_3": round(r["recall_at_3"], 3)}
    meta = {"version": "0.1", "train_cases": a.train, "held_cases": a.held, "epochs": a.epochs, "hidden": a.hidden,
            "layers": a.layers, "loss": losses[-3:], **{k: (round(v, 4) if isinstance(v, float) else v)
                                                       for k, v in ev.items()},
            "by_class": by_class, "seconds": round(time.time() - t0)}
    save(p, meta, a.name)
    print(json.dumps(meta, indent=1), flush=True)


if __name__ == "__main__":
    main()

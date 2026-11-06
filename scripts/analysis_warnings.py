"""Candidate warnings on committed conclusions, measured on existing records: how often each condition holds on
wrong commitments against right ones. Conditions use only what the agent holds at decision time."""
import json, glob, collections, sys
import pathlib as _pl; _R=_pl.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(_R/'src')); sys.path.insert(0, str(_R/'scripts'))
G = lambda o: 'R' if o in ('commit_correct', 'commit_correct_ambiguous') else 'W' if o in ('commit_wrong', 'coarse_wrong') else 'P' if o == 'commit_partial' else 'C' if o == 'coarse_correct' else 'I'
ARMS = {'sealed': ('results/campaign/c4/A-full-cd', None, None), 'diag-fut': ('results/campaign/c4_dev/A-full-cd-fut', None, None),
        'kg-none': ('results/campaign/kgx_c4/A-full-cd-kg-none', 'none', 'diag'), 'kg-inst': ('results/campaign/kgx_c4/A-full-cd-kg-inst', None, None),
        'kg-flat': ('results/campaign/kgx_c4/A-full-cd-kg-flat', None, None), 'kg-sig': ('results/campaign/kgx_c4/A-full-cd-kg-sig', 'sig', 'diag'),
        'kg-modes': ('results/campaign/kgx_c4/A-full-cd-kg-modes', 'modes', 'diag'),
        'test-sig-removed': ('results/campaign/a20/test_empty_nodup/A-full-cd-kg-sig-comp', 'sig', 'test'),
        'test-modes-removed': ('results/campaign/a20/test_empty_nodup/A-full-cd-kg-modes-comp', 'modes', 'test'),
        'test-sig-completed': ('results/campaign/a20/test_nodup/A-full-cd-kg-sig-comp', 'sig', 'test'),
        'test-modes-completed': ('results/campaign/a20/test_nodup/A-full-cd-kg-modes-comp', 'modes', 'test'),
        'test-control': ('results/campaign/a20/test_empty_nodup/A-full-cd-kg-none-comp', None, None)}
import a20_complete
from adagent.harness import exp2
kt = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False, gap_calibrated=True,
                     instrumentation='status_quo', mismatch=0, ladder=()).substrate.kg
held = {}
def undoc_types(kind, draw):
    if (kind, draw) not in held:
        g, _ = a20_complete.degrade(kt, kind, draw)
        held[(kind, draw)] = g
    g = held[(kind, draw)]
    return {u for u, m, d in g.g.edges(data=True) if d.get('rel') == 'hasFaultMode' and not d.get('effects')}, g
rows = []
for arm, (path, kind, draw) in ARMS.items():
    for f in glob.glob(path + '/s*.json'):
        if 'error' in f: continue
        r = json.load(open(f)); L = r['loop']; d = L.get('decision') or {}
        g = G(r['score']['outcome'])
        if d.get('outcome') != 'commit' or g not in 'RWPC': continue
        top = L['trajectory'][-1]['top']; hyp = d['hypothesis']; p = d.get('probability', 1)
        runner = next((q for h, q in top if h != hyp and h != 'NONE_OF_THESE'), 0.0)
        none = L['trajectory'][-1].get('none', 0)
        tr = L.get('transient') or {}; pk = tr.get('p_kind') or [1, 0, 0, 0, 0]
        causes = hyp.split('+')
        modes = [c.split('@')[0] for c in causes]; els = [c.split('@el:')[1].rstrip('()') if '@el:' in c else '' for c in causes]
        undoc = False
        if kind in ('sig', 'modes'):
            U, gg = undoc_types(kind, draw)
            tys = {kt.type_of(e) for e in els if e in kt.g}
            if kind == 'sig':
                undoc = bool(tys & U)
            else:   # modes removed: a type whose documented modes are fewer than the vocabulary would suggest is not visible; use the sig criterion only
                undoc = False
        rows.append({'arm': arm, 'g': g, 'wrong': g == 'W',
                     'marginal (p<0.95)': p < 0.95, 'contender (runner-up >0.05)': runner > 0.05,
                     '"none of these" >0.02': none > 0.02, 'no check made': len(L.get('checks', [])) == 0,
                     'transient recognised': max(pk[1:]) > 0.5, 'two or more causes': len(causes) > 1,
                     'instrument fault concluded': any(m in ('INSTRUMENT_MISREPORT', 'TELEMETRY_STALE') for m in modes),
                     'type with undocumented signatures': undoc,
                     'returns to generation >=2': len(L.get('passes', [])) >= 3})
feats = [k for k in rows[0] if k not in ('arm', 'g', 'wrong')]
W = [r for r in rows if r['wrong']]; N = [r for r in rows if not r['wrong']]
print(f"commitments {len(rows)}: wrong {len(W)}, right or partly right {len(N)}")
print(f"{'condition':<36}{'fires on wrong':>16}{'fires on others':>17}{'wrong rate if fires':>21}{'if not':>8}")
for k in feats:
    fw = sum(r[k] for r in W); fn = sum(r[k] for r in N)
    rate_f = fw / max(1, fw + fn); rate_n = (len(W) - fw) / max(1, len(W) - fw + len(N) - fn)
    print(f"{k:<36}{fw:>8}/{len(W):<7}{fn:>9}/{len(N):<7}{rate_f:>14.1%}{rate_n:>12.1%}")
print('wrong by arm', collections.Counter(r['arm'] for r in W))
json.dump(rows, open('warnings_rows.json', 'w'))

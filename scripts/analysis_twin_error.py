import sys, math, statistics as S, yaml; sys.path[:0]=['src','scripts']
from adagent.harness import exp2
from adagent.world.physics import evaluate
from adagent.tools.twin import twin_network
design=(yaml.safe_load(open('knowledge/practice/line_design.yaml')) or {}).get('design',{})
case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False, gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
wn = case.world.net
tn = twin_network(case.substrate.kg, design)
ws, ts = evaluate(wn), evaluate(tn)
def st(name, xs):
    xs=[x for x in xs if math.isfinite(x)]
    rms=math.sqrt(sum(x*x for x in xs)/len(xs))
    print(f"{name}: n={len(xs)} mean={S.mean(xs):+.3f} sd={S.pstdev(xs):.3f} rms={rms:.3f} min={min(xs):+.3f} max={max(xs):+.3f}")
sp=[ws.span_loss_db[k]-ts.span_loss_db[k] for k in ws.span_loss_db if k in ts.span_loss_db]
st('span loss dB (world - twin)', sp)
keys=[k for k in ws.rx if k in ts.rx]
st('margin dB', [ws.rx[k].margin_db-ts.rx[k].margin_db for k in keys])
st('OSNR dB', [ws.rx[k].osnr_db-ts.rx[k].osnr_db for k in keys])
st('DGD ps', [ws.rx[k].dgd_ps-ts.rx[k].dgd_ps for k in keys])
st('DGD rel', [(ws.rx[k].dgd_ps-ts.rx[k].dgd_ps)/ts.rx[k].dgd_ps for k in keys if ts.rx[k].dgd_ps>0])
lb=lambda b: math.log10(max(b,1e-30))
st('log10 pre-FEC BER (decades)', [lb(ws.rx[k].pre_fec_ber)-lb(ts.rx[k].pre_fec_ber) for k in keys])
print('twin DGD mean ps', S.mean(ts.rx[k].dgd_ps for k in keys))

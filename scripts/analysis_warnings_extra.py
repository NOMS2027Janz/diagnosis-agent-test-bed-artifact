import json,sys,collections
import pathlib as _pl; _R=_pl.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(_R/'src')); sys.path.insert(0, str(_R/'scripts'))
exec(open('analysis_warnings.py').read().split("rows = []")[0])
rows=[]
for arm,(path,kind,draw) in ARMS.items():
  for f in glob.glob(path+'/s*.json'):
    if 'error' in f: continue
    r=json.load(open(f)); L=r['loop']; d=L.get('decision') or {}; g=G(r['score']['outcome'])
    if d.get('outcome')!='commit' or g not in 'RWPC': continue
    hyp=d['hypothesis']; els=[c.split('@el:')[1].rstrip('()') if '@el:' in c else '' for c in hyp.split('+')]
    near=False; nearU=False
    if kind=='sig':
      U,gg=undoc_types(kind,draw)
      tys=set()
      for e in els:
        e0=e.split('|')[0]
        if e0 not in kt.g: continue
        nb=set(kt.g.successors(e0))|set(kt.g.predecessors(e0))
        for n in list(nb):
          nb|=set(kt.g.successors(n))|set(kt.g.predecessors(n))
        tys|={kt.type_of(n) for n in nb if n in kt.g}
      nearU=bool(tys & U)
    top=L['trajectory'][-1]['top']; p=d.get('probability',1); none=L['trajectory'][-1].get('none',0)
    rows.append({'arm':arm,'wrong':g=='W','gap nearby (sig arms)':nearU,'marginal or none':(p<0.95 or none>0.02),'p<0.9':p<0.9,'none>0.05':none>0.05})
W=[r for r in rows if r['wrong']]; N=[r for r in rows if not r['wrong']]
for k in ['gap nearby (sig arms)','marginal or none','p<0.9','none>0.05']:
  fw=sum(r[k] for r in W); fn=sum(r[k] for r in N)
  print(f"{k:<28}{fw}/{len(W)}  {fn}/{len(N)}  wrong-if-fires {fw/max(1,fw+fn):.1%}  if-not {(len(W)-fw)/max(1,len(W)-fw+len(N)-fn):.1%}")
S=[r for r in rows if r['arm'] in ('kg-sig','test-sig-removed','test-sig-completed')]
SW=[r for r in S if r['wrong']]; SN=[r for r in S if not r['wrong']]
fw=sum(r['gap nearby (sig arms)'] for r in SW); fn=sum(r['gap nearby (sig arms)'] for r in SN)
print('within signature arms: gap nearby fires on',fw,'/',len(SW),'wrong and',fn,'/',len(SN),'others')

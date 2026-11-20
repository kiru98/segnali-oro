"""Backtest della STRATEGIA MEDIE 10/30 (4 ore) in euro, con rischio % del capitale.
Uso: python backtest_medie.py storico_gold_HOUR_4.csv"""
import numpy as np, pandas as pd
SPREAD=0.5; EURUSD=1.10; K=4.0
d=pd.read_csv(__import__("sys").argv[1] if len(__import__("sys").argv)>1 else "storico_gold_HOUR_4.csv",parse_dates=["time"]).set_index("time").sort_index(); d=d[~d.index.duplicated()]
pc=d.close.shift(1); A=pd.concat([d.high-d.low,(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1).ewm(alpha=1/14,adjust=False).mean().to_numpy()
O,H,L,C=(d[x].to_numpy() for x in ["open","high","low","close"]); t=d.index; n=len(d)
ef=d.close.ewm(span=10,adjust=False).mean().to_numpy(); es=d.close.ewm(span=30,adjust=False).mean().to_numpy()
up=np.r_[False,(ef[1:]>es[1:])&(ef[:-1]<=es[:-1])]; dn=np.r_[False,(ef[1:]<es[1:])&(ef[:-1]>=es[:-1])]
def sim(rischio, cap0=300.0):
    cap=cap0; rows=[]; i=31; picco=cap; ddmax=0
    while i<n-1:
        lato=1 if up[i-1] else (-1 if dn[i-1] else 0)
        if not lato: i+=1; continue
        e=O[i]; r=K*A[i-1]; sl=e-lato*r
        oz=np.floor(cap*rischio*EURUSD/r*100)/100
        if oz<0.01: i+=1; continue
        out=None
        for j in range(i,n):
            if lato==1 and L[j]<=sl: out=sl;break
            if lato==-1 and H[j]>=sl: out=sl;break
            if (lato==1 and ef[j]<es[j]) or (lato==-1 and ef[j]>es[j]): out=C[j];break
            sl=max(sl,C[j]-K*A[j]) if lato==1 else min(sl,C[j]+K*A[j])
        if out is None: out=C[-1]; j=n-1
        pnl=(lato*(out-e)-SPREAD)*oz/EURUSD; cap+=pnl
        picco=max(picco,cap); ddmax=max(ddmax,(picco-cap)/picco)
        rows.append((t[i],oz,pnl,cap)); i=j+1
    return pd.DataFrame(rows,columns=["time","oz","pnl","cap"]), ddmax
for risk in [0.02,0.03,0.04,0.05]:
    r,dd=sim(risk)
    print(f"== rischio {risk*100:.0f}% per operazione: 300 € -> {r.cap.iloc[-1]:.0f} € | calo max {dd*100:.0f}% | op {len(r)} | oz da {r.oz.min()} a {r.oz.max()}")
    cap_ini=300.0; out=[]
    for y,g in r.groupby(r.time.dt.year):
        fine=g.cap.iloc[-1]; out.append(f"{y}: {cap_ini:.0f}->{fine:.0f} € ({(fine/cap_ini-1)*100:+.0f}%)"); cap_ini=fine
    anni=(r.time.max()-r.time.min()).days/365.25
    print("   "+" | ".join(out)); print(f"   media {( (r.cap.iloc[-1]/300)**(1/anni)-1)*100:+.0f}% all'anno su {anni:.1f} anni")

import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
d=pd.read_csv("docs/predictions.csv")
rng=np.random.default_rng(1)
for t,g in d.groupby("target"):
    y=g.label.values; p=g.p_model.values; pb=g.p_baseline.values
    prev=y.mean()
    print(t, "n",len(y),"pos",y.sum(),"prev %.3f"%prev, "AUC %.4f base %.4f"%(roc_auc_score(y,p),roc_auc_score(y,pb)),
          "Brier %.4f base %.4f const_test %.4f"%(brier_score_loss(y,p),brier_score_loss(y,pb),prev*(1-prev)))
    print("  mean p %.3f  min %.3f max %.3f  q90 %.3f"%(p.mean(),p.min(),p.max(),np.quantile(p,.9)), "unique p", len(np.unique(p.round(6))))
    # calibration slope/intercept via logistic on logit(p)
    from sklearn.linear_model import LogisticRegression
    lp=np.log(np.clip(p,1e-6,1-1e-6)/(1-np.clip(p,1e-6,1-1e-6)))
    m=LogisticRegression(C=1e6).fit(lp.reshape(-1,1),y); print("  calib slope %.2f intercept %.2f"%(m.coef_[0][0],m.intercept_[0]))
    # Brier skill paired bootstrap vs test-prevalence constant and vs baseline
    bs=[];bd=[]
    for _ in range(2000):
        i=rng.integers(0,len(y),len(y)); yy=y[i]
        bs.append(1-brier_score_loss(yy,p[i])/max(yy.mean()*(1-yy.mean()),1e-9)); bd.append(brier_score_loss(yy,p[i])-brier_score_loss(yy,pb[i]))
    print("  BSS vs test-prev const %.3f CI [%.3f,%.3f]"%(1-brier_score_loss(y,p)/(prev*(1-prev)),*np.percentile(bs,[2.5,97.5])),
          " Brier diff model-base CI [%.4f,%.4f]"%tuple(np.percentile(bd,[2.5,97.5])))
    # top decile / top 20 lift
    o=np.argsort(-p)
    for k in (18,35,71):
        print("  top%d: %d pos (%.2f) lift %.2f"%(k,y[o[:k]].sum(),y[o[:k]].mean(),y[o[:k]].mean()/prev))
    # by year
    g=g.assign(yr=g.start_date.str[:4])
    print(g.groupby("yr").agg(n=("label","size"),pos=("label","sum"),mp=("p_model","mean")).T.to_string())
    # AUC by year halves
    for lab,mask in (("2018-19",g.yr<="2019"),("2020+",g.yr>="2020")):
        gg=g[mask]; 
        if gg.label.nunique()==2: print("  ",lab,"n",len(gg),"pos",gg.label.sum(),"AUC %.3f base %.3f"%(roc_auc_score(gg.label,gg.p_model),roc_auc_score(gg.label,gg.p_baseline)))

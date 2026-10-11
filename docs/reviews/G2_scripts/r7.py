import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss
t=pd.read_pickle("../../.data/G2-review/trials.pkl").set_index("nct_id")
p=pd.read_csv("docs/predictions.csv"); p=p[p.target=="accrual_fail"].set_index("nct_id").join(t[["sponsor_class"]])
tr=t[(t.split=="train")&t.a.notna()]
rate=tr.assign(ind=tr.sponsor_class=="INDUSTRY").groupby("ind").a.mean().astype(float); print("train rate by industry",rate.to_dict())
p["ind"]=p.sponsor_class=="INDUSTRY"; p["p1"]=p.ind.map(rate)
y=p.label.values; a=p.p_model.values; s=p.p1.values
rng=np.random.default_rng(0); D=[];B=[]
for _ in range(2000):
    i=rng.integers(0,len(y),len(y))
    if 0<y[i].sum()<len(i): D.append(roc_auc_score(y[i],a[i])-roc_auc_score(y[i],s[i])); B.append(brier_score_loss(y[i],a[i])-brier_score_loss(y[i],s[i]))
print("AUC model %.3f vs industry-flag-only %.3f; diff CI [%.3f,%.3f]"%(roc_auc_score(y,a),roc_auc_score(y,s),*np.percentile(D,[2.5,97.5])))
print("Brier model %.4f vs industry-flag-only %.4f; diff CI [%.4f,%.4f]"%(brier_score_loss(y,a),brier_score_loss(y,s),*np.percentile(B,[2.5,97.5])))
print("test pos by industry", p.groupby("ind").label.agg(["size","sum"]).to_dict())
# sensitivity / PPV at served threshold-free: what fraction of positives have p > test prevalence
print("positives with p<prev(0.096):",(p[p.label==1].p_model<0.096).sum(),"of",int(y.sum()))

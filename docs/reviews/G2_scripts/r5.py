import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_predict, StratifiedKFold
from lightgbm import LGBMClassifier
from clinova.model.train import load_bundle, predict_p, train, FIRST_VERSION_FEATURES
from clinova.model.features import load_dataset
b=load_bundle("v1.0"); df,_=load_dataset(ref=b["ref"])
F=b["feature_names"]; print("feats",len(F),F)
meta=pd.read_pickle("../../.data/G2-review/trials.pkl").set_index("nct_id")
df=df.join(meta[["prov","fv_status","lead_sponsor","status"]])
df["prec"]=df.prov.map(lambda x:(x or {}).get("start_date_precision","day"))
te=df[df.split=="test"]; tr=df[df.split=="train"]
pc=pd.read_csv("docs/predictions.csv")
for t,lab in (("accrual_fail","label_accrual_fail"),("shortfall","label_shortfall")):
    d=te[te[lab].notna()]; p=predict_p(b,t,d); q=pc[pc.target==t].set_index("nct_id").reindex(d.index)
    print(t,"now n",len(d),"vs csv",q.p_model.notna().sum(),"max|dp|",np.nanmax(np.abs(p-q.p_model.values)),"AUC now %.4f"%roc_auc_score(d[lab].astype(int),p))
# adversarial validation
X=df[F].astype(float); ys=(df.split=="test").astype(int).values
oof=cross_val_predict(LGBMClassifier(n_estimators=200,num_leaves=8,verbose=-1),X,ys,cv=StratifiedKFold(5,shuffle=True,random_state=0),method="predict_proba")[:,1]
print("adversarial AUC train-vs-test from 21 feats: %.3f"%roc_auc_score(ys,oof))
# single-feature AUCs on test accrual
d=te[te.label_accrual_fail.notna()]; y=d.label_accrual_fail.astype(int)
sa={f:roc_auc_score(y,d[f].fillna(d[f].median())) for f in F if d[f].notna().any()}
sa={f:max(v,1-v) for f,v in sa.items()}; print("top single-feature test AUC (accrual):",sorted(sa.items(),key=lambda x:-x[1])[:4])
cfg={"features":FIRST_VERSION_FEATURES,"use_sim":False,"bootstraps":0,"ref":b["ref"]}
subs={"full":tr,"day_precision_only":tr[tr.prec=="day"],"no_month_precision_relabel":tr[tr.prec!="month"],
      "prospective_fv_only":tr[tr.fv_status.isin(["NOT_YET_RECRUITING","RECRUITING","ENROLLING_BY_INVITATION"])],
      "start>=2010":tr[pd.to_datetime(tr.start_date)>="2010-01-01"]}
cols=[c for c in df.columns if c in F or c in ("split","start_date","label_accrual_fail","label_shortfall","label_amend")]
for name,s in subs.items():
    bb=train(s[cols],cfg); out=[name,"n",len(s)]
    for t,lab in (("accrual_fail","label_accrual_fail"),("shortfall","label_shortfall")):
        if t in bb["models"]:
            d=te[te[lab].notna()]; out+= [t, bb["n_train"][t], "AUC %.3f"%roc_auc_score(d[lab].astype(int),predict_p(bb,t,d))]
    print(*out)

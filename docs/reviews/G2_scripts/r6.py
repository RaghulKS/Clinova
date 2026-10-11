import pandas as pd, numpy as np, json
from sklearn.metrics import roc_auc_score, brier_score_loss
from clinova.common.db import get_conn
r=json.load(open("docs/replay.json"))
m=pd.DataFrame([{**{k:v for k,v in x.items() if k not in("retrained","frozen")},"aucR":x["retrained"]["auc"],"aucF":x["frozen"]["auc"]} for x in r["months"]])
print("months",len(m),"sum scored",m.n_scored.sum(),"sum pos",m.n_pos.sum(),"months with <=3 pos",(m.n_pos<=3).sum(),"sum new outcomes",m.n_new_outcomes.sum())
print("first-12 mean AUC F %.3f, last-24 mean AUC F %.3f"%(m.aucF[:12].mean(),m.aucF[24:].mean()))
with get_conn() as c:
    c.execute("SET TRANSACTION READ ONLY")
    cur=c.execute("select model_version,trial_id,p,y,month from prediction_scores where context='replay'")
    s=pd.DataFrame(cur.fetchall(),columns=[x.name for x in cur.description])
s["arm"]=s.model_version.str.split(":").str[1]; s["run"]=s.model_version.str.split(":").str[2]
print(s.groupby(["run","arm"]).size())
run=s.groupby("run").month.max().idxmax()
for arm,g in s[s.run.isin(s.run.unique())].groupby(["run","arm"]):
    g=g.sort_values("month"); f=g.drop_duplicates("trial_id")
    print(arm,"rows",len(g),"uniq",f.trial_id.nunique(),"pooled AUC %.3f Brier %.4f | first-score-per-trial AUC %.3f Brier %.4f pos %d"%(
        roc_auc_score(g.y,g.p),brier_score_loss(g.y,g.p),roc_auc_score(f.y,f.p),brier_score_loss(f.y,f.p),f.y.sum()))

import pandas as pd, numpy as np
df=pd.read_pickle("../../.data/G2-review/trials.pkl")
p=pd.read_csv("docs/predictions.csv")
df["src"]=df.prov.map(lambda x:(x or {}).get("stop_category_source","(gemini/T11)"))
df["prec"]=df.prov.map(lambda x:(x or {}).get("start_date_precision","day"))
df["lagd"]=(pd.to_datetime(df.first_version_date)-pd.to_datetime(df.start_date)).dt.days
pd.set_option("display.width",200)
a=df[df.a.notna()]
print("== accrual label source x split (n, pos)")
print(a.groupby(["split","src"]).a.agg(["size","sum"]).to_string())
print("== precision x split accrual"); print(a.groupby(["split","prec"]).a.agg(["size","sum","mean"]).to_string())
print("== fv_status (version-0 status) x split, accrual"); print(a.groupby(["split","fv_status"]).a.agg(["size","sum","mean"]).to_string())
print("== fv enrollment type x split"); print(a.groupby(["split","fv_enr_type"],dropna=False).a.agg(["size","sum","mean"]).to_string())
a=a.assign(late=a.lagd>365)
print("== registered >1y after start x split"); print(a.groupby(["split","late"]).a.agg(["size","sum","mean"]).to_string())
s=df[df.s.notna()]
print("== shortfall by split x status"); print(s.groupby(["split","status"]).s.agg(["size","sum","mean"]).to_string())
t=df[(df.split=="test")]
print("== test accrual positives by status/stop_category"); print(t[t.a==True].groupby(["status","stop_category"]).size().to_string())
print("== test accrual positives top sponsors"); print(t[t.a==True].lead_sponsor.value_counts().head(6).to_string())
print("== test accrual positives sponsor_class"); print(t.groupby("sponsor_class").a.agg(["size","sum"]).to_string())
print("== train accrual sponsor_class"); print(df[df.split=="train"].groupby("sponsor_class").a.agg(["size","sum"]).to_string())

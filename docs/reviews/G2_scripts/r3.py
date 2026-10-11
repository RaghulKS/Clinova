import pandas as pd, json
from clinova.common.db import get_conn
q="""select nct_id, split, status, stop_category, start_date, first_version_date, first_posted, completion_date,
 planned_enrollment, actual_enrollment, sponsor_class, lead_sponsor, label_accrual_fail a, label_shortfall s,
 current->'_clinova_provenance' prov,
 first_version->'designModule'->'enrollmentInfo'->>'type' fv_enr_type,
 first_version->'statusModule'->>'overallStatus' fv_status,
 first_version->'designModule'->'enrollmentInfo'->>'count' fv_enr,
 jsonb_array_length(coalesce(first_version->'contactsLocationsModule'->'locations','[]'::jsonb)) fv_nloc
 from trials where split in ('train','test')"""
with get_conn() as c:
    c.execute("SET TRANSACTION READ ONLY")
    cur=c.execute(q); cols=[x.name for x in cur.description]
    df=pd.DataFrame(cur.fetchall(),columns=cols)
df.to_pickle("../../.data/G2-review/trials.pkl")
print(df.shape); print(df.prov.dropna().head(3).tolist())

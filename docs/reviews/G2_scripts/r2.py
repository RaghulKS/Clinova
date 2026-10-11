import pandas as pd
from clinova.common.db import get_conn
with get_conn() as c:
    c.execute("SET TRANSACTION READ ONLY")
    cols=[r[0] for r in c.execute("select column_name from information_schema.columns where table_name='trials' order by ordinal_position").fetchall()]
    print(cols)
    print(c.execute("select column_name from information_schema.columns where table_name='outcomes'").fetchall())

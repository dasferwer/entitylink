import json
import os
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from entitylink.blocking import index, neighbors
from entitylink.db import connect, init


def main():
    base = os.environ["DATABASE_URL"]
    schema = "benchmark_" + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    os.environ["DATABASE_URL"] = make_conninfo(base, options=f"-c search_path={schema}")
    try:
        init()
        groups = {}
        with connect() as conn:
            for number in range(2000):
                group = number // 2
                identity = uuid.uuid4()
                row = conn.execute(
                    "INSERT INTO entities(id,source,external_id,name,address,phone,tax_id,cluster) VALUES (%s,'synthetic',%s,%s,'','',%s,%s) RETURNING *",
                    (
                        identity,
                        str(number),
                        "Организация " + uuid.uuid4().hex,
                        str(1000000000 + group),
                        identity,
                    ),
                ).fetchone()
                index(conn, row)
                groups.setdefault(group, []).append(row)
        started = time.monotonic()
        compared = 0
        found = 0
        truncated = 0
        with connect() as conn:
            for a, b in list(groups.values())[:100]:
                candidates, overflow = neighbors(conn, a, limit=50)
                compared += len(candidates)
                truncated += overflow
                found += b["id"] in {r["id"] for r in candidates}
        result = {
            "catalog_rows": 2000,
            "evaluated_pairs": 100,
            "blocking_recall": found / 100,
            "candidate_comparisons": compared,
            "exhaustive_comparisons_for_anchors": 100 * 1999,
            "truncated_anchors": truncated,
            "query_seconds": round(time.monotonic() - started, 3),
            "dataset": "Синтетические пары с общим идентификатором; не оценка качества на реальных контрагентах",
        }
        assert found == 100
        assert compared <= 5000
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        os.environ["DATABASE_URL"] = base
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    main()

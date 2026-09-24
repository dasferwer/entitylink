import os

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def init():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290029)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id uuid PRIMARY KEY, source text NOT NULL, external_id text NOT NULL,
                name text NOT NULL, address text NOT NULL, phone text NOT NULL,
                tax_id text NOT NULL, cluster uuid NOT NULL, UNIQUE(source, external_id));
            CREATE TABLE IF NOT EXISTS merges (
                id uuid PRIMARY KEY, target uuid NOT NULL, before jsonb NOT NULL,
                members jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
                undone_at timestamptz);
            ALTER TABLE entities ADD COLUMN IF NOT EXISTS indexed boolean NOT NULL DEFAULT false;
            CREATE TABLE IF NOT EXISTS blocks (
                key text NOT NULL,entity_id uuid NOT NULL REFERENCES entities(id),
                PRIMARY KEY(key,entity_id));
            CREATE INDEX IF NOT EXISTS entities_cluster ON entities(cluster);
            CREATE TABLE IF NOT EXISTS reviews (
                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                left_id uuid NOT NULL REFERENCES entities(id),right_id uuid NOT NULL REFERENCES entities(id),
                decision text NOT NULL CHECK(decision IN ('duplicate','distinct')),
                actor text NOT NULL,reason text NOT NULL,
                left_cluster uuid NOT NULL,right_cluster uuid NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now());
        """)
        from entitylink.blocking import index

        while True:
            rows = conn.execute("SELECT * FROM entities WHERE NOT indexed LIMIT 500").fetchall()
            if not rows:
                break
            for row in rows:
                index(conn, row)

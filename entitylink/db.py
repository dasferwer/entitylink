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
        """)

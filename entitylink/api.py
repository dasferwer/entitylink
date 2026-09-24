import os
import secrets
import uuid
from contextlib import asynccontextmanager
from itertools import combinations

from fastapi import Depends, FastAPI, Header, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from entitylink.db import connect, init
from entitylink.matching import compare


@asynccontextmanager
async def lifespan(app):
    init()
    yield


def authorize(x_api_key: str = Header(default="")):
    key = os.environ.get("API_KEY", "")
    if not key or not secrets.compare_digest(x_api_key, key):
        raise HTTPException(401, "Неверный API-ключ")


app = FastAPI(title="EntityLink", lifespan=lifespan, dependencies=[Depends(authorize)])


class Entity(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=300)
    address: str = Field(default="", max_length=500)
    phone: str = Field(default="", max_length=40)
    tax_id: str = Field(default="", pattern=r"^(?:[0-9]{10}|[0-9]{12})?$")


class Merge(BaseModel):
    left: uuid.UUID
    right: uuid.UUID


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/entities")
def add(entity: Entity):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290028)")
        existing = conn.execute(
            "SELECT * FROM entities WHERE source=%s AND external_id=%s",
            (entity.source, entity.external_id),
        ).fetchone()
        if existing:
            if any(existing[key] != value for key, value in entity.model_dump().items()):
                raise HTTPException(409, "Для этого ключа источника уже сохранены другие данные")
            return existing
        if conn.execute("SELECT count(*) AS n FROM entities").fetchone()["n"] >= 500:
            raise HTTPException(409, "Демонстрационный лимит: 500 записей")
        identity = uuid.uuid4()
        return conn.execute(
            """INSERT INTO entities VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                              RETURNING *""",
            (
                identity,
                entity.source,
                entity.external_id,
                entity.name,
                entity.address,
                entity.phone,
                entity.tax_id,
                identity,
            ),
        ).fetchone()


@app.get("/entities")
def entities():
    with connect() as conn:
        return conn.execute("SELECT * FROM entities ORDER BY source, external_id").fetchall()


@app.get("/candidates")
def candidates():
    result = []
    for left, right in combinations(entities(), 2):
        if left["cluster"] == right["cluster"]:
            continue
        match = compare(left, right)
        if match["decision"] == "review":
            result.append({"left": left["id"], "right": right["id"], **match})
    return sorted(result, key=lambda item: item["score"], reverse=True)


@app.post("/merges")
def merge(body: Merge):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290028)")
        pair = conn.execute(
            "SELECT * FROM entities WHERE id=ANY(%s)", ([body.left, body.right],)
        ).fetchall()
        if len(pair) != 2:
            raise HTTPException(422, "Нужны две разные существующие записи")
        clusters = list({row["cluster"] for row in pair})
        if len(clusters) == 1:
            return {"cluster": clusters[0], "already_merged": True}
        members = conn.execute(
            "SELECT * FROM entities WHERE cluster=ANY(%s) ORDER BY id", (clusters,)
        ).fetchall()
        tax_ids = {row["tax_id"] for row in members if row["tax_id"]}
        if len(tax_ids) > 1:
            raise HTTPException(409, "В группах есть конфликтующие идентификаторы")
        target = uuid.uuid4()
        merge_id = uuid.uuid4()
        before = {str(row["id"]): str(row["cluster"]) for row in members}
        conn.execute(
            "INSERT INTO merges (id,target,before,members) VALUES (%s,%s,%s,%s)",
            (merge_id, target, Jsonb(before), Jsonb(sorted(before))),
        )
        conn.execute("UPDATE entities SET cluster=%s WHERE cluster=ANY(%s)", (target, clusters))
        return {"id": merge_id, "cluster": target, "count": len(members)}


@app.post("/merges/{merge_id}/undo")
def undo(merge_id: uuid.UUID):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290028)")
        action = conn.execute("SELECT * FROM merges WHERE id=%s FOR UPDATE", (merge_id,)).fetchone()
        if action is None:
            raise HTTPException(404, "Объединение не найдено")
        if action["undone_at"]:
            return {"undone": True}
        current = conn.execute(
            "SELECT id FROM entities WHERE cluster=%s", (action["target"],)
        ).fetchall()
        if sorted(str(row["id"]) for row in current) != action["members"]:
            raise HTTPException(409, "Сначала отмените последующие объединения этой группы")
        for entity_id, cluster in action["before"].items():
            conn.execute(
                "UPDATE entities SET cluster=%s WHERE id=%s",
                (uuid.UUID(cluster), uuid.UUID(entity_id)),
            )
        conn.execute("UPDATE merges SET undone_at=now() WHERE id=%s", (merge_id,))
        return {"undone": True}


@app.get("/merges")
def audit():
    with connect() as conn:
        return conn.execute("SELECT * FROM merges ORDER BY created_at, id").fetchall()

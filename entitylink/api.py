import os
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from entitylink.blocking import index, neighbors
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
    left_cluster: uuid.UUID | None = None
    right_cluster: uuid.UUID | None = None


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
        identity = uuid.uuid4()
        saved = conn.execute(
            """INSERT INTO entities(id,source,external_id,name,address,phone,tax_id,cluster) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
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
        index(conn, saved)
        saved["indexed"] = True
        return saved


@app.get("/entities")
def entities(after: uuid.UUID | None = None, limit: int = Query(default=100, ge=1, le=500)):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM entities WHERE (%s::uuid IS NULL OR id>%s) ORDER BY id LIMIT %s",
            (after, after, limit),
        ).fetchall()


@app.get("/candidates")
def candidates(
    response: Response,
    after: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=100),
):
    result = []
    truncated = []
    seen = set()
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        anchors = conn.execute(
            "SELECT * FROM entities WHERE (%s::uuid IS NULL OR id>%s) AND (%s::uuid IS NULL OR id=%s) ORDER BY id LIMIT %s",
            (after, after, entity_id, entity_id, limit + 1),
        ).fetchall()
        if len(anchors) > limit:
            response.headers["X-Next-Cursor"] = str(anchors[limit - 1]["id"])
        for left in anchors[:limit]:
            matches, overflow = neighbors(conn, left)
            if overflow:
                truncated.append(str(left["id"]))
            for right in matches:
                pair = tuple(sorted((left["id"], right["id"])))
                if pair in seen:
                    continue
                seen.add(pair)
                match = compare(left, right)
                if match["decision"] == "review":
                    result.append(
                        {
                            "left": left["id"],
                            "right": right["id"],
                            "left_cluster": left["cluster"],
                            "right_cluster": right["cluster"],
                            **match,
                        }
                    )
    response.headers["X-Truncated-Entities"] = ",".join(truncated)
    return sorted(result, key=lambda item: item["score"], reverse=True)


class Review(BaseModel):
    left: uuid.UUID
    right: uuid.UUID
    left_cluster: uuid.UUID
    right_cluster: uuid.UUID
    decision: str = Field(pattern="^(duplicate|distinct)$")
    actor: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)


@app.post("/reviews")
def review(body: Review):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290028)")
        pair = conn.execute(
            "SELECT * FROM entities WHERE id=ANY(%s)", ([body.left, body.right],)
        ).fetchall()
        current = {r["id"]: r for r in pair}
        if len(pair) != 2:
            raise HTTPException(422, "Нужны две разные существующие записи")
        if (
            current[body.left]["cluster"] != body.left_cluster
            or current[body.right]["cluster"] != body.right_cluster
        ):
            raise HTTPException(409, "Группы изменились: обновите кандидата перед решением")
        if (
            body.decision == "duplicate"
            and compare(current[body.left], current[body.right])["decision"] == "conflict"
        ):
            raise HTTPException(409, "Идентификаторы противоречат решению")
        return conn.execute(
            "INSERT INTO reviews(left_id,right_id,decision,actor,reason,left_cluster,right_cluster) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                body.left,
                body.right,
                body.decision,
                body.actor,
                body.reason,
                body.left_cluster,
                body.right_cluster,
            ),
        ).fetchone()


@app.get("/reviews")
def reviews(after: int = 0, limit: int = Query(default=100, ge=1, le=500)):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM reviews WHERE id>%s ORDER BY id LIMIT %s", (after, limit)
        ).fetchall()


@app.post("/merges")
def merge(body: Merge):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(290028)")
        pair = conn.execute(
            "SELECT * FROM entities WHERE id=ANY(%s)", ([body.left, body.right],)
        ).fetchall()
        if len(pair) != 2:
            raise HTTPException(422, "Нужны две разные существующие записи")
        by_id = {row["id"]: row for row in pair}
        if (body.left_cluster is not None and by_id[body.left]["cluster"] != body.left_cluster) or (
            body.right_cluster is not None and by_id[body.right]["cluster"] != body.right_cluster
        ):
            raise HTTPException(409, "Состав группы изменился после просмотра кандидата")
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

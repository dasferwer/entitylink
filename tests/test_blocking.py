import uuid

from entitylink.blocking import index, keys, neighbors
from entitylink.db import connect
from tests.test_api import add


def test_blocking_keeps_typo_and_normalized_phone():
    a = {"name": "Северный ветер", "phone": "+7 (999) 111-22-33"}
    b = {"name": "Северный ветар", "phone": "8 999 1112233"}
    assert "phone:79991112233" in set(keys(a)) & set(keys(b))
    assert any(key.startswith("name:") for key in set(keys(a)) & set(keys(b)))


def test_catalog_larger_than_old_limit_and_bounded_candidates(client):
    with connect() as conn:
        for i in range(650):
            identity = uuid.uuid4()
            row = conn.execute(
                "INSERT INTO entities(id,source,external_id,name,address,phone,tax_id,cluster) VALUES (%s,'large',%s,'Общее название','','','',%s) RETURNING *",
                (identity, str(i), identity),
            ).fetchone()
            index(conn, row)
        matches, truncated = neighbors(conn, row, limit=25)
        assert len(matches) == 25
        assert truncated
    created = add(client, "additional", name="Другой контрагент")
    assert created["id"]
    first = client.get("/entities?limit=500").json()
    second = client.get("/entities", params={"limit": 500, "after": first[-1]["id"]}).json()
    assert len(first) + len(second) == 651
    assert not {r["id"] for r in first} & {r["id"] for r in second}
    page = client.get("/candidates", params={"entity_id": str(row["id"])})
    assert page.headers["X-Truncated-Entities"] == str(row["id"])


def test_exact_identifier_has_priority_over_common_name(client):
    anchor = add(client, 1, name="Компания", tax_id="1234567890")
    for i in range(2, 12):
        add(client, i, name="Компания")
    exact = add(client, 12, name="Совершенно другое", tax_id="1234567890")
    with connect() as conn:
        row = conn.execute("SELECT * FROM entities WHERE id=%s", (anchor["id"],)).fetchone()
        found, _ = neighbors(conn, row, limit=1)
    assert str(found[0]["id"]) == exact["id"]


def test_review_rejects_stale_cluster_and_records_reason(client):
    a, b, c = [add(client, i) for i in range(3)]
    body = {
        "left": a["id"],
        "right": b["id"],
        "left_cluster": a["cluster"],
        "right_cluster": b["cluster"],
        "decision": "distinct",
        "actor": "reviewer-1",
        "reason": "Разные юридические лица",
    }
    response = client.post("/reviews", json=body)
    assert response.status_code == 200
    assert client.get("/reviews").json()[0]["reason"] == body["reason"]
    client.post("/merges", json={"left": a["id"], "right": c["id"]})
    assert client.post("/reviews", json=body).status_code == 409

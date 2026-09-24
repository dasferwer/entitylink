import uuid


def add(client, number, **fields):
    payload = {"source": "demo", "external_id": str(number), "name": "Пример", **fields}
    response = client.post("/entities", json=payload)
    assert response.status_code == 200
    return response.json()


def test_repeat_and_conflict(client):
    first = add(client, 1)
    assert add(client, 1) == first
    assert (
        client.post(
            "/entities", json={"source": "demo", "external_id": "1", "name": "Другое"}
        ).status_code
        == 409
    )
    assert client.get("/health", headers={"X-API-Key": "wrong"}).status_code == 401


def test_merge_undo_and_audit(client):
    a, b = add(client, 1, tax_id="1234567890"), add(client, 2, tax_id="1234567890")
    assert len(client.get("/candidates").json()) == 1
    merged = client.post("/merges", json={"left": a["id"], "right": b["id"]}).json()
    assert len({r["cluster"] for r in client.get("/entities").json()}) == 1
    assert client.get("/candidates").json() == []
    assert client.post(f"/merges/{merged['id']}/undo").status_code == 200
    assert client.post(f"/merges/{merged['id']}/undo").status_code == 200
    assert len({r["cluster"] for r in client.get("/entities").json()}) == 2
    assert client.get("/merges").json()[0]["undone_at"] is not None


def test_conflicting_cluster_identifier_cannot_be_hidden(client):
    a = add(client, 1, tax_id="1234567890")
    b = add(client, 2)
    c = add(client, 3, tax_id="9876543210")
    assert client.post("/merges", json={"left": a["id"], "right": b["id"]}).status_code == 200
    assert client.post("/merges", json={"left": b["id"], "right": c["id"]}).status_code == 409


def test_undo_requires_reverse_order(client):
    a, b, c = [add(client, n) for n in range(3)]
    first = client.post("/merges", json={"left": a["id"], "right": b["id"]}).json()
    second = client.post("/merges", json={"left": b["id"], "right": c["id"]}).json()
    assert client.post(f"/merges/{first['id']}/undo").status_code == 409
    assert client.post(f"/merges/{second['id']}/undo").status_code == 200
    assert client.post(f"/merges/{first['id']}/undo").status_code == 200
    assert len({r["cluster"] for r in client.get("/entities").json()}) == 3


def test_invalid_pairs(client):
    a = add(client, 1)
    assert client.post("/merges", json={"left": a["id"], "right": a["id"]}).status_code == 422
    assert client.post(f"/merges/{uuid.uuid4()}/undo").status_code == 404

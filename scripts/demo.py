import json
import os
import uuid
from urllib.request import Request, urlopen

base = os.environ.get("API_URL", "http://localhost:8089")
key = os.environ.get("API_KEY", "local-demo-key")


def call(path, data=None):
    request = Request(
        base + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


source = "demo-" + uuid.uuid4().hex[:8]
a, b = [
    call(
        "/entities", {"source": source, "external_id": str(i), "name": name, "tax_id": "1234567890"}
    )
    for i, name in enumerate(["Северный ветер", "Северный ветар"])
]
merged = call("/merges", {"left": a["id"], "right": b["id"]})
assert merged["count"] == 2
assert call(f"/merges/{merged['id']}/undo", {}) == {"undone": True}
print("Объединение и отмена выполнены; исходные записи сохранены.")

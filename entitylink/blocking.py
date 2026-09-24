from entitylink.matching import normalize, phone

VERSION = "trigram-v1"


def keys(entity):
    """Широкий отбор кандидатов отделён от строгого решения о совпадении."""
    result = set()
    if entity.get("tax_id"):
        result.add("tax:" + entity["tax_id"])
    number = phone(entity.get("phone"))
    if number:
        result.add("phone:" + number)
    name = normalize(entity["name"])
    for word in name.split():
        if len(word) >= 3:
            result.update("name:" + word[i : i + 3] for i in range(len(word) - 2))
        elif word:
            result.add("short:" + word)
    return sorted(result)


def index(conn, entity):
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO blocks(key,entity_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            [(key, entity["id"]) for key in keys(entity)],
        )
    conn.execute("UPDATE entities SET indexed=true WHERE id=%s", (entity["id"],))


def neighbors(conn, entity, limit=500):
    # Общие триграммы дают широкий recall; точный идентификатор имеет приоритет.
    rows = conn.execute(
        """SELECT e.*,b.exact,b.overlap FROM (
        SELECT entity_id,count(*) AS overlap,max(CASE WHEN key LIKE 'tax:%%' THEN 2
            WHEN key LIKE 'phone:%%' THEN 1 ELSE 0 END) AS exact
        FROM blocks WHERE key=ANY(%s) AND entity_id<>%s GROUP BY entity_id
        ) b JOIN entities e ON e.id=b.entity_id WHERE e.cluster<>%s
        ORDER BY b.exact DESC,b.overlap DESC,e.id LIMIT %s""",
        (keys(entity), entity["id"], entity["cluster"], limit + 1),
    ).fetchall()
    return rows[:limit], len(rows) > limit

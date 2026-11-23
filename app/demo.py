"""Local Keitaro subset. The production gateway uses the same request shapes."""
import json
import httpx


def put(db, kind, row):
    db.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?)", (kind, row["id"], json.dumps(row)))
    return row


def flows(campaign, country, offer):
    return [
        dict(campaign_id=campaign, name="Google · " + country, type="regular", position=1,
             state="active", schema="redirect", action_type="http", action_payload="https://www.google.com/",
             collect_clicks=True, filters=[dict(name="country", mode="accept", payload=[country])]),
        dict(campaign_id=campaign, name="Офферы", type="default", position=2, state="active",
             schema="landings", action_type="http", collect_clicks=True,
             offers=[dict(offer_id=offer, share=100, state="active")]),
    ]


def seed(store):
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM objects LIMIT 1").fetchone():
            return
        for kind, names in {
            "domains": ["demo.example", "travel.example"], "groups": ["Travel", "Retail"],
            "traffic_sources": ["Поиск", "Рассылка"], "offers": ["Билеты", "Отели", "Трансфер", "Экскурсии", "Страховка"]
        }.items():
            for i, name in enumerate(names, 1):
                put(db, kind, dict(id=i, name=name))
        for i, (name, country) in enumerate([("Летние поездки", "DE"), ("Выходные в городе", "AU")], 1):
            put(db, "campaigns", dict(id=i, name=name, alias=f"demo-{i}", type="position",
                domain_id=i, group_id=str(i), traffic_source_id=i))
            geo, offers = flows(i, country, i)
            if i == 1:
                offers["offers"] = [dict(offer_id=j, share=s, state="active") for j,s in [(1,34),(2,33),(3,33)]]
            put(db, "streams", dict(geo, id=i*2-1))
            put(db, "streams", dict(offers, id=i*2))


def transport(store):
    def handle(req):
        parts = req.url.path.strip("/").split("/")
        kind = parts[0]
        with store.transaction() as db:
            if len(parts) == 3 and kind == "campaigns" and parts[2] == "streams":
                rows = db.execute("SELECT body FROM objects WHERE kind='streams' ORDER BY id")
                return httpx.Response(200, json=[v for (s,) in rows if (v := json.loads(s))["campaign_id"] == int(parts[1])])
            if req.method == "GET" and len(parts) == 1:
                rows = db.execute("SELECT body FROM objects WHERE kind=? ORDER BY id", (kind,))
                return httpx.Response(200, json=[json.loads(s) for (s,) in rows])
            if req.method == "POST" and kind in ("campaigns","streams"):
                data = json.loads(req.content)
                ident = db.execute("SELECT coalesce(max(id),0)+1 FROM objects WHERE kind=?", (kind,)).fetchone()[0]
                return httpx.Response(200, json=put(db, kind, dict(data, id=ident)))
            if len(parts) == 2:
                row = db.execute("SELECT body FROM objects WHERE kind=? AND id=?", (kind, parts[1])).fetchone()
                if row:
                    data = json.loads(row[0])
                    if req.method == "PUT" and kind == "streams":
                        data.update(json.loads(req.content))
                        put(db, kind, data)
                    return httpx.Response(200, json=data)
        return httpx.Response(404, json={"error":"Запись отсутствует"})
    return httpx.MockTransport(handle)

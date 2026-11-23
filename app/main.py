import asyncio
import json
from contextlib import asynccontextmanager
from random import choices

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import ROOT, Settings
from app.campaigns import COUNTRIES, Choice, router
from app.demo import seed, transport
from app.editor import Edit, EditResult, Stream, preview, version
from app.storage import SQLConnection, Store


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings
    engine = create_engine(settings.active_database_url)
    app.state.sessions = sessionmaker(engine)
    try:
        store = Store(lambda: SQLConnection(engine))
        app.state.store = store
        if settings.adrobot_mode == "demo":
            seed(store)
            options = dict(base_url="http://demo.invalid/", transport=transport(store), trust_env=False)
        else:
            key = settings.keitaro_api_key.get_secret_value()
            if not key:
                raise RuntimeError("Set KEITARO_API_KEY for live mode")
            options = dict(base_url=str(settings.keitaro_api_url).rstrip("/") + "/",
                           headers={"Api-Key": key, "Accept": "application/json"})
        async with httpx.AsyncClient(timeout=10.0, **options) as client:
            app.state.keitaro = client
            yield
    finally:
        engine.dispose()


def build_app():
    app = FastAPI(title="Adrobot", lifespan=lifespan)
    app.include_router(router)
    edit_lock = asyncio.Lock()

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": app.state.settings.adrobot_mode}

    async def kt(method, path, **kwargs):
        try:
            resp = await app.state.keitaro.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise HTTPException(code if code in (403,404,422) else 502, f"Keitaro: {method} {path}, HTTP {code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Нет подтверждённого ответа Keitaro") from exc

    @app.get("/api/campaigns", response_model=list[Choice])
    async def listing():
        return [{"id":int(r["id"]),"name":r["name"]} for r in await kt("GET","campaigns")]

    @app.get("/api/demo/campaigns/{cid}/route")
    async def demo_route(cid: int, country_code: str):
        if app.state.settings.adrobot_mode != "demo":
            raise HTTPException(404, "Доступно только в деморежиме")
        country = country_code.strip().upper()
        if country not in {r.code for r in COUNTRIES}:
            raise HTTPException(422, "Неизвестная страна")
        rows = sorted(await kt("GET", f"campaigns/{cid}/streams"), key=lambda r:r["position"])
        for row in rows:
            if row.get("state") != "active":
                continue
            if any(country not in f["payload"] for f in row.get("filters", [])):
                continue
            if row["schema"] == "redirect":
                return dict(stream_id=row["id"], destination=row["action_payload"])
            active = [r for r in row.get("offers",[]) if r["state"]=="active" and r["share"]>0]
            if active:
                offer = choices(active, weights=[r["share"] for r in active])[0]
                return dict(stream_id=row["id"], offer_id=offer["offer_id"])
        raise HTTPException(404, "Подходящий поток отсутствует")

    @app.get("/api/campaigns/{cid}/streams", response_model=list[Stream])
    async def streams(cid: int):
        rows = await kt("GET", f"campaigns/{cid}/streams")
        with app.state.store.transaction() as db:
            pins = {sid:json.loads(blob) for sid,blob in db.execute(
                "SELECT stream_id, pinned FROM preferences WHERE campaign_id=?", (cid,))}
        return [dict(id=r["id"],name=r["name"],position=r.get("position"),editable=r.get("schema")=="landings",
                     pinned={str(o["offer_id"]):int(o["share"]) for o in (r.get("offers") or [])
                             if str(o["offer_id"]) in pins.get(int(r["id"]), {}) and o.get("state")!="disabled"},
                     offers=r.get("offers") or [], revision=version(r.get("offers") or [], pins.get(int(r["id"]), {}))) for r in rows]

    async def edit_flow(cid,sid,edit,save):
        row = await kt("GET",f"streams/{sid}")
        if int(row["campaign_id"])!=cid or row.get("schema")!="landings":
            raise HTTPException(404,"Поток офферов не найден")
        rows = row.get("offers") or []
        catalog = {int(r["id"]) for r in await kt("GET","offers") if r.get("state") not in ("disabled","deleted")}
        with app.state.store.transaction() as db:
            record = db.execute("SELECT pinned FROM preferences WHERE campaign_id=? AND stream_id=?", (cid, sid)).fetchone()
        pins = json.loads(record[0]) if record else {}
        updated = preview(rows, edit, catalog, pins)
        if save:
            await kt("PUT",f"streams/{sid}",json={"offers":updated})
            updated = (await kt("GET",f"streams/{sid}"))["offers"]
            with app.state.store.transaction() as db:
                db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?,?)", (cid,sid,json.dumps(edit.pinned)))
        return dict(offers=updated, revision=version(updated, edit.pinned) if save else version(rows, pins))

    @app.post("/api/campaigns/{cid}/streams/{sid}/preview", response_model=EditResult)
    async def draft(cid:int,sid:int,edit:Edit):
        return await edit_flow(cid,sid,edit,False)

    @app.put("/api/campaigns/{cid}/streams/{sid}/offers", response_model=EditResult)
    async def save(cid:int,sid:int,edit:Edit):
        async with edit_lock:
            return await edit_flow(cid,sid,edit,True)

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app


app = build_app()

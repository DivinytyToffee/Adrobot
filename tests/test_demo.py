import os
from unittest.mock import patch
from alembic import command
from alembic.config import Config
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import build_app
class DemoTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test.sqlite3"
        env = patch.dict(os.environ, {"ADROBOT_MODE": "demo", "DEMO_DATABASE_URL": "sqlite:///" + self.path.as_posix()})
        env.start()
        self.addCleanup(env.stop)
        command.upgrade(Config("alembic.ini"), "head")
        self.client=TestClient(build_app())
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)

    def test_create_and_route(self):
        client=self.client
        opts=client.get("/api/campaign-options").json()
        self.assertEqual(len(opts["offers"]),5)
        self.assertEqual(len(opts["countries"]),249)
        data=dict(name="Check",country_code="de",offer_id=4)
        headers={"Idempotency-Key":str(uuid4())}
        r=client.post("/api/campaigns",json=data,headers=headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(client.post("/api/campaigns",json=data,headers=headers).json(),r.json())
        self.assertEqual(client.post("/api/campaigns",json=dict(data,name="other"),headers=headers).status_code,409)
        cid=r.json()["keitaro_campaign_id"]
        path=f"/api/demo/campaigns/{cid}/route"
        self.assertEqual(client.get(path,params={"country_code":"DE"}).json()["destination"],"https://www.google.com/")
        self.assertEqual(client.get(path,params={"country_code":"AU"}).json()["offer_id"],4)
        self.assertEqual(client.post("/api/campaigns",json=dict(data,offer_id=500),headers={"Idempotency-Key":str(uuid4())}).status_code,422)

    def test_edit_and_restart(self):
        path="/api/campaigns/1/streams"
        rows=self.client.get(path).json()
        geo,flow=rows
        target=path+f'/{flow["id"]}'
        draft=dict(active_ids=[1,2,3,4],revision=flow["revision"],pinned={})
        def shares(result):
            return [row["share"] for row in result["offers"]]
        self.assertEqual(shares(self.client.post(target+"/preview",json=draft).json()),[25,25,25,25])
        self.assertEqual(shares(self.client.get(path).json()[1]),[34,33,33])
        draft.update(active_ids=[1,3,4],pinned={"1":50})
        saved=self.client.put(target+"/offers",json=draft).json()
        self.assertEqual(shares(saved),[50,0,25,25])
        self.assertEqual(self.client.put(target+"/offers",json=draft).status_code,409)
        with TestClient(build_app()) as restarted:
            geo2,flow2=restarted.get(path).json()
            self.assertEqual(geo,geo2)
            self.assertEqual(shares(flow2),[50,0,25,25])
            draft.update(revision=flow2["revision"],active_ids=[1,2,3,4],pinned={})
            self.assertEqual(shares(restarted.put(target+"/offers",json=draft).json()),[25]*4)
        self.assertEqual(self.client.put(path+f'/{geo["id"]}/offers',json=draft).status_code,404)

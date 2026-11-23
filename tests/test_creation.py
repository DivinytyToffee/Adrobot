import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.main import app


class CreationTest(unittest.TestCase):
    def test_resume_after_lost_stream_response(self):
        streams = []
        posts = []
        def keitaro(request):
            path = request.url.path
            if request.method == "GET":
                return httpx.Response(200, json=streams if path.endswith("/streams") else [])
            data = json.loads(request.content)
            posts.append(data)
            if path.endswith("/campaigns"):
                return httpx.Response(200, json={"id": 15})
            streams.append(dict(data, id=len(streams) + 1))
            if len(streams) == 2:
                raise httpx.ReadTimeout("response lost")
            return httpx.Response(200, json=streams[-1])

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "DATABASE_URL": "sqlite:///" + Path(folder, "test.db").as_posix(),
            "KEITARO_API_KEY": "test",
            "ADROBOT_MODE": "live",
            "ALL_PROXY": "", "HTTP_PROXY": "", "HTTPS_PROXY": "",
        }):
            command.upgrade(Config("alembic.ini"), "head")
            with TestClient(app) as client:
                mock_client = httpx.AsyncClient(base_url="https://test.invalid/", transport=httpx.MockTransport(keitaro))
                with patch.object(app.state, "keitaro", mock_client):
                    key = str(uuid4())
                    headers = {"Idempotency-Key": key}
                    data = dict(name="Example", country_code="de", offer_id=10, domain_id=1,
                                group_id=2, traffic_source_id=3)
                    self.assertEqual(client.get("/").status_code, 200)
                    self.assertEqual(client.get("/api/campaign-options").status_code, 200)
                    def submit():
                        return client.post("/api/campaigns", headers=headers, json=data)
                    self.assertEqual(submit().status_code, 502)
                    url = "/api/campaign-creations/" + key
                    self.assertEqual(client.get(url).json()["status"], "sending_offer")
                    result = submit().json()
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(result["keitaro_campaign_id"], 15)
                    self.assertEqual(result["offer_stream_id"], 2)
                    self.assertEqual(submit().json(), result)
                    self.assertEqual(len(posts), 3)
                    self.assertEqual(posts[1]["filters"][0]["payload"], ["DE"])
                    self.assertEqual(posts[2]["offers"][0]["offer_id"], 10)
                    self.assertEqual(posts[2]["action_type"], "http")
                    data["offer_id"] = 11
                    self.assertEqual(submit().status_code, 409)
                client.portal.call(mock_client.aclose)

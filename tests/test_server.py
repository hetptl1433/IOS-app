"""The web app's HTTP API, against a server on a free local port."""
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from fakes import FakeComEd
from sparc import lab
from sparc.backtest import PROFILES
from sparc.history import backfill
from sparc.server import make_server
from sparc.store import Store


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.source.cleanup)
        store = Store(Path(cls.source.name) / "sparc.db")
        backfill(store, date(2026, 1, 1), date(2026, 2, 28), FakeComEd(), log=lambda message: None,
                 today=date(2026, 3, 15))
        store.close()

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        shutil.copyfile(Path(self.source.name) / "sparc.db", root / "sparc.db")
        self.fake = FakeComEd()
        self.gate = None  # set to an Event to hold every ComEd request until it is set
        self.server = make_server(lab.Folders(root), port=0, get=self.comed, quiet=True)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def comed(self, url):
        if self.gate:
            self.gate.wait(10)
        return self.fake(url)

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        self.addCleanup(connection.close)
        sent = {"X-Sparc": "1", **(headers or {})}
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        if data is not None:
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=data, headers=sent)
        response = connection.getresponse()
        content = response.read()
        kind = response.getheader("Content-Type", "")
        return response.status, json.loads(content) if kind.startswith("application/json") else content, response

    def wait(self, job):
        for _ in range(600):
            status, job, _ = self.request("GET", f"/api/jobs/{job['id']}")
            self.assertEqual(status, 200)
            if job["state"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def settings(self, **changes):
        return {"start": "2026-02-05", "end": "2026-02-09", "calibrate_days": 30, "tariffs": ["standard"],
                "strategies": ["day-ahead", "cap-3", "walk:ridge"],
                "profiles": [lab.profile_json(PROFILES[0])], **changes}

    def test_pages_and_state(self):
        status, page, response = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>SPARC charging lab</title>", page)
        self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
        for path in ("/app.js", "/charts.js", "/style.css"):
            self.assertEqual(self.request("GET", path)[0], 200, path)
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual((state["data"]["first"], state["data"]["last"]), ("2026-01-01", "2026-02-28"))
        self.assertIn("walk:ridge", [c["id"] for c in state["choices"]])
        self.assertEqual(state["runs"], [])
        self.assertEqual(self.request("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/state")[0], 405)

    def test_changes_need_the_header_and_a_local_host(self):
        status, body, _ = self.request("POST", "/api/backtest", self.settings(), headers={"X-Sparc": ""})
        self.assertEqual(status, 403)
        self.assertIn("X-Sparc", body["error"])
        status, _, _ = self.request("GET", "/api/state", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/state", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_bad_requests_are_explained(self):
        status, body, _ = self.request("POST", "/api/backtest", self.settings(strategies=["teleport"]))
        self.assertEqual((status, body["error"]), (400, "Unknown strategy: teleport"))
        status, body, _ = self.request("POST", "/api/backtest", raw=b"{not json")
        self.assertEqual(status, 400)
        status, body, _ = self.request("POST", "/api/backtest", raw=b"[1, 2]")
        self.assertEqual((status, body["error"]), (400, "Send a JSON object."))
        status, body, _ = self.request("POST", "/api/fetch", {"start": "2026-03-02", "end": "2026-03-01"})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("GET", "/api/runs/20260101-000000-abcd")[0], 404)
        self.assertEqual(self.request("GET", "/api/runs/..%2Fsparc")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/models/ridge-20260101-20260131-abcdef")[0], 404)
        self.assertEqual(self.request("GET", "/api/jobs/0123456789ab")[0], 404)

    def test_backtest_job_saves_a_run(self):
        status, job, _ = self.request("POST", "/api/backtest", self.settings())
        self.assertEqual((status, job["kind"]), (202, "backtest"))
        job = self.wait(job)
        self.assertEqual(job["state"], "done", job["error"])
        ident = job["result"]["run"]
        status, run, _ = self.request("GET", f"/api/runs/{ident}")
        self.assertEqual(status, 200)
        self.assertEqual(run["nights"], {"Commuter": 5})
        self.assertIn("Forecast: Ridge, refit monthly", run["forecasts"])
        status, content, response = self.request("GET", f"/api/runs/{ident}/nights.csv")
        self.assertEqual(status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/csv"))
        self.assertEqual(len(content.decode().splitlines()), 1 + 5 * 5)
        self.assertEqual([r["id"] for r in self.request("GET", "/api/runs")[1]], [ident])
        self.assertEqual(self.request("DELETE", f"/api/runs/{ident}")[0], 200)
        self.assertEqual(self.request("GET", f"/api/runs/{ident}")[0], 404)

    def test_training_job_saves_a_model(self):
        status, job, _ = self.request("POST", "/api/train", {"kind": "ridge", "train_start": "2026-01-02",
                                                            "train_end": "2026-01-31", "test_start": "2026-02-02",
                                                            "test_end": "2026-02-26", "name": "January"})
        self.assertEqual(status, 202)
        job = self.wait(job)
        self.assertEqual(job["state"], "done", job["error"])
        (model,) = self.request("GET", "/api/models")[1]
        self.assertEqual((model["name"], model["usable_from"]), ("January", "2026-02-02"))
        self.assertGreater(model["metrics"]["test"]["samples"], 1000)
        self.assertEqual(self.request("DELETE", f"/api/models/{model['id']}")[0], 200)
        self.assertEqual(self.request("GET", "/api/models")[1], [])

    def test_fetch_job_runs_alone_and_can_be_cancelled(self):
        self.gate = threading.Event()
        status, job, _ = self.request("POST", "/api/fetch", {"start": "2026-03-01", "end": "2026-03-04"})
        self.assertEqual(status, 202)
        status, body, _ = self.request("POST", "/api/backtest", self.settings())
        self.assertEqual(status, 409)
        self.assertIn("Wait for the current job", body["error"])
        self.assertEqual(self.request("POST", f"/api/jobs/{job['id']}/cancel")[1]["cancelling"], True)
        self.gate.set()
        self.assertEqual(self.wait(job)["state"], "cancelled")

    def test_fetch_job_adds_prices(self):
        status, job, _ = self.request("POST", "/api/fetch", {"start": "2026-03-01", "end": "2026-03-03"})
        self.assertEqual(status, 202)
        job = self.wait(job)
        self.assertEqual((job["state"], job["result"]), ("done", {"failed": []}))
        self.assertEqual(self.request("GET", "/api/state")[1]["data"]["last"], "2026-03-03")


if __name__ == "__main__":
    unittest.main()

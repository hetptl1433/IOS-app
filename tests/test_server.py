import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sparc_lab.server import DEFAULTS, LocalServer, State


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.state = State(Path(cls.temp.name))
        cls.server = LocalServer(("127.0.0.1", 0), cls.state)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.state.close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        payload = json.dumps(body) if body is not None else None
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def test_health_and_bootstrap(self):
        status, _, content = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["mode"], "simulation-only")
        status, _, content = self.request("GET", "/api/bootstrap")
        body = json.loads(content)
        self.assertEqual(status, 200)
        self.assertTrue(body["datasets"][0]["is_synthetic"])
        self.assertNotIn("points", body["datasets"][0])
        self.assertGreater(len(body["models"]), 2)

    def test_rejects_cross_origin_mutation(self):
        status, _, content = self.request("POST", "/api/runs", {}, {"Origin": "https://unrelated.example"})
        self.assertEqual(status, 403)
        self.assertIn("own page", json.loads(content)["error"])

    def test_rejects_nonfinite_json(self):
        status, _, content = self.request("POST", "/api/runs", {"bad": float("nan")})
        self.assertEqual(status, 400)
        self.assertIn("Non-finite", json.loads(content)["error"])

    def test_unknown_dataset_and_model_return_useful_errors(self):
        status, _, _ = self.request("GET", "/api/datasets/unknown/preview")
        self.assertEqual(status, 404)
        dataset_id = self.state.summaries()[0]["id"]
        status, _, content = self.request("POST", "/api/runs", {"dataset_id": dataset_id, "config": DEFAULTS, "model_ids": ["invented-model"]})
        self.assertEqual(status, 400)
        self.assertIn("Unknown model", json.loads(content)["error"])

    def test_path_traversal_is_not_served(self):
        status, _, _ = self.request("GET", "/%2e%2e/server.py")
        self.assertEqual(status, 404)

    def test_completed_run_is_persisted_and_exported(self):
        dataset_id = self.state.summaries()[0]["id"]
        status, _, content = self.request("POST", "/api/runs", {"dataset_id": dataset_id, "config": DEFAULTS, "model_ids": ["seasonal-naive"]})
        self.assertEqual(status, 202)
        identifier = json.loads(content)["job_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _, _, content = self.request("GET", f"/api/jobs/{identifier}")
            job = json.loads(content)
            if job["status"] in ("complete", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "complete", job.get("error"))
        result = job["result"]
        status, _, content = self.request("GET", f"/api/runs/{result['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["id"], result["id"])
        status, headers, content = self.request("GET", f"/api/runs/{result['id']}/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn(b"final_soc", content)


if __name__ == "__main__":
    unittest.main()

"""Loopback-only by default; all experiments run locally, without vehicle commands."""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import mimetypes
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from . import __version__
from .data import (
    dataset_summary,
    demo_dataset,
    fetch_comed_dataset,
    hourly_records,
    parse_csv_dataset,
)
from .models import get_model_catalog, list_artifacts, train_model
from .simulator import run_simulation

LOG = logging.getLogger("sparc")
WEB = Path(__file__).with_name("web")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
MAX_BODY = 8 * 1024 * 1024
DEFAULTS = {
    "arrival": "2026-10-14T18:00:00-05:00",
    "departure": "2026-10-15T07:00:00-05:00",
    "battery_kwh": 75,
    "initial_soc": 35,
    "target_soc": 80,
    "charger_kw": 7.2,
    "efficiency": 0.90,
    "delivery_cents": 0,
    "max_price_cents": None,
    "price_policy": "ready",
    "reserve_minutes": 20,
    "min_session_minutes": 15,
    "replan_minutes": 5,
    "forecast_context_hours": 336,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class State:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.artifacts = self.root / "artifacts"
        for name in ("datasets", "runs", "jobs", "artifacts"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sparc-job")
        demo = demo_dataset()
        self.datasets: dict[str, dict] = {demo["id"]: demo}
        self.jobs: dict[str, dict] = {}
        self.hourly: dict[str, list] = {}
        self._load_persisted()

    def _load_persisted(self) -> None:
        for path in (self.root / "datasets").glob("*.json"):
            try:
                dataset = json.loads(path.read_text(encoding="utf-8"))
                if SAFE_ID.fullmatch(dataset["id"]):
                    self.datasets[dataset["id"]] = dataset
            except (ValueError, KeyError, OSError):
                LOG.warning("Skipped unreadable dataset %s", path.name)
        for path in (self.root / "jobs").glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job.get("status") in ("queued", "running"):
                    job.update(status="failed", error="The server restarted before this experiment completed.")
                    atomic_json(path, job)
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                LOG.warning("Skipped unreadable job %s", path.name)

    def dataset(self, identifier: str) -> dict:
        with self.lock:
            if identifier not in self.datasets:
                raise LookupError("Dataset not found.")
            return self.datasets[identifier]

    def hourly_for(self, identifier: str) -> list:
        with self.lock:
            if identifier not in self.hourly:
                self.hourly[identifier] = hourly_records(self.dataset(identifier))
            return self.hourly[identifier]

    def summaries(self) -> list:
        with self.lock:
            return [dataset_summary(item) for item in self.datasets.values()]

    def register(self, dataset: dict) -> dict:
        identifier = dataset.get("id", "")
        if not SAFE_ID.fullmatch(identifier):
            identifier = "data-" + uuid.uuid4().hex[:12]
            dataset["id"] = identifier
        with self.lock:
            if identifier in self.datasets:
                identifier += "-" + uuid.uuid4().hex[:8]
                dataset["id"] = identifier
            atomic_json(self.root / "datasets" / f"{identifier}.json", dataset)
            self.datasets[identifier] = dataset
            return dataset_summary(dataset)

    def runs(self) -> list:
        results = []
        for path in sorted((self.root / "runs").glob("*.json"), reverse=True):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
                results.append({key: run.get(key) for key in ("id", "created_at", "dataset", "config", "summary", "warnings")})
            except (ValueError, OSError):
                continue
        return sorted(results, key=lambda row: row.get("created_at") or "", reverse=True)[:100]

    def run(self, identifier: str) -> dict:
        if not SAFE_ID.fullmatch(identifier):
            raise LookupError("Experiment not found.")
        path = self.root / "runs" / f"{identifier}.json"
        if not path.is_file():
            raise LookupError("Experiment not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def submit(self, kind: str, action: Callable[[], dict]) -> dict:
        with self.lock:
            active = sum(job["status"] in ("queued", "running") for job in self.jobs.values())
            if active >= 8:
                raise OverflowError("Eight experiments are already queued. Wait for one to finish.")
            identifier = "job-" + uuid.uuid4().hex[:12]
            job = {"id": identifier, "kind": kind, "status": "queued", "created_at": utc_now(), "result": None, "error": None}
            self.jobs[identifier] = job
            atomic_json(self.root / "jobs" / f"{identifier}.json", job)

        def work() -> None:
            with self.lock:
                job["status"] = "running"
                atomic_json(self.root / "jobs" / f"{identifier}.json", job)
            try:
                result = action()
                if kind == "simulation":
                    result.update(id="run-" + uuid.uuid4().hex[:12], created_at=utc_now())
                    atomic_json(self.root / "runs" / f"{result['id']}.json", result)
                with self.lock:
                    job.update(status="complete", result=result, completed_at=utc_now())
            except Exception as exc:
                LOG.exception("Experiment %s failed", identifier)
                with self.lock:
                    job.update(status="failed", error=str(exc), completed_at=utc_now())
            finally:
                with self.lock:
                    atomic_json(self.root / "jobs" / f"{identifier}.json", job)

        self.executor.submit(work)
        return {"job_id": identifier, "status": "queued"}

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple, state: State):
        self.state = state
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info(fmt, *args)

    def _send(self, status: int, content: bytes, content_type: str = "application/json; charset=utf-8", filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value, allow_nan=False).encode("utf-8"))

    def _body(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ValueError("Send JSON with Content-Type: application/json.")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ValueError("Request must contain JSON and be no larger than 8 MB.")
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise PermissionError("Only requests from this app's own page are accepted.")
        body = json.loads(self.rfile.read(length), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON numbers are not allowed.")))
        if not isinstance(body, dict):
            raise ValueError("The request body must be a JSON object.")
        return body

    def _error(self, exc: Exception) -> None:
        status = 404 if isinstance(exc, LookupError) else 403 if isinstance(exc, PermissionError) else 429 if isinstance(exc, OverflowError) else 400
        self._json({"error": str(exc)}, status)

    def do_GET(self) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            state = self.server.state
            if path == "/api/health":
                return self._json({"ok": True, "version": __version__, "mode": "simulation-only"})
            if path == "/api/bootstrap":
                return self._json({"version": __version__, "datasets": state.summaries(), "models": get_model_catalog(state.artifacts), "defaults": DEFAULTS, "recent_runs": state.runs(), "artifacts": list_artifacts(state.artifacts)})
            if path == "/api/datasets":
                return self._json({"datasets": state.summaries()})
            if path == "/api/runs":
                return self._json({"runs": state.runs()})
            if path == "/api/artifacts":
                return self._json({"artifacts": list_artifacts(state.artifacts)})
            if path.startswith("/api/datasets/") and path.endswith("/preview"):
                identifier = path.split("/")[3]
                return self._json({"dataset": dataset_summary(state.dataset(identifier)), "points": state.hourly_for(identifier)[-168:]})
            if path.startswith("/api/jobs/"):
                identifier = path.split("/")[3]
                with state.lock:
                    if identifier not in state.jobs:
                        raise LookupError("Job not found.")
                    return self._json(state.jobs[identifier])
            if path.startswith("/api/runs/"):
                identifier = path.split("/")[3]
                result = state.run(identifier)
                if path.endswith("/export.csv"):
                    output = io.StringIO()
                    fields = ["label", "cost", "energy_kwh", "battery_added_kwh", "final_soc", "ready", "shortfall_kwh", "savings", "savings_pct", "mae", "switches"]
                    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(result["strategies"])
                    return self._send(200, output.getvalue().encode("utf-8"), "text/csv; charset=utf-8", f"sparc-{identifier}.csv")
                return self._json(result)
            if path.startswith("/api/"):
                raise LookupError("Endpoint not found.")
            relative = "index.html" if path == "/" else path.lstrip("/")
            file = (WEB / relative).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                raise LookupError("Page not found.")
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            if mime.startswith("text/") or mime == "application/javascript":
                mime += "; charset=utf-8"
            self._send(200, file.read_bytes(), mime)
        except (ValueError, LookupError, PermissionError, OverflowError, OSError) as exc:
            self._error(exc)
        except Exception:
            LOG.exception("Unexpected request failure")
            self._json({"error": "An unexpected server error occurred; check the local server log."}, 500)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._body()
            state = self.server.state
            if path == "/api/datasets/import":
                csv_text = body.get("csv")
                if not isinstance(csv_text, str):
                    raise ValueError("Choose a CSV file or paste its text first.")
                name = str(body.get("name") or "Imported data")[:120]
                return self._json({"dataset": state.register(parse_csv_dataset(csv_text, name))}, 201)
            if path == "/api/datasets/comed":
                start, end = body.get("start"), body.get("end")
                if not isinstance(start, str) or not isinstance(end, str):
                    raise ValueError("Choose a start date and an end date.")
                return self._json({"dataset": state.register(fetch_comed_dataset(start, end))}, 201)
            if path == "/api/runs":
                dataset = state.dataset(str(body.get("dataset_id", "")))
                config = body.get("config")
                model_ids = body.get("model_ids")
                if not isinstance(config, dict) or not isinstance(model_ids, list) or not all(isinstance(item, str) for item in model_ids):
                    raise ValueError("Provide charging settings and a list of model IDs.")
                if len(model_ids) > 6 or len(set(model_ids)) != len(model_ids):
                    raise ValueError("Choose up to six distinct forecasting models.")
                available = {model["id"]: model for model in get_model_catalog(state.artifacts)}
                for identifier in model_ids:
                    if identifier not in available:
                        raise ValueError(f"Unknown model: {identifier}")
                    if not available[identifier]["available"]:
                        raise ValueError(available[identifier].get("reason") or f"{identifier} is unavailable.")
                merged = {**DEFAULTS, **config}
                return self._json(state.submit("simulation", lambda: run_simulation(dataset, merged, model_ids, artifacts_dir=state.artifacts)), 202)
            if path == "/api/train":
                dataset_id = str(body.get("dataset_id", ""))
                records = state.hourly_for(dataset_id)
                model_id = body.get("model_id", "ridge")
                if model_id not in ("ridge", "lightgbm", "chronos-bolt"):
                    raise ValueError("Choose Ridge, LightGBM, or a supported prepared Chronos-Bolt training setup.")
                fraction = float(body.get("train_fraction", 0.7))
                if not 0.5 <= fraction <= 0.85:
                    raise ValueError("Training fraction must be between 0.5 and 0.85.")
                config = body.get("config") or {}
                if not isinstance(config, dict):
                    raise ValueError("Training configuration must be an object.")
                allowed = {"alpha", "alphas", "epochs", "learning_rate", "max_steps", "batch_size", "context_length"}
                if set(config) - allowed:
                    raise ValueError("Unsupported training setting. Use the documented training CLI for advanced configurations.")
                return self._json(state.submit("training", lambda: train_model(model_id, records, state.artifacts, train_fraction=fraction, config=config)), 202)
            raise LookupError("Endpoint not found.")
        except (ValueError, LookupError, PermissionError, OverflowError, OSError) as exc:
            self._error(exc)
        except Exception:
            LOG.exception("Unexpected request failure")
            self._json({"error": "An unexpected server error occurred; check the local server log."}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SPARC model and charging simulator locally.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; default is this computer only")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("SPARC_DATA_DIR", "runtime")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    state = State(args.data_dir)
    server = LocalServer((args.host, args.port), state)
    print(f"SPARC Lab · http://{args.host}:{server.server_port} · simulation only", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()


if __name__ == "__main__":
    main()

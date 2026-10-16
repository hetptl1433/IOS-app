"""The local web app: fetch prices, run backtests, train models and compare the results.

    python -m sparc serve        # then open http://127.0.0.1:8765

It listens on 127.0.0.1 only and runs one job (a fetch, a backtest or a training)
at a time, in the background. Requests that change anything must carry an
X-Sparc header, which a page on another site cannot add without a CORS
preflight that this server never approves. The Host header is checked too,
so a hostile DNS name pointed at 127.0.0.1 is refused.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
import webbrowser
from contextlib import closing
from dataclasses import asdict
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import comed, lab
from .backtest import Cancelled
from .clock import local
from .forecast import lightgbm_available
from .history import backfill
from .store import Store

WEB = Path(__file__).with_name("web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/charts.js": ("charts.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
MAX_BODY = 1_000_000
FIRST_FETCH_DATE = date(2010, 1, 1)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Job:
    """A fetch, backtest or training running in the background."""

    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.title = kind, title
        self.state = "running"  # then done, failed or cancelled
        self.done = self.total = 0
        self.message = "Starting"
        self.result: dict | None = None
        self.error: str | None = None
        self.started, self.finished = time.time(), None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def update(self, done: int, total: int, message: str) -> None:
        with self.lock:
            self.done, self.total, self.message = done, total, message

    def to_json(self) -> dict:
        with self.lock:
            return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                    "done": self.done, "total": self.total, "message": self.message,
                    "result": self.result, "error": self.error, "started": self.started,
                    "finished": self.finished, "cancelling": self.cancel.is_set()}


class Jobs:
    """Runs one job at a time and remembers the last few."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recent: dict[str, Job] = {}
        self.current: Job | None = None

    def start(self, kind: str, title: str, work, finished=None) -> Job:
        with self.lock:
            if self.current and self.current.state == "running":
                raise ApiError(HTTPStatus.CONFLICT, f"Wait for the current job to finish: {self.current.title}.")
            job = self.current = Job(kind, title)
            self.recent[job.id] = job
            while len(self.recent) > 20:
                del self.recent[next(iter(self.recent))]
        threading.Thread(target=self._run, args=(job, work, finished), name=f"sparc-{kind}", daemon=True).start()
        return job

    def get(self, ident: str) -> Job:
        with self.lock:
            job = self.recent.get(ident)
        if job is None:
            raise LookupError("No such job.")
        return job

    @staticmethod
    def _run(job: Job, work, finished) -> None:
        result, error, state = None, None, "failed"
        try:
            result, state = work(job), "done"
        except Cancelled:
            state = "cancelled"
        except (ValueError, LookupError, comed.ComEdError) as exc:
            error = str(exc)
        except Exception as exc:  # report it in the page as well as the terminal
            traceback.print_exc()
            error = f"Unexpected error ({type(exc).__name__}: {exc}). The terminal running the server has details."
        finally:
            with job.lock:
                job.result, job.error, job.state, job.finished = result, error, state, time.time()
            if finished:
                finished(job)


class App:
    def __init__(self, folders: lab.Folders, get: comed.Get | None = None):
        self.folders = folders
        self.get = get or comed.Fetcher()
        self.jobs = Jobs()
        self.status_lock = threading.Lock()
        self.status: dict | None = None  # coverage takes a second to compute, so it is kept until a fetch

    def store(self):
        return closing(Store(self.folders.db))

    def data_status(self) -> dict:
        with self.status_lock:
            if self.status is None:
                with self.store() as store:
                    self.status = lab.data_status(store)
            return self.status

    def forget_status(self, job: Job | None = None) -> None:
        with self.status_lock:
            self.status = None

    # Routes. Each returns a JSON-able value, or raises ApiError, ValueError or LookupError.

    def state(self, body) -> dict:
        with self.store() as store:
            defaults = lab.default_config(store, self.folders)
        current = self.jobs.current
        return {"data": self.data_status(), "defaults": defaults,
                "choices": [asdict(c) for c in lab.strategy_choices(self.folders)], "groups": lab.GROUPS,
                "tariffs": {key: {"name": d.name, "bands": d.bands} for key, d in lab.TARIFFS.items()},
                "profiles_max": lab.MAX_PROFILES, "nights_max": lab.MAX_NIGHTS,
                "min_calibration": lab.MIN_CALIBRATION, "lightgbm": lightgbm_available(),
                "models": lab.list_models(self.folders), "runs": lab.list_runs(self.folders),
                "job": current.to_json() if current else None,
                "today": local(int(time.time())).date().isoformat(), "folder": str(self.folders.root.resolve())}

    def fetch(self, body) -> dict:
        start, end = lab.parse_date(body.get("start"), "From"), lab.parse_date(body.get("end"), "To")
        today = local(int(time.time())).date()
        if end < start:
            raise ValueError("The end date must not be before the start date.")
        if start < FIRST_FETCH_DATE or end > today:
            raise ValueError(f"Choose dates between {FIRST_FETCH_DATE} and today.")

        def work(job: Job) -> dict:
            with self.store() as store:
                failed = backfill(store, start, end, self.get, progress=job.update,
                                  should_stop=job.cancel.is_set, log=lambda message: None)
            return {"failed": failed}

        return self.jobs.start("fetch", f"Fetching prices for {start} to {end}", work,
                               finished=self.forget_status).to_json()

    def backtest(self, body) -> dict:
        config = lab.RunConfig.parse(body, lab.strategy_choices(self.folders))

        def work(job: Job) -> dict:
            with self.store() as store:
                result = lab.run_backtest(store, config, self.folders, job.update, job.cancel.is_set)
            return {"run": result["id"]}

        nights = (config.end - config.start).days + 1
        return self.jobs.start("backtest", f"Backtest of {nights} nights from {config.start}", work).to_json()

    def train(self, body) -> dict:
        config = lab.TrainConfig.parse(body)

        def work(job: Job) -> dict:
            with self.store() as store:
                model = lab.train_model(store, config, self.folders, job.update, job.cancel.is_set)
            return {"model": model["id"]}

        return self.jobs.start("train", f"Training {config.kind} on {config.train[0]} to {config.train[1]}",
                               work).to_json()

    def job(self, body, ident) -> dict:
        return self.jobs.get(ident).to_json()

    def cancel(self, body, ident) -> dict:
        job = self.jobs.get(ident)
        job.cancel.set()
        return job.to_json()

    def runs(self, body) -> list:
        return lab.list_runs(self.folders)

    def run(self, body, ident) -> dict:
        return lab.load_run(self.folders, ident)

    def delete_run(self, body, ident) -> dict:
        lab.load_run(self.folders, ident)  # 404 for a run that does not exist
        lab.delete_run(self.folders, ident)
        return {"deleted": ident}

    def models(self, body) -> list:
        return lab.list_models(self.folders)

    def delete_model(self, body, ident) -> dict:
        lab.delete_model(self.folders, ident)
        return {"deleted": ident}


ROUTES = [(method, re.compile(pattern), name) for method, pattern, name in (
    ("GET", r"/api/state", "state"),
    ("POST", r"/api/fetch", "fetch"),
    ("POST", r"/api/backtest", "backtest"),
    ("POST", r"/api/train", "train"),
    ("GET", r"/api/jobs/([0-9a-f]{12})", "job"),
    ("POST", r"/api/jobs/([0-9a-f]{12})/cancel", "cancel"),
    ("GET", r"/api/runs", "runs"),
    ("GET", r"/api/runs/([\w-]+)", "run"),
    ("DELETE", r"/api/runs/([\w-]+)", "delete_run"),
    ("GET", r"/api/models", "models"),
    ("DELETE", r"/api/models/([\w-]+)", "delete_model"),
)]
CSV_ROUTE = re.compile(r"/api/runs/([\w-]+)/nights\.csv")


class Handler(BaseHTTPRequestHandler):
    server_version = "sparc"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def handle_request(self, method: str) -> None:
        app: App = self.server.app
        try:
            body = self.read_body()
            if self.headers.get("Host") not in self.server.hosts:
                raise ApiError(HTTPStatus.FORBIDDEN, "Open the app at http://127.0.0.1 or http://localhost.")
            path = urlsplit(self.path).path
            if method == "GET" and path in STATIC:
                return self.send_static(*STATIC[path])
            if method == "GET" and (match := CSV_ROUTE.fullmatch(path)):
                return self.send_csv(lab.run_csv(app.folders, match[1]), match[1])
            if method != "GET" and self.headers.get("X-Sparc") != "1":
                raise ApiError(HTTPStatus.FORBIDDEN, "Requests that change data need the X-Sparc header.")
            for route_method, pattern, name in ROUTES:
                match = pattern.fullmatch(path)
                if match and route_method == method:
                    result = getattr(app, name)(body, *match.groups())
                    return self.send_json(HTTPStatus.ACCEPTED if name in ("fetch", "backtest", "train")
                                          else HTTPStatus.OK, result)
            if any(pattern.fullmatch(path) for _, pattern, _ in ROUTES):
                raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} is not supported here.")
            raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
        except ApiError as exc:
            self.send_json(exc.status, {"error": str(exc)})
        except LookupError as exc:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": str(exc).strip("'\"")})
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            traceback.print_exc()
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad Content-Length.") from None
        if length > MAX_BODY:
            self.close_connection = True
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "The request body is not JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Send a JSON object.")
        return body

    def send_json(self, status: HTTPStatus, value) -> None:
        self.send_bytes(status, json.dumps(value, default=str).encode(), "application/json; charset=utf-8")

    def send_static(self, name: str, content_type: str) -> None:
        self.send_bytes(HTTPStatus.OK, (WEB / name).read_bytes(), content_type)

    def send_csv(self, path: Path, ident: str) -> None:
        self.send_bytes(HTTPStatus.OK, path.read_bytes(), "text/csv; charset=utf-8",
                        {"Content-Disposition": f'attachment; filename="sparc-{ident}.csv"'})

    def send_bytes(self, status: HTTPStatus, data: bytes, content_type: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def log_request(self, code="-", size="-"):
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    def log_message(self, format, *args):
        if not self.server.quiet:
            super().log_message(format, *args)


def make_server(folders: lab.Folders, port: int = 8765, get: comed.Get | None = None,
                quiet: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.quiet = quiet
    server.app = App(folders, get)
    bound = server.server_address[1]
    server.hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
    return server


def serve(folders: lab.Folders, port: int = 8765, open_browser: bool = True) -> int:
    try:
        server = make_server(folders, port)
    except OSError as exc:
        print(f"Could not listen on port {port} ({exc.strerror}). Try another: python -m sparc serve --port 8766")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"SPARC is running at {url}\nData folder: {folders.root.resolve()}\nPress Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

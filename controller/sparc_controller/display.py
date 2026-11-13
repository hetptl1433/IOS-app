"""Loopback-only page and JSON controls for a screen plugged into the Pi itself.

It never listens beyond 127.0.0.1, so it needs no pairing key: anything that can
reach it already runs on the Pi. Phones keep using the encrypted channel.
"""
import asyncio
import ipaddress
import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .app_test import CHICAGO, local_instant
from .network import pi_temperature
from .protocol import compact_json
from .pricing import ComEdPricing

PAGES = Path(__file__).resolve().parents[1] / "display"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/display.css": ("display.css", "text/css; charset=utf-8"),
          "/display.js": ("display.js", "text/javascript; charset=utf-8"),
          "/combined-savings.js": ("combined-savings.js", "text/javascript; charset=utf-8"),
          "/connections.js": ("connections.js", "text/javascript; charset=utf-8"),
          "/qrcode.js": ("qrcode.js", "text/javascript; charset=utf-8"),
          "/truck.webp": ("truck.webp", "image/webp")}
COMMANDS = ("charge_now", "pause", "resume", "end")
MAX_BODY = 4096
POLICY = ("default-src 'self'; style-src 'self' 'unsafe-inline'; base-uri 'none'; "
          "form-action 'none'; frame-ancestors 'none'")
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           413: "Content Too Large", 415: "Unsupported Media Type", 421: "Misdirected Request",
           500: "Internal Server Error"}


def loopback(address):
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if getattr(ip, "ipv4_mapped", None): ip = ip.ipv4_mapped
        return ip.is_loopback
    except ValueError:
        return False


class DisplayTransport:
    def __init__(self, service, port, pricing=None):
        self.service, self.port = service, port
        self.active = 0
        self.files = {}
        self.pricing = pricing if pricing is not None else (getattr(service, "pricing", None) or ComEdPricing())

    @property
    def hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def state(self):
        state = self.service.display_state()
        state["controller"]["temperatureC"] = pi_temperature()
        state["pricing"] = self.pricing.state()
        return {"ok": True, **state}

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if self.active >= 8 or not peer or not loopback(peer[0]):
            writer.close()
            return
        self.active += 1
        try:
            status, content_type, body = await asyncio.wait_for(self.request(reader), 45)
            cache = "no-cache" if content_type.startswith(("text/", "image/")) else "no-store"
            writer.write(f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: {content_type}\r\n"
                         f"Content-Length: {len(body)}\r\nCache-Control: {cache}\r\nConnection: close\r\n"
                         f"X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
                         f"Content-Security-Policy: {POLICY}\r\n\r\n".encode() + body)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                ConnectionError, UnicodeDecodeError):
            pass
        finally:
            self.active -= 1
            writer.close()
            try: await writer.wait_closed()
            except ConnectionError: pass

    def reply(self, status, value):
        return status, "application/json", compact_json(value)

    async def request(self, reader):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = raw.decode("ascii").split("\r\n")
        first = lines[0].split(" ")
        if len(first) != 3 or first[2] != "HTTP/1.1":
            return self.reply(400, {"ok": False, "error": "Invalid request."})
        method, target, _ = first
        # The kiosk opens /?kiosk=1; routing ignores any query string.
        path = target.split("?", 1)[0]
        headers = {}
        for line in lines[1:]:
            if not line: continue
            name, separator, value = line.partition(":")
            name = name.strip().lower()
            if not separator or name in headers:
                return self.reply(400, {"ok": False, "error": "Invalid headers."})
            headers[name] = value.strip()
        # A page served from another name (DNS rebinding) never reaches the controls.
        if headers.get("host") not in self.hosts:
            return self.reply(421, {"ok": False, "error": "Open this page at 127.0.0.1."})
        if method == "GET":
            if path == "/healthz": return self.reply(200, {"ok": True})
            if path == "/api/state": return self.reply(200, self.state())
            if path == "/api/savings":
                try:
                    params=parse_qs(urlsplit(target).query,strict_parsing=True)
                    if set(params)-{"kind"} or any(len(v)!=1 for v in params.values()):raise ValueError("Choose one savings view.")
                    return self.reply(200,await self.service.savings_view({k:v[0] for k,v in params.items()}))
                except ValueError as exc:
                    return self.reply(400,{"ok":False,"error":str(exc)})
            if path == "/api/rates":
                try:
                    params = parse_qs(urlsplit(target).query, strict_parsing=True)
                    if set(params)-{"day"} or any(len(v)!=1 for v in params.values()):
                        raise ValueError("Choose one day for rate history.")
                    return self.reply(200, await self.service.dispatch("get_rates", {k:v[0] for k,v in params.items()}, "display"))
                except ValueError as exc:
                    return self.reply(400, {"ok":False,"error":str(exc)})
            if path == "/api/history":
                try:
                    params = parse_qs(urlsplit(target).query, strict_parsing=True)
                    if set(params)-{"cursor", "limit"} or any(len(v)!=1 for v in params.values()):
                        raise ValueError("Choose a valid history page.")
                    body = {key:int(value[0]) for key,value in params.items()}
                    return self.reply(200, await self.service.history_view(body))
                except ValueError:
                    return self.reply(400, {"ok":False, "error":"Choose a valid history page."})
            if path in STATIC:
                name, content_type = STATIC[path]
                if name not in self.files: self.files[name] = (PAGES / name).read_bytes()
                return 200, content_type, self.files[name]
            return self.reply(404, {"ok": False, "error": "Not found."})
        if method != "POST" or path not in ("/api/command", "/api/weekly", "/api/trip", "/api/demo", "/api/overnight", "/api/campaign", "/api/developer", "/api/connections", "/api/connections/activate", "/api/connections/plug/check", "/api/connections/plug/power", "/api/connections/tesla/authorize", "/api/connections/tesla/complete", "/api/connections/tesla/refresh", "/api/connections/tesla/select", "/api/connections/tesla/disconnect", "/api/connections/tesla/command"):
            return self.reply(405 if path.startswith("/api/") else 404, {"ok": False, "error": "Not found."})
        if headers.get("origin", f"http://127.0.0.1:{self.port}") not in {"http://" + host for host in self.hosts} \
                or headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
            return self.reply(403, {"ok": False, "error": "Use the SPARC screen on this Pi."})
        if headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return self.reply(415, {"ok": False, "error": "Send JSON."})
        length = headers.get("content-length", "")
        if not length.isascii() or not length.isdigit() or not 0 < int(length) <= MAX_BODY:
            return self.reply(413, {"ok": False, "error": "Request too large."})
        try:
            body = json.loads(await asyncio.wait_for(reader.readexactly(int(length)), 10))
            if not isinstance(body, dict): raise ValueError("Send a JSON object.")
            result = await self.post(path, body)
        except ValueError as exc:
            return self.reply(400, {"ok": False, "error": str(exc) if not isinstance(exc, json.JSONDecodeError) else "Send JSON."})
        except Exception:
            return self.reply(500, {"ok": False, "error": "SPARC could not complete that. Try again."})
        return self.reply(200, {**self.state(), **({'actionResult':result} if result is not None else {})})

    async def post(self, path, body):
        connection_actions = {'/api/connections':'set_connection_settings',
            '/api/connections/activate':'activate_connection', '/api/connections/tesla/command':'tesla_command',
            '/api/connections/plug/check':'check_smart_plug', '/api/connections/plug/power':'set_smart_plug_power',
            '/api/connections/tesla/authorize':'tesla_authorize', '/api/connections/tesla/refresh':'tesla_refresh',
            '/api/connections/tesla/complete':'tesla_complete_authorization',
            '/api/connections/tesla/select':'tesla_select_vehicle', '/api/connections/tesla/disconnect':'tesla_disconnect'}
        if path in connection_actions:
            return await self.service.dispatch(connection_actions[path], body, 'display')
        if path == "/api/command":
            if body.get("command") not in COMMANDS:
                raise ValueError("Choose Charge now, Pause, Resume or End.")
            return await self.service.dispatch(body["command"], {}, "display")
        elif path == "/api/overnight":
            await self.service.dispatch("set_overnight", body, "display")
        elif path == "/api/campaign":
            await self.service.dispatch("set_campaign", body, "display")
        elif path == "/api/developer":
            await self.service.dispatch("set_virtual_car_state", body, "display")
        elif path == "/api/weekly":
            await self.service.dispatch("set_weekly", body, "display")
        elif path == "/api/demo":
            # The screen's hidden developer switch: the same demo car the phone sees.
            await self.service.dispatch("set_virtual_car", {"enabled": body.get("enabled")}, "display")
        else:
            await self.service.dispatch("set_today", {"today": self.trip(body)}, "display")

    def trip(self, body):
        """Resolve a local Chicago departure on the Pi, so the page never does time-zone math."""
        if body.get("clear") is True:
            return None
        offset, minutes = body.get("dayOffset"), body.get("departureMinutes")
        if type(offset) is not int or offset not in (0, 1) or type(minutes) is not int or not 0 <= minutes < 1440:
            raise ValueError("Choose today or tomorrow and a departure time.")
        day = self.service.clock().astimezone(CHICAGO).date() + timedelta(days=offset)
        return {"targetPercent": body.get("targetPercent"), "departure": local_instant(day, minutes).isoformat()}


async def run_display(service, port):
    transport = DisplayTransport(service, port)
    server = await asyncio.start_server(transport.handle, "127.0.0.1", port, limit=8192)
    pricing_task = None if getattr(service, "pricing", None) is not None else asyncio.create_task(transport.pricing.run())
    try:
        async with server:
            await server.serve_forever()
    finally:
        if pricing_task is not None:
            pricing_task.cancel()
            await asyncio.gather(pricing_task, return_exceptions=True)

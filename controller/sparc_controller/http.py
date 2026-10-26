"""Small bounded HTTP transport for encrypted LAN envelopes (never plaintext commands)."""
import asyncio
import ipaddress
import json

from .protocol import MAX_FRAME, ProtocolError, compact_json

LOCAL_NETWORKS = [ipaddress.ip_network(x) for x in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "fc00::/7", "fe80::/10", "::1/128")]


def local_peer(address):
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if getattr(ip, "ipv4_mapped", None): ip = ip.ipv4_mapped
        return any(ip.version == network.version and ip in network for network in LOCAL_NETWORKS)
    except ValueError:
        return False


class HTTPTransport:
    def __init__(self, service):
        self.service = service
        self.active = 0

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        if self.active >= 16 or not peer or not local_peer(peer[0]):
            writer.close()
            return
        self.active += 1
        try:
            status, body = await asyncio.wait_for(self.request(reader), 12)
            payload = compact_json(body) if isinstance(body, dict) else body
            writer.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError):
            pass
        finally:
            self.active -= 1
            writer.close()
            try: await writer.wait_closed()
            except ConnectionError: pass

    async def request(self, reader):
        raw = await reader.readuntil(b"\r\n\r\n")
        if len(raw) > 8192: return "431 Request Header Fields Too Large", {"error": "headers_too_large"}
        lines = raw.decode("ascii").split("\r\n")
        first = lines[0].split(" ")
        if len(first) != 3 or first[2] != "HTTP/1.1": return "400 Bad Request", {"error": "invalid_request"}
        method, path, _ = first
        headers = {}
        for line in lines[1:]:
            if not line: continue
            if ":" not in line: return "400 Bad Request", {"error": "invalid_headers"}
            name, value = line.split(":", 1)
            name = name.strip().lower()
            if name in headers: return "400 Bad Request", {"error": "duplicate_header"}
            headers[name] = value.strip()
        if "transfer-encoding" in headers: return "400 Bad Request", {"error": "unsupported_transfer_encoding"}
        if method == "GET" and path == "/v1/info": return "200 OK", self.service.info.public()
        if method != "POST" or path != "/v1/exchange": return "404 Not Found", {"error": "not_found"}
        length = int(headers.get("content-length", "0"))
        if not 1 <= length <= MAX_FRAME: return "413 Content Too Large", {"error": "invalid_length"}
        data = await reader.readexactly(length)
        try:
            return "200 OK", await self.service.handle(data, "lan")
        except ProtocolError:
            return "401 Unauthorized", {"error": "authentication_failed"}


async def run_http(service, host, port):
    transport = HTTPTransport(service)
    server = await asyncio.start_server(transport.handle, host, port, limit=8192)
    async with server:
        await server.serve_forever()

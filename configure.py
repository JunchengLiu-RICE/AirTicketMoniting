"""Open a local airport/date/cabin form and save trip.yaml without running searches."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import urlsplit
import webbrowser

import yaml

from core.config import FIELDS, build_config


ROOT = Path(__file__).resolve().parent


class ConflictError(ValueError):
    pass


class TripEditor:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()

    def read(self):
        content = self.path.read_bytes() if self.path.exists() else b""
        settings = yaml.safe_load(content) if content else {"origin": "", "destination": "", "dates": [], "cabin": "economy"}
        if not isinstance(settings, dict) or set(settings) - FIELDS:
            raise ValueError("trip.yaml contains unsupported settings; see README.md")
        return settings, hashlib.sha256(content).hexdigest()

    def save(self, settings, revision):
        if not isinstance(settings, dict):
            raise ValueError("Trip settings must be an object")
        config = build_config(settings)
        with self.lock:
            _, current_revision = self.read()
            if not secrets.compare_digest(str(revision), current_revision):
                raise ConflictError("文件已被其他窗口修改，请刷新页面后重试。")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write("# Configured with python configure.py. Dates use departure-airport local time.\n")
                    yaml.safe_dump(settings, stream, allow_unicode=True, sort_keys=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            _, new_revision = self.read()
        return {"revision": new_revision, "active_routes": len(config["routes"])}


def make_server(path, port=0):
    editor = TripEditor(path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log trip details or request bodies.

        def respond(self, status, content, content_type="application/json; charset=utf-8"):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def host_allowed(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.host_allowed():
                self.respond(403, {"error": "Local access only"})
                return
            route = urlsplit(self.path).path
            if route == "/api/trip":
                try:
                    settings, revision = editor.read()
                    self.respond(200, {"settings": settings, "revision": revision, "token": editor.token})
                except (ValueError, OSError, yaml.YAMLError) as exc:
                    self.respond(400, {"error": str(exc)})
            elif route == "/api/airports":
                self.respond(200, (ROOT / "core/airport_search.json").read_bytes())
            else:
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/style.css": ("style.css", "text/css; charset=utf-8")}
                if route not in files:
                    self.respond(404, {"error": "Not found"})
                    return
                name, mime = files[route]
                self.respond(200, (ROOT / "ui" / name).read_bytes(), mime)

        def do_POST(self):
            if (not self.host_allowed() or self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}")
                    or not secrets.compare_digest(self.headers.get("X-AirTicket-Token", ""), editor.token)):
                self.respond(403, {"error": "请从当前配置页面保存。"})
                return
            if self.path != "/api/trip":
                self.respond(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Invalid request")
                result = editor.save(payload.get("settings"), payload.get("revision"))
                self.respond(200, result)
            except ConflictError as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, OSError, yaml.YAMLError) as exc:
                self.respond(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="Configure airports, dates and cabin in your browser")
    parser.add_argument("--config", type=Path, default=ROOT / "trip.yaml")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    with make_server(args.config, args.port) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"AirTicketMoniting trip editor: {url}", flush=True)
        print("Press Ctrl+C to close. Saving does not start a fare search.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

"""Local-only review server. Never serves the repository or arbitrary filesystem paths."""

import json
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from ..database import writer_lock
from .store import apply_edit, exports, load_run


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, bundle, port):
        self.config, self.bundle = config, bundle
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), ReviewHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "media-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        return self.headers.get("Host") == urlsplit(self.server.origin).netloc

    def do_GET(self):
        if not self.allowed():
            self.send(403, '{"error":"Invalid host"}')
            return
        path = urlsplit(self.path).path
        try:
            if path in {"/", "/review.js", "/review.css"}:
                name = {"/": "review.html", "/review.js": "review.js", "/review.css": "review.css"}[
                    path
                ]
                mime = {
                    "/": "text/html",
                    "/review.js": "application/javascript",
                    "/review.css": "text/css",
                }[path]
                self.send(
                    200, files(__package__).joinpath(name).read_bytes(), mime + "; charset=utf-8"
                )
                return
            directory, base, state = load_run(self.server.bundle)
            if path == "/api/state":
                self.send(
                    200, json.dumps({"analysis": base, "edits": state, "token": self.server.token})
                )
            elif path == "/audio.wav":
                if urlsplit(self.path).query != base["run_id"]:
                    raise ValueError("Speaker run changed; reload before playing audio")
                self.audio(directory / "audio.wav")
            elif path.startswith("/export/") and path.removeprefix("/export/") in exports(
                base, state
            ):
                if urlsplit(self.path).query != base["run_id"]:
                    raise ValueError("Speaker run changed; reload before exporting")
                name = path.removeprefix("/export/")
                self.send(
                    200,
                    exports(base, state)[name],
                    "text/plain; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
            else:
                self.send(404, '{"error":"Not found"}')
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, ValueError, KeyError) as exc:
            self.send(409, json.dumps({"error": str(exc)}))

    def audio(self, path):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                self.send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                return
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), size - 1) if b else size - 1
            else:
                start = max(0, size - int(b))
            if start > end or start >= size:
                self.send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as audio:
            audio.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = audio.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        if (
            not self.allowed()
            or self.path != "/api/edits"
            or self.headers.get("Origin") != self.server.origin
            or not secrets.compare_digest(self.headers.get("X-Review-Token", ""), self.server.token)
        ):
            self.send(403, '{"error":"Invalid review request"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 200_000:
                raise ValueError("Invalid edit request size")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or request.keys() - {
                "run_id",
                "revision",
                "names",
                "overrides",
            }:
                raise ValueError("Invalid edit request")
            with writer_lock(self.server.config.database):
                state = apply_edit(self.server.bundle, **request)
            self.send(200, json.dumps(state))
        except (ValueError, OSError, RuntimeError, TypeError, KeyError) as exc:
            self.send(409, json.dumps({"error": str(exc)}))


def serve(config, bundle, port, open_browser):
    load_run(bundle)
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    with ReviewServer(config, bundle, port) as server:
        print(
            f"Speaker review: {server.origin}/\nSaved on this computer. Press Ctrl+C to stop.",
            flush=True,
        )
        if open_browser:
            webbrowser.open(server.origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    return 0

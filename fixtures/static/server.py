"""Stdlib static server. Serves /healthz and refuses to leave the site root."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("CAIRN_PUBLIC_ROOT", "/srv/public"))
PORT = int(os.environ.get("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, "text/plain", b"ok\n")
            return
        if path in {"/whoami", "/edge-probe"}:
            user = self.headers.get("X-Auth-Request-User") or "none"
            self._send(200, "text/plain", user.encode())
            return
        if path == "/api/ping":
            self._send(200, "text/plain", b"pong\n")
            return
        target = _safe(path)
        if target is None:
            self._send(404, "text/plain", b"not found\n")
            return
        self._send(200, "text/html; charset=utf-8", target.read_bytes())

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _safe(url_path: str) -> Path | None:
    rel = url_path.lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    if rel.startswith(".") or "/." in f"/{rel}":
        return None
    root = ROOT.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    return None


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

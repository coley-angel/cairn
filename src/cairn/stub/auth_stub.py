"""Local auth double used only by the integration test.

The CLI never selects this process. It is not an identity provider.
A request is authenticated only when it presents the well-known test cookie.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = os.environ.get("CAIRN_STUB_USER", "ada")
EMAIL = os.environ.get("CAIRN_STUB_EMAIL", "ada@example.com")
GROUPS = os.environ.get("CAIRN_STUB_GROUPS", "engineering")
COOKIE = os.environ.get("CAIRN_STUB_COOKIE", "cairn_session=demo")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.startswith("/oauth2/auth"):
            cookie = self.headers.get("Cookie", "")
            if COOKIE in cookie:
                self._send(
                    202,
                    {
                        "X-Auth-Request-User": USER,
                        "X-Auth-Request-Email": EMAIL,
                        "X-Auth-Request-Groups": GROUPS,
                        "X-Auth-Request-Preferred-Username": USER,
                    },
                )
            else:
                self._send(401)
            return
        if path.startswith("/oauth2/"):
            self._send(401, body=b"sign-in required")
            return
        self._send(404)

    def _send(self, code: int, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.environ.get("PORT", "4180"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

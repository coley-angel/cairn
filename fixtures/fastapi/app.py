import os

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/whoami")
def whoami(request: Request) -> dict[str, str | None]:
    return {
        "user": request.headers.get("x-auth-request-user"),
        "email": request.headers.get("x-auth-request-email"),
        "groups": request.headers.get("x-auth-request-groups"),
    }


@app.get("/edge-probe")
def edge_probe(request: Request) -> dict[str, str | None]:
    return {"user": request.headers.get("x-auth-request-user")}


@app.get("/api/ping")
def api_ping() -> dict[str, bool]:
    return {"pong": True}

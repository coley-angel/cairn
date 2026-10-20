import os

from flask import Flask, request

app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return "ok\n"


@app.get("/whoami")
def whoami():
    return request.headers.get("X-Auth-Request-User", "none")

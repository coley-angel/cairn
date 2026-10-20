"""Dockerfiles for the paradigms Cairn knows how to run."""

from __future__ import annotations

from pathlib import Path

from cairn.headers import GO_IMAGE, GO_RUNTIME_IMAGE, NODE_IMAGE, PYTHON_IMAGE
from cairn.model import AppContract


def render_dockerfile(contract: AppContract, app_dir: Path) -> str | None:
    paradigm = contract.paradigm
    if paradigm in {"dockerfile", "compose"}:
        return None
    port = contract.port
    if paradigm == "static":
        return _static(app_dir, port)
    if paradigm == "node":
        return _node(port)
    if paradigm == "next":
        return _next(port)
    if paradigm == "fastapi":
        return _python(app_dir, contract.runtime.module or "app:app", port, kind="asgi")
    if paradigm == "flask":
        return _python(app_dir, contract.runtime.module or "app:app", port, kind="wsgi")
    if paradigm == "django":
        return _python(app_dir, contract.runtime.module or "app.wsgi:application", port, kind="django")
    if paradigm == "go":
        return _go(port)
    raise ValueError(f"no Dockerfile for paradigm {paradigm}")


def _python(app_dir: Path, module: str, port: int, *, kind: str) -> str:
    if (app_dir / "requirements.txt").is_file():
        install = "COPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\n"
    elif (app_dir / "pyproject.toml").is_file():
        install = "COPY pyproject.toml .\nRUN pip install --no-cache-dir .\n"
    else:
        install = ""
    extra = "ENV DJANGO_SETTINGS_MODULE=app.settings\n" if kind == "django" else ""
    if kind == "asgi":
        command = f'CMD ["sh", "-c", "exec uvicorn {module} --host 0.0.0.0 --port ${{PORT:-{port}}}"]'
    else:
        command = f'CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${{PORT:-{port}}} {module}"]'
    return f"""\
FROM {PYTHON_IMAGE}
WORKDIR /app
{install}COPY . .
{extra}RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin cairn
ENV PORT={port} PYTHONDONTWRITEBYTECODE=1
EXPOSE {port}
USER cairn
{_probe(port)}
{command}
"""


def _static(app_dir: Path, port: int) -> str:
    server = "COPY server.py /srv/server.py" if (app_dir / "server.py").is_file() else "COPY .cairn/static_server.py /srv/server.py"
    if (app_dir / "public").is_dir():
        site = "COPY public /srv/public"
    elif (app_dir / "index.html").is_file():
        site = "COPY index.html /srv/public/index.html"
    else:
        site = "COPY .cairn/placeholder-index.html /srv/public/index.html"
    return f"""\
FROM {PYTHON_IMAGE}
WORKDIR /srv
{server}
{site}
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin cairn
ENV PORT={port} PYTHONDONTWRITEBYTECODE=1
EXPOSE {port}
USER cairn
{_probe(port)}
CMD ["python", "/srv/server.py"]
"""


def _node(port: int) -> str:
    return f"""\
FROM {NODE_IMAGE}
WORKDIR /app
COPY package.json server.js ./
ENV PORT={port}
EXPOSE {port}
USER node
HEALTHCHECK --interval=2s --timeout=2s --start-period=5s --retries=20 CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||{port})+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
"""


def _next(port: int) -> str:
    return f"""\
FROM {NODE_IMAGE}
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build
ENV PORT={port}
EXPOSE {port}
USER node
CMD ["npm", "start"]
"""


def _go(port: int) -> str:
    return f"""\
FROM {GO_IMAGE} AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN go build -o /out/app .

FROM {GO_RUNTIME_IMAGE}
RUN apt-get update \\
    && apt-get install -y --no-install-recommends wget ca-certificates \\
    && rm -rf /var/lib/apt/lists/* \\
    && useradd --uid 65532 --create-home --shell /usr/sbin/nologin cairn
COPY --from=build /out/app /usr/local/bin/app
ENV PORT={port}
EXPOSE {port}
USER cairn
HEALTHCHECK --interval=2s --timeout=2s --start-period=5s --retries=20 CMD wget -q -O- http://127.0.0.1:{port}/healthz
CMD ["/usr/local/bin/app"]
"""


def _probe(port: int) -> str:
    return (
        "HEALTHCHECK --interval=2s --timeout=2s --start-period=15s --retries=20 "
        'CMD python -c "import os,urllib.request; urllib.request.urlopen('
        f"'http://127.0.0.1:'+os.environ.get('PORT','{port}')+'/healthz')\""
    )

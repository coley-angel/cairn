"""Kubernetes manifests. The auth proxy shares the edge pod network namespace."""

from __future__ import annotations

from cairn.headers import CADDY_IMAGE, OAUTH2_PROXY_IMAGE
from cairn.model import AppContract


def render_kubernetes(contract: AppContract, *, caddyfile: str, proxy_config: str | None) -> dict[str, str]:
    labels = {
        "app": contract.name,
        "paradigm": contract.paradigm,
    }
    return {
        "configmap.yaml": _configmap(contract, caddyfile, proxy_config),
        "deployment-app.yaml": _app_deployment(contract),
        "service-app.yaml": _service(contract, "app", contract.port, {**labels, "role": "app"}),
        "deployment-edge.yaml": _edge_deployment(contract, proxy_config is not None),
        "service-edge.yaml": _service(contract, "edge", contract.edge.bind_port, {**labels, "role": "edge"}),
        "networkpolicy.yaml": _policy(contract),
    }


def _configmap(contract: AppContract, caddyfile: str, proxy_config: str | None) -> str:
    data = [f"  Caddyfile: |\n{_indent(caddyfile, 4)}"]
    if proxy_config is not None:
        data.append(f"  oauth2-proxy.cfg: |\n{_indent(proxy_config, 4)}")
    if contract.auth.emails:
        data.append("  emails.txt: |\n" + _indent("".join(f"{email}\n" for email in contract.auth.emails), 4))
    body = "\n".join(data)
    return (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        f"  name: {contract.name}-edge\n"
        "  labels:\n"
        f"    cairn.dev/app: {contract.name}\n"
        "data:\n"
        f"{body}\n"
    )


def _app_deployment(contract: AppContract) -> str:
    env = [f"            - name: PORT\n              value: \"{contract.port}\""]
    if contract.paradigm == "django":
        env.append(
            "            - name: DJANGO_SECRET_KEY\n"
            "              valueFrom:\n"
            "                secretKeyRef:\n"
            f"                  name: {contract.name}-app\n"
            "                  key: django-secret-key"
        )
        env.append(
            "            - name: DJANGO_SETTINGS_MODULE\n"
            "              value: app.settings"
        )
    if contract.auth.mode == "app-oidc" and contract.auth.issuer:
        env.extend(_oidc_env(contract))
    environment = "\n".join(env)
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {contract.name}-app
  labels:
    cairn.dev/app: {contract.name}
    cairn.dev/role: app
spec:
  replicas: 1
  selector:
    matchLabels:
      cairn.dev/app: {contract.name}
      cairn.dev/role: app
  template:
    metadata:
      labels:
        cairn.dev/app: {contract.name}
        cairn.dev/role: app
        cairn.dev/paradigm: {contract.paradigm}
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: app
          image: cairn.dev/{contract.name}:local
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: {contract.port}
              name: http
          env:
{environment}
          readinessProbe:
            httpGet:
              path: {contract.health_path}
              port: http
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {{}}
"""


def _oidc_env(contract: AppContract) -> list[str]:
    from cairn.model import public_origin

    origin = public_origin(contract)
    secret = f"{contract.name}-oidc"
    return [
        f"            - name: OIDC_ISSUER\n              value: {contract.auth.issuer}",
        "            - name: OIDC_CLIENT_ID\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        f"                  name: {secret}\n"
        "                  key: client-id",
        "            - name: OIDC_CLIENT_SECRET\n"
        "              valueFrom:\n"
        "                secretKeyRef:\n"
        f"                  name: {secret}\n"
        "                  key: client-secret",
        f"            - name: OIDC_REDIRECT_URI\n              value: {origin}/callback",
    ]


def _edge_deployment(contract: AppContract, with_auth: bool) -> str:
    auth = ""
    if with_auth:
        email_mount = ""
        if contract.auth.emails:
            email_mount = f"""\
            - name: config
              mountPath: /etc/cairn/emails.txt
              subPath: emails.txt
              readOnly: true
"""
        auth = f"""\
        - name: auth
          image: {OAUTH2_PROXY_IMAGE}
          args: ["--config", "/etc/cairn/oauth2-proxy.cfg"]
          ports:
            - containerPort: 4180
              name: auth
          env:
            - name: OAUTH2_PROXY_CLIENT_ID
              valueFrom:
                secretKeyRef:
                  name: {contract.name}-oidc
                  key: client-id
            - name: OAUTH2_PROXY_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: {contract.name}-oidc
                  key: client-secret
            - name: OAUTH2_PROXY_COOKIE_SECRET
              valueFrom:
                secretKeyRef:
                  name: {contract.name}-oidc
                  key: cookie-secret
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: config
              mountPath: /etc/cairn/oauth2-proxy.cfg
              subPath: oauth2-proxy.cfg
              readOnly: true
{email_mount}            - name: tmp
              mountPath: /tmp
"""
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {contract.name}-edge
  labels:
    cairn.dev/app: {contract.name}
    cairn.dev/role: edge
spec:
  replicas: 1
  selector:
    matchLabels:
      cairn.dev/app: {contract.name}
      cairn.dev/role: edge
  template:
    metadata:
      labels:
        cairn.dev/app: {contract.name}
        cairn.dev/role: edge
    spec:
      automountServiceAccountToken: false
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: edge
          image: {CADDY_IMAGE}
          command: ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
          ports:
            - containerPort: {contract.edge.bind_port}
              name: http
          readinessProbe:
            tcpSocket:
              port: http
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: config
              mountPath: /etc/caddy/Caddyfile
              subPath: Caddyfile
              readOnly: true
            - name: data
              mountPath: /data
            - name: configdir
              mountPath: /config
            - name: tmp
              mountPath: /tmp
{auth}      volumes:
        - name: config
          configMap:
            name: {contract.name}-edge
        - name: data
          emptyDir: {{}}
        - name: configdir
          emptyDir: {{}}
        - name: tmp
          emptyDir: {{}}
"""


def _service(contract: AppContract, role: str, port: int, labels: dict[str, str]) -> str:
    label_lines = "\n".join(f"    cairn.dev/{key}: {value}" for key, value in labels.items() if key != "app")
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {contract.name}-{role}
  labels:
    cairn.dev/app: {contract.name}
{label_lines}
spec:
  selector:
    cairn.dev/app: {contract.name}
    cairn.dev/role: {role}
  ports:
    - name: http
      port: {port}
      targetPort: http
"""


def _policy(contract: AppContract) -> str:
    return f"""\
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {contract.name}-app
  labels:
    cairn.dev/app: {contract.name}
spec:
  podSelector:
    matchLabels:
      cairn.dev/app: {contract.name}
      cairn.dev/role: app
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              cairn.dev/app: {contract.name}
              cairn.dev/role: edge
      ports:
        - protocol: TCP
          port: {contract.port}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {contract.name}-edge
  labels:
    cairn.dev/app: {contract.name}
spec:
  podSelector:
    matchLabels:
      cairn.dev/app: {contract.name}
      cairn.dev/role: edge
  policyTypes:
    - Ingress
  ingress:
    - ports:
        - protocol: TCP
          port: {contract.edge.bind_port}
"""


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else "" for line in text.splitlines())

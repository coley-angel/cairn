# Security policy

Cairn's security properties are described in [docs/threat-model.md](docs/threat-model.md). Read that before changing the Caddyfile, the oauth2-proxy config, or the Kubernetes sidecar.

Please do not include live client secrets, cookie secrets, or certificates in a public issue or pull request. A report can name the contract fields and the header you observed.

Cairn does not ship certificates. Local TLS uses Caddy's runtime internal CA. Production certificates should be inspected with `openssl x509 -text -noout` before they are trusted.

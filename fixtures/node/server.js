const http = require("http");

const port = Number(process.env.PORT || 3000);

const server = http.createServer((req, res) => {
  const path = (req.url || "/").split("?")[0];
  if (path === "/healthz") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok\n");
    return;
  }
  if (path === "/whoami" || path === "/edge-probe") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end(req.headers["x-auth-request-user"] || "none");
    return;
  }
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found\n");
});

server.listen(port, "0.0.0.0");

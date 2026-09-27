"""HTTP 服务：静态页面 + /api/audit + /healthz。

仅使用 Python 标准库（容器内零额外依赖）。
端口由环境变量 ``PORT``（默认 8080）配置，Compose 映射为可配置宿主机端口。
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

sys.path.insert(0, str(BASE_DIR))
from validator import validate  # noqa: E402
from solver import Solver  # noqa: E402

MAX_BODY = 1 << 20


def run_audit(stations_text, script_text):
    parsed = validate(stations_text or "", script_text or "")
    if parsed["errors"]:
        # 存在任何校验错误：不产出审计结论（旧结果由前端清除）
        return {"ok": False, "errors": parsed["errors"],
                "checkpoints": []}
    result = Solver(names=parsed["names"], ops=parsed["ops"]).solve()
    return {"ok": True, "errors": [], **result}


class Handler(BaseHTTPRequestHandler):
    server_version = "ObservationCable/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] %s - %s\n"
                         % (self.address_string(), fmt % args))

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, name, content_type):
        try:
            body = (STATIC_DIR / name).read_bytes()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "cable-audit"})
        elif path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._serve_file("app.js", "application/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._serve_file("styles.css", "text/css; charset=utf-8")
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/audit":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send_json(413, {"error": "payload too large"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return
        result = run_audit(data.get("stations", ""), data.get("script", ""))
        self._send_json(200, result)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"cable-audit listening on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""裁决服务 HTTP 入口（仅依赖 Python 标准库）。

路由：
  GET  /healthz         健康检查
  GET  /api/sample      内置示例草稿
  POST /api/adjudicate  提交草稿执行裁决
  GET  /                前端静态资源（构建产物目录由 STATIC_DIR 指定）
"""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND_DIR))

from app.sample import example_payload  # noqa: E402
from app.solver import AdjudicationError, adjudicate  # noqa: E402

STATIC_DIR = Path(os.environ.get(
    "STATIC_DIR",
    _BACKEND_DIR.parent / "frontend",
)).resolve()

MAX_BODY_BYTES = 1 << 20  # 1 MiB 足够承载 8 点 × 3 候选的草稿

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".map": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "DomeAdjudicator/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ------------------------------------------------------------ 响应工具
    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel: str) -> None:
        # 防目录穿越：规范化后必须仍在 STATIC_DIR 内
        base = Path(rel)
        if rel == "" or rel.endswith("/"):
            base = base / "index.html"
        target = (STATIC_DIR / base).resolve()
        try:
            target.relative_to(STATIC_DIR)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Cache-Control",
            "no-cache" if target.name == "index.html" else "public, max-age=300",
        )
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------ 路由
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/healthz":
            self._json({"status": "ok"})
        elif path == "/api/sample":
            self._json(example_payload())
        elif path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND, "未知的 API 路径")
        else:
            self._send_static(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/adjudicate":
            self.send_error(HTTPStatus.NOT_FOUND, "未知的 API 路径")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Content-Length 不合法"}, HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json({"error": "请求体为空或超过 1 MiB 上限"},
                       HTTPStatus.BAD_REQUEST)
            return
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json({"error": f"JSON 解析失败：{exc}"},
                       HTTPStatus.BAD_REQUEST)
            return
        try:
            report = adjudicate(data)
        except AdjudicationError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:  # pragma: no cover - 防御性
            self._json({"error": f"服务端内部错误：{exc}"},
                       HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._json(report)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"裁决服务监听 http://{host}:{port}（静态目录 {STATIC_DIR}）",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

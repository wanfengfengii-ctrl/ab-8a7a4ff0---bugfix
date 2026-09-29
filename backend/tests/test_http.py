"""HTTP API 集成测试：健康检查、示例、裁决、错误响应与静态资源。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import importlib.util
import sys
from pathlib import Path

_SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"
spec = importlib.util.spec_from_file_location("dome_server", _SERVER_PATH)
server_mod = importlib.util.module_from_spec(spec)
sys.modules["dome_server"] = server_mod
spec.loader.exec_module(server_mod)


@pytest.fixture()
def http_server(tmp_path):
    # 静态目录指向临时目录，避免依赖前端构建产物
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>t</title>", encoding="utf-8")
    server_mod.STATIC_DIR = tmp_path
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_healthz(http_server):
    status, ctype, body = _get(http_server, "/healthz")
    assert status == 200 and "json" in ctype
    assert json.loads(body) == {"status": "ok"}


def test_sample_endpoint_shape(http_server):
    status, _, body = _get(http_server, "/api/sample")
    data = json.loads(body)
    assert status == 200
    assert 5 <= len(data["points"]) <= 8
    assert len(data["candidates"]) == len(data["points"])
    assert all(2 <= len(g) <= 3 for g in data["candidates"])


def test_sample_adjudication_smoke(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    status, report = _post(http_server, "/api/adjudicate", sample)
    assert status == 200
    assert report["feasible"] is True
    assert len(report["choice"]) == len(sample["points"])
    assert len(report["assignment"]) == len(sample["points"])
    assert all("rejected" in row for row in report["assignment"])
    assert all("actualSq" in row for row in report["edges"])
    assert all("actualSignedArea2" in row for row in report["triangles"])


def test_bad_payload_returns_400(http_server):
    status, body = _post(http_server, "/api/adjudicate", {"points": []})
    assert status == 400
    assert "5 至 8" in body["error"]


def test_malformed_json_returns_400(http_server):
    req = urllib.request.Request(
        http_server + "/api/adjudicate",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_static_index_and_path_traversal(http_server):
    status, ctype, body = _get(http_server, "/")
    assert status == 200 and b"doctype" in body
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(
            http_server + "/..%2f..%2fetc%2fpasswd", timeout=5)
    assert exc_info.value.code in (403, 404)


# ----------------------------------------------------- 分段落位复核

def test_adjudicate_default_response_omits_staged_field(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    status, report = _post(http_server, "/api/adjudicate", sample)
    assert status == 200
    assert "stagedReview" not in report


def test_staged_review_sample_returns_safe_order(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    sample["stagedReview"] = True
    status, report = _post(http_server, "/api/adjudicate", sample)
    assert status == 200
    sr = report["stagedReview"]
    assert sr["enabled"] is True and sr["feasible"] is True
    assert sorted(sr["order"]) == list(range(len(sample["points"])))
    assert len(sr["steps"]) == len(sample["points"])
    for i, st in enumerate(sr["steps"], start=1):
        assert st["step"] == i
        assert {"step", "pointIndex", "pointName", "landed",
                "affectedTriangles", "affectedEdges",
                "crossChecks", "cumulative"} <= set(st)
        assert all(t["preserved"] for t in st["affectedTriangles"])
        assert all(e["within"] for e in st["affectedEdges"])
        assert all(not c["intersect"] for c in st["crossChecks"])
        assert len(st["cumulative"]["placed"]) == i


def test_staged_review_bad_flag_returns_400(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    sample["stagedReview"] = "yes"
    status, body = _post(http_server, "/api/adjudicate", sample)
    assert status == 400 and "stagedReview" in body["error"]


def test_staged_review_baseline_crossing_rejected(http_server):
    # 基线边 A–D（0–3）与 B–C（1–2）不共端点且正向相交，A 右移可在终态
    # 消除交叉：静态裁决可行，但启用分段复核必须以 baseline-infeasible 拒绝。
    payload = {
        "points": [
            {"name": "A", "x": 0, "y": 0},
            {"name": "B", "x": 9, "y": 0},
            {"name": "C", "x": 0, "y": 9},
            {"name": "D", "x": 9, "y": 9},
            {"name": "E", "x": 30, "y": 30},
        ],
        "triangles": [[1, 2, 4]],
        "edges": [
            {"endpoints": [0, 3], "minSq": 0, "maxSq": 10_000_000},
            {"endpoints": [1, 2], "minSq": 0, "maxSq": 10_000_000},
        ],
        "candidates": [
            [[0, 0], [10, 0]],
            [[0, 0], [1, 1]],
            [[0, 0], [-1, 1]],
            [[0, 0], [0, 1]],
            [[0, 0], [1, 0]],
        ],
    }
    # 关闭分段复核：静态最终几何语义不变，仍可行
    status, static = _post(http_server, "/api/adjudicate", payload)
    assert status == 200
    assert static["feasible"] is True
    assert "stagedReview" not in static
    # 启用分段复核：基线起点交叉，稳定判负并给出基线交叉证据
    payload["stagedReview"] = True
    status, report = _post(http_server, "/api/adjudicate", payload)
    assert status == 200
    assert report["feasible"] is False
    sr = report["stagedReview"]
    assert sr["feasible"] is False
    assert sr["reason"] == "baseline-infeasible"
    bv = sr["baselineViolation"]
    assert bv["kind"] == "crossing" and bv["touching"] is False
    assert {bv["edgeA"], bv["edgeB"]} == {0, 1}
    assert bv["coordsA"] == [[0, 0], [9, 9]]
    assert bv["coordsB"] == [[9, 0], [0, 9]]

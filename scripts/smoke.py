"""裁决 API 冒烟脚本（仅用标准库，供本地与 Compose verify 服务调用）。

检查项：
  1. /healthz 健康；
  2. 内置示例经 /api/adjudicate 裁决可行，证据字段完整；
  3. 构造一个无解草稿（把一条边锁到不可能的长度平方），
     接口必须稳定返回 feasible=false 及首个违约证据，而非伪结论；
  4. 非法草稿返回 400；
  5. 静态首页可访问；
  6. 未启用分段落位复核时响应不含 stagedReview 字段；启用后示例给出
     安全落位次序与逐步证据；构造的死锁草稿稳定报告最早受阻步骤。

任一检查失败即以非零退出码结束。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _request(method: str, url: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, ctype, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    # 1) 健康检查
    status, _, raw = _request("GET", f"{base}/healthz")
    check("GET /healthz 返回 200", status == 200, f"HTTP {status}")
    check("健康体 status=ok", json.loads(raw)["status"] == "ok")

    # 2) 内置示例可行
    status, _, raw = _request("GET", f"{base}/api/sample")
    check("GET /api/sample 返回 200", status == 200, f"HTTP {status}")
    sample = json.loads(raw)
    n = len(sample["points"])
    check("示例基准点数量在 5~8", 5 <= n <= 8, f"n={n}")
    check("每点候选位移 2~3 条",
          all(2 <= len(g) <= 3 for g in sample["candidates"]))

    status, _, raw = _request("POST", f"{base}/api/adjudicate", sample)
    check("示例裁决返回 200", status == 200, f"HTTP {status}")
    report = json.loads(raw)
    check("示例存在可行方案", report["feasible"] is True)
    check("每点恰选一条位移", len(report["choice"]) == n
          and all(0 <= c < len(sample["candidates"][i])
                  for i, c in enumerate(report["choice"])))
    check("采用/未采用候选证据完整",
          all({"candidateIndex", "displacement", "displacementSq",
               "displaced", "rejected"} <= set(row)
              for row in report["assignment"]))
    check("各边长度平方均在闭区间内",
          all(row["within"] for row in report["edges"]))
    check("各三角面朝向均严格保持",
          all(row["preserved"] for row in report["triangles"]))
    check("非共端边无相交/相切", report["conflicts"] == [])
    obj = report["objective"]
    check("优化目标自洽（平方和 ≥ 最大值）",
          obj["sumSq"] >= obj["maxSq"] >= 0, json.dumps(obj, ensure_ascii=False))

    # 3) 无解草稿：第一条边锁死为 999999，小幅候选位移无法满足
    impossible = json.loads(json.dumps(sample))
    first_edge = impossible["edges"][0]
    first_edge["minSq"] = 999_999
    first_edge["maxSq"] = 999_999
    status, _, raw = _request("POST", f"{base}/api/adjudicate", impossible)
    check("无解草稿返回 200（裁决结果而非服务错误）", status == 200)
    bad = json.loads(raw)
    check("裁决明确报告 feasible=false", bad["feasible"] is False)
    v = bad.get("firstViolation")
    check("附带首个违约证据", isinstance(v, dict)
          and {"kind", "message", "choice"} <= set(v))
    check("证据指向被锁死边长的长度约束",
          v["kind"] == "length" and v["edgeIndex"] == 0,
          f"kind={v['kind']} actualSq={v.get('actualSq')}")
    check("证据组合长度恰为点数", len(v["choice"]) == n)
    check("证据几何表中该边确实越界",
          bad["edges"][0]["within"] is False
          and bad["edges"][0]["actualSq"] == v["actualSq"])

    # 4) 非法草稿 400
    status, _, raw = _request("POST", f"{base}/api/adjudicate", {"points": []})
    check("非法草稿返回 400", status == 400, f"HTTP {status}")
    check("400 响应给出中文原因", "error" in json.loads(raw))

    # 5) 静态首页
    status, ctype, raw = _request("GET", f"{base}/")
    check("GET / 返回 200 HTML",
          status == 200 and "html" in ctype, f"HTTP {status} {ctype}")
    check("首页为裁决页面",
          "基线网位移联合裁决".encode() in raw)

    # 6) 分段落位复核（stagedReview）
    check("未启用时响应不含 stagedReview 字段",
          "stagedReview" not in report)

    staged_sample = json.loads(json.dumps(sample))
    staged_sample["stagedReview"] = True
    status, _, raw = _request("POST", f"{base}/api/adjudicate", staged_sample)
    check("启用复核的示例裁决返回 200", status == 200, f"HTTP {status}")
    staged_report = json.loads(raw)
    sr = staged_report["stagedReview"]
    check("复核标志回显 enabled", sr.get("enabled") is True)
    check("示例存在安全落位次序", sr["feasible"] is True and sr["reason"] == "ok")
    check("安全次序为每点恰一次的排列",
          sorted(sr["order"]) == list(range(n)) and len(sr["steps"]) == n)
    check("逐步证据含新落位点/受影响面边边对/累计状态",
          all({"pointIndex", "landed", "affectedTriangles",
               "affectedEdges", "crossChecks", "cumulative"} <= set(st)
              for st in sr["steps"]))
    check("每一步混合网均满足朝向/边长/不相交",
          all(all(t["preserved"] for t in st["affectedTriangles"])
              and all(e["within"] for e in st["affectedEdges"])
              and all(not c["intersect"] for c in st["crossChecks"])
              for st in sr["steps"]))
    check("末步累计几何等于最终几何",
          sr["steps"][-1]["cumulative"]["movedPoints"]
          == staged_report["movedPoints"])

    # 死锁草稿：两条非共端边的可动端 P、Q 必须“同时”落位才不相交，
    # 单点落位必相交——最终几何可行但不存在安全次序。
    def mv(a, b):
        return [b[0] - a[0], b[1] - a[1]]
    p0, q0 = (1, 3), (2, 1)
    deadlock = {
        "points": [
            {"name": "A", "x": 0, "y": 0},
            {"name": "B", "x": 0, "y": 10},
            {"name": "P", "x": p0[0], "y": p0[1]},
            {"name": "Q", "x": q0[0], "y": q0[1]},
            {"name": "E", "x": 30, "y": 30},
        ],
        "triangles": [[0, 1, 4]],
        "edges": [
            {"endpoints": [0, 2], "minSq": 0, "maxSq": 10_000_000},
            {"endpoints": [1, 3], "minSq": 0, "maxSq": 10_000_000},
        ],
        "candidates": [
            [[0, 0], [-10, 10]],
            [[0, 0], [-10, 10]],
            [mv(p0, (3, 2)), mv(p0, (4, 2))],
            [mv(q0, (1, 1)), mv(q0, (1, 2))],
            [[0, 0], [1, 0]],
        ],
        "stagedReview": True,
    }
    status, _, raw = _request("POST", f"{base}/api/adjudicate", deadlock)
    check("死锁草稿返回 200（裁决结果而非服务错误）", status == 200)
    dsr = json.loads(raw)["stagedReview"]
    check("死锁草稿明确无安全次序",
          dsr["feasible"] is False and dsr["reason"] == "no-safe-order")
    b = dsr["blocking"]
    check("稳定指出最早受阻步骤（第 4 步）与已落位集合",
          b["step"] == 4 and b["placedNames"] == ["A", "B", "E"],
          json.dumps({k: b.get(k) for k in ("step", "placedNames")},
                     ensure_ascii=False))
    tried = {t["pointName"]: t for t in b["triedCandidates"]}
    check("受阻时列出全部尝试点候选", set(tried) == {"P", "Q"})
    check("每个尝试点给出首条被破坏约束（非共端边相交）",
          all(t["violation"]["kind"] == "crossing"
              and {t["violation"]["edgeA"], t["violation"]["edgeB"]} == {0, 1}
              for t in b["triedCandidates"]))
    check("blocking 含首条违约与尝试点的精确边证据",
          b["firstViolation"]["kind"] == "crossing"
          and any(c["intersect"] for t in b["triedCandidates"]
                  for c in t["crossChecks"]))

    bad_flag = json.loads(json.dumps(sample))
    bad_flag["stagedReview"] = "true"
    status, _ctype, raw = _request("POST", f"{base}/api/adjudicate", bad_flag)
    check("stagedReview 非布尔值返回 400", status == 400, f"HTTP {status}")
    check("400 原因指向 stagedReview", "stagedReview" in json.loads(raw)["error"])

    print("\n全部冒烟检查通过。")


if __name__ == "__main__":
    main()

"""分段落位复核（stagedReview）单元测试。

覆盖：
  - 未启用时响应完全不出现 stagedReview 字段（既有行为保持原样）；
  - 启用后零位移方案的逐步证据与累计状态；
  - 逐步峰值最小、点序列字典序最小（与全排列暴力结果一致）；
  - 联合裁决：静态最优但无法安全落位的方案被跳过，改取可落位的次优方案；
  - 无安全次序时稳定给出最早受阻步骤、已落位集合、尝试点候选、首条违约；
  - 静态本就无解时不进入排序；
  - 录入校验（stagedReview 必须为布尔值）。
"""

from __future__ import annotations

import itertools
import json

import pytest

from app.solver import (
    AdjudicationError,
    _check_placement,
    _signed_area2,
    _target_positions,
    adjudicate,
)


# --------------------------------------------------------------- 构造器

def _mv(a, b):
    return [b[0] - a[0], b[1] - a[1]]


def _barrier_payload(*, p0=(1, 3), q0=(2, 1),
                     p_finals=((3, 2), (4, 2)),
                     q_finals=((1, 1), (1, 2)),
                     avec=(-10, 10), bvec=(-10, 10), evec=(1, 0)):
    """A/B 为两条登记边的固定端，P/Q 为可动端；基线 A-P 与 B-Q 不相交。

    p_finals/q_finals 给出 P/Q 的候选落位，满足：任一动端单独落位都会
    让两线相交，而四个最终组合两两不相交——即“必须同时落位”的死锁。
    """
    points = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 0, "y": 10},
        {"name": "P", "x": p0[0], "y": p0[1]},
        {"name": "Q", "x": q0[0], "y": q0[1]},
        {"name": "E", "x": 30, "y": 30},
    ]
    edges = [
        {"endpoints": [0, 2], "minSq": 0, "maxSq": 10_000_000},
        {"endpoints": [1, 3], "minSq": 0, "maxSq": 10_000_000},
    ]
    candidates = [
        [[0, 0], list(avec)],
        [[0, 0], list(bvec)],
        [_mv(p0, z) for z in p_finals],
        [_mv(q0, z) for z in q_finals],
        [[0, 0], list(evec)],
    ]
    return {
        "points": points,
        "triangles": [[0, 1, 4]],
        "edges": edges,
        "candidates": candidates,
    }


def _staged(payload):
    p = json.loads(json.dumps(payload))
    p["stagedReview"] = True
    return adjudicate(p)


# --------------------------------------------------------------- 未启用保持原样

def test_disabled_response_has_no_staged_field():
    report = adjudicate(_barrier_payload())
    assert "stagedReview" not in report


def test_explicit_false_response_has_no_staged_field():
    p = _barrier_payload()
    p["stagedReview"] = False
    report = adjudicate(p)
    assert "stagedReview" not in report


def test_staged_flag_must_be_boolean():
    p = _barrier_payload()
    p["stagedReview"] = "true"
    with pytest.raises(AdjudicationError, match="stagedReview"):
        adjudicate(p)
    p["stagedReview"] = 1
    with pytest.raises(AdjudicationError, match="stagedReview"):
        adjudicate(p)


# --------------------------------------------------------------- 零位移逐步证据

def test_zero_displacement_staged_steps():
    from app.sample import example_payload
    p = example_payload()
    p["stagedReview"] = True
    report = adjudicate(p)
    sr = report["stagedReview"]
    n = len(p["points"])
    assert sr["feasible"] is True
    assert sr["reason"] == "ok"
    assert sorted(sr["order"]) == list(range(n))
    assert sr["stepPeakSq"] == [0] * n
    assert len(sr["steps"]) == n
    # 每一步：新落位点、受影响面/边/边对证据齐全且全部通过
    for st in sr["steps"]:
        pi = st["pointIndex"]
        assert st["landed"] == [report["points"][pi]["x"],
                                report["points"][pi]["y"]]
        assert st["displacement"] == [0, 0] and st["displacementSq"] == 0
        assert all(t["preserved"] for t in st["affectedTriangles"])
        assert all(e["within"] for e in st["affectedEdges"])
        assert all(not c["intersect"] for c in st["crossChecks"])
        cum = st["cumulative"]
        assert cum["peakSq"] == 0
        assert len(cum["placed"]) == st["step"]
        # 累计混合网：已落位 == 终态，未落位 == 基线（此处全部零位移）
        assert cum["movedPoints"] == report["movedPoints"]
    # 末步累计坐标恰为最终报告几何
    assert sr["steps"][-1]["cumulative"]["movedPoints"] == report["movedPoints"]


# --------------------------------------------------------------- 次序最优性（暴力核对）

def _bruteforce_best_order(payload, choice):
    pts = payload["points"]
    n = len(pts)
    origin = [(q["x"], q["y"]) for q in pts]
    cands = payload["candidates"]
    target = _target_positions(choice, origin, cands)
    tris, edges = payload["triangles"], payload["edges"]
    base_area = [_signed_area2(origin, t) for t in tris]
    edge_pairs = [(i, j) for i in range(len(edges)) for j in range(i + 1, len(edges))
                  if not (set(edges[i]["endpoints"]) & set(edges[j]["endpoints"]))]
    kw = dict(points=pts, triangles=tris, edges=edges, candidates=cands,
              base_area=base_area, edge_pairs=edge_pairs, target=target)
    tri_aff = {q: [ti for ti, t in enumerate(tris) if q in t] for q in range(n)}
    edge_aff = {q: [ei for ei, e in enumerate(edges) if q in e["endpoints"]]
                for q in range(n)}
    pair_aff = {q: [(ei, ej) for ei, ej in edge_pairs
                    if q in edges[ei]["endpoints"] or q in edges[ej]["endpoints"]]
                for q in range(n)}
    dsq = [cands[i][choice[i]][0] ** 2 + cands[i][choice[i]][1] ** 2
           for i in range(n)]
    feasible = []
    for perm in itertools.permutations(range(n)):
        pos = list(origin)
        peaks, peak = [], -1
        for q in perm:
            v, _f = _check_placement(
                q, pos, tri_idx=tri_aff[q], edge_idx=edge_aff[q],
                pair_idx=pair_aff[q], **kw)
            if v is not None:
                break
            pos[q] = target[q]
            peak = max(peak, dsq[q])
            peaks.append(peak)
        else:
            feasible.append((tuple(peaks), perm))
    return min(feasible)


def test_order_optimal_matches_bruteforce():
    # 联合裁决将选出 P 取大位移 (-4,0) 的方案，且强制 P 先于 Q；
    # 与全排列暴力最优一致。
    payload = _barrier_payload(
        p0=(1, 2), q0=(2, 1),
        p_finals=((3, 2), (-4, 0)),
        q_finals=((1, 1), (0, -4)),
        evec=(0, 1))
    report = _staged(payload)
    sr = report["stagedReview"]
    assert sr["feasible"] is True
    expected_peaks, expected_order = _bruteforce_best_order(
        payload, report["choice"])
    assert tuple(sr["stepPeakSq"]) == expected_peaks
    assert tuple(sr["order"]) == expected_order
    # 逐步位移平方序列与点序列一致
    assert [s["displacementSq"] for s in sr["steps"]] == [
        report["assignment"][q]["displacementSq"] for q in sr["order"]]


def test_joint_selection_skips_static_best_unsequenceable():
    payload = _barrier_payload(
        p0=(1, 2), q0=(2, 1),
        p_finals=((3, 2), (-4, 0)),
        q_finals=((1, 1), (0, -4)),
        evec=(0, 1))
    # 未启用：静态最优是 P 取小位移 (2,0)（平方 4），且该方案无法安全落位
    static = adjudicate(json.loads(json.dumps(payload)))
    assert static["choice"][2] == 0
    assert static["objective"]["maxSq"] == 4
    # 启用：联合裁决改取 P 的候选 1（平方 29），并保证 P 先于 Q
    report = _staged(payload)
    assert report["choice"][2] == 1
    assert report["objective"]["maxSq"] == 29
    order = report["stagedReview"]["order"]
    assert order.index(2) < order.index(3)
    # 联合裁决确实先评估了静态最优组合、再落到可排序组合
    info = report["stagedReview"]["search"]
    assert info["orderSearchedCombinations"] >= 2
    assert info["finalFeasibleCombinations"] >= 2


# --------------------------------------------------------------- 无安全次序证据

def test_no_safe_order_blocking_evidence():
    payload = _barrier_payload()  # 标准四点死锁 + E
    # 未启用时静态可行（最终几何全部合格）
    assert adjudicate(json.loads(json.dumps(payload)))["feasible"] is True
    report = _staged(payload)
    sr = report["stagedReview"]
    assert sr["feasible"] is False
    assert sr["reason"] == "no-safe-order"
    assert sr["order"] is None and sr["steps"] == []
    b = sr["blocking"]
    assert b["kind"] == "staging-blocked"
    # 最早受阻步骤：A、B、E 可先落位，第 4 步 P、Q 均无法落位
    assert b["step"] == 4
    assert b["placedCount"] == 3
    assert b["placedNames"] == ["A", "B", "E"]
    assert b["remainingPointCount"] == 2
    tried = {t["pointName"]: t for t in b["triedCandidates"]}
    assert set(tried) == {"P", "Q"}
    # 每个尝试点都给出候选、拟落位坐标与首条被破坏约束
    first_kinds = []
    for t in b["triedCandidates"]:
        assert t["wouldLand"]
        assert t["displacementSq"] >= 0
        v = t["violation"]
        assert v["kind"] == "crossing"
        assert {v["edgeA"], v["edgeB"]} == {0, 1}
        # 精确边证据含步后坐标与相交判定
        assert any(c["intersect"] for c in t["crossChecks"])
        first_kinds.append(v["kind"])
    assert b["firstViolation"]["kind"] == first_kinds[0]
    assert "第 4 步" in b["message"] and "首条被破坏约束" in b["message"]
    # 终态证据锚定静态最优方案
    assert report["feasible"] is False
    assert report["firstViolation"] is None
    assert all(t["preserved"] for t in report["triangles"])
    assert report["conflicts"] == []


def test_no_safe_order_is_deterministic():
    payload = _barrier_payload()
    r1 = _staged(payload)
    r2 = _staged(payload)
    b1 = json.dumps(r1["stagedReview"]["blocking"], sort_keys=True,
                    ensure_ascii=False)
    b2 = json.dumps(r2["stagedReview"]["blocking"], sort_keys=True,
                    ensure_ascii=False)
    assert b1 == b2


# --------------------------------------------------------------- 施工起点（基线网）违约

def _baseline_crossing_payload():
    """五点草稿：登记边 A–D 与 B–C 不共享端点且在基线坐标正向相交。

    交点为 (2,2)（非相切）；A 取候选 (-10,10) 后 A'–D 与 B–C 不再相交，
    最终组合静态可行——但启用分段复核时施工起点即不安全，必须拒绝。
    """
    def mv(a, b):
        return [b[0] - a[0], b[1] - a[1]]

    return {
        "points": [
            {"name": "A", "x": 0, "y": 0},
            {"name": "B", "x": 0, "y": 4},
            {"name": "C", "x": 4, "y": 0},
            {"name": "D", "x": 4, "y": 4},
            {"name": "E", "x": 30, "y": -30},
        ],
        "triangles": [[0, 1, 4]],
        "edges": [
            {"endpoints": [0, 3], "minSq": 0, "maxSq": 10_000_000},
            {"endpoints": [1, 2], "minSq": 0, "maxSq": 10_000_000},
        ],
        "candidates": [
            [[0, 0], [-10, 10]],
            [[0, 0], [1, 0]],
            [[0, 0], [0, 1]],
            [[0, 0], [1, 1]],
            [[0, 0], [1, 0]],
        ],
    }


def test_baseline_crossing_statically_feasible():
    # 前置条件：最终组合可合法（A 的目标位移消除交叉），
    # 关闭分段复核时既有静态最终几何裁决语义不变。
    payload = _baseline_crossing_payload()
    static = adjudicate(json.loads(json.dumps(payload)))
    assert static["feasible"] is True
    assert static["choice"] == [1, 0, 0, 0, 0]
    assert static["conflicts"] == []
    assert "stagedReview" not in static

    payload["stagedReview"] = False
    static_explicit = adjudicate(json.loads(json.dumps(payload)))
    assert static_explicit["feasible"] is True
    assert "stagedReview" not in static_explicit


def test_baseline_crossing_rejected_with_baseline_evidence():
    payload = _baseline_crossing_payload()
    report = _staged(payload)
    assert report["feasible"] is False
    sr = report["stagedReview"]
    assert sr["feasible"] is False
    assert sr["reason"] == "baseline-unsafe"
    assert sr["order"] is None and sr["steps"] == []
    b = sr["blocking"]
    assert b["kind"] == "baseline-unsafe"
    assert b["step"] == 0
    v = b["firstViolation"]
    assert v["kind"] == "crossing"
    assert {v["edgeA"], v["edgeB"]} == {0, 1}
    assert v["endpointsA"] == [0, 3] and v["endpointsB"] == [1, 2]
    assert v["touching"] is False  # 正向相交而非单点相切
    assert "基线" in v["message"] and "相交" in v["message"]
    # 基线交叉证据：整数坐标与相交判定
    hits = [c for c in b["baselineChecks"]["crossChecks"] if c["intersect"]]
    assert len(hits) == 1
    hit = hits[0]
    assert {hit["edgeA"], hit["edgeB"]} == {0, 1}
    assert hit["coordsA"] == [[0, 0], [4, 4]]
    assert hit["coordsB"] == [[0, 4], [4, 0]]
    assert hit["touching"] is False
    # 未进入任何次序搜索
    info = sr["search"]
    assert info["orderSearchedCombinations"] == 0
    assert info["orderSearchVisitedStates"] == 0
    # 终态字段锚定基线网（零位移），不锚定候选组合
    assert report["choice"] is None and report["objective"] is None
    assert report["firstViolation"] is None
    assert report["movedPoints"] == [[p["x"], p["y"]]
                                     for p in payload["points"]]
    assert len(report["conflicts"]) == 1
    assert {report["conflicts"][0]["edgeA"],
            report["conflicts"][0]["edgeB"]} == {0, 1}
    assert all(row["candidateIndex"] is None for row in report["assignment"])


def test_baseline_crossing_result_is_deterministic():
    payload = _baseline_crossing_payload()
    r1 = _staged(payload)
    r2 = _staged(payload)
    assert json.dumps(r1, sort_keys=True, ensure_ascii=False) == json.dumps(
        r2, sort_keys=True, ensure_ascii=False)


def test_baseline_length_violation_rejected_even_if_final_feasible():
    # 基线下 A–D 长度平方为 25；把下限抬到 33，终态 A 取 (-10,10) 后
    # A'–D 长度平方 85，静态可行；但施工起点边长即越界。
    # D=(-3,4)：A–D 全程位于直线 x+y=4（B–C）下方，两线不相交，
    # 故起点首个违约为边长而非相交。
    payload = _baseline_crossing_payload()
    payload["points"][3] = {"name": "D", "x": -3, "y": 4}
    payload["edges"][0]["minSq"] = 33
    payload["edges"][0]["maxSq"] = 10_000_000
    static = adjudicate(json.loads(json.dumps(payload)))
    assert static["feasible"] is True
    assert static["choice"][0] == 1
    report = _staged(payload)
    sr = report["stagedReview"]
    assert sr["feasible"] is False
    assert sr["reason"] == "baseline-unsafe"
    v = sr["blocking"]["firstViolation"]
    assert v["kind"] == "length"
    assert v["edgeIndex"] == 0 and v["actualSq"] == 25
    assert v["minSq"] == 33
    bad_edges = [e for e in sr["blocking"]["baselineChecks"]["edges"]
                 if not e["within"]]
    assert len(bad_edges) == 1 and bad_edges[0]["edgeIndex"] == 0
    assert not any(c["intersect"]
                   for c in sr["blocking"]["baselineChecks"]["crossChecks"])


def test_safe_baseline_still_gets_ok_conclusion():
    # 安全基线草稿（内置示例）启用复核后结论不变：存在安全次序
    from app.sample import example_payload
    p = example_payload()
    p["stagedReview"] = True
    report = adjudicate(p)
    sr = report["stagedReview"]
    assert report["feasible"] is True
    assert sr["feasible"] is True and sr["reason"] == "ok"
    assert sorted(sr["order"]) == list(range(len(p["points"])))


# --------------------------------------------------------------- 静态本就无解

def test_static_infeasible_short_circuits_staging():
    from app.sample import example_payload
    p = example_payload()
    p["edges"][0]["minSq"] = 999_999
    p["edges"][0]["maxSq"] = 999_999
    p["stagedReview"] = True
    report = adjudicate(p)
    sr = report["stagedReview"]
    assert report["feasible"] is False
    assert sr["feasible"] is False
    assert sr["reason"] == "static-infeasible"
    assert sr["steps"] == [] and sr["blocking"] is None
    assert sr["search"]["finalFeasibleCombinations"] == 0
    # 静态首个违约证据照常返回
    assert report["firstViolation"]["kind"] == "length"

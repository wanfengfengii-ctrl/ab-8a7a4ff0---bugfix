"""位移联合裁决核心（纯整数几何，零第三方依赖）。

输入（见 ``adjudicate`` 的 JSON 结构）：
  points        : [{"name"? , "x", "y"}]                基准点，5~8 个，整数坐标
  triangles     : [[i, j, k], ...]                      已登记三角面（点索引）
  edges         : [{"endpoints": [i, j], "minSq", "maxSq"}]
  candidates    : [[[dx, dy], ...], ...]                每点 2~3 条整数候选位移
  stagedReview? : bool                                  启用分段落位复核

约束（硬约束，全部使用整数运算）：
  1. 每点恰选一条候选位移；
  2. 每个三角面位移后的有向面积（两倍）与原基线严格同号——保持严格朝向；
  3. 每条登记边的长度平方落在闭区间 [minSq, maxSq]；
  4. 任意两条不共享端点的边不得相交，亦不得在端点/延长方向上相切。

优化目标（字典序）：
  (a) 各点位移平方的最大值最小；
  (b) 位移平方和最小；
  (c) 按点录入顺序的候选序号字典序最小（序号从 0 起）。

无解时返回 ``feasible=False`` 及确定性的首个违约证据：按候选序号字典序
枚举第一种组合，并在该组合内按 朝向 → 边长 → 边界相交 的顺序取首个失败项。

分段落位复核（stagedReview=true）
--------------------------------
在候选位移与落位顺序的**完整组合空间**中联合裁决：逐点把基准点从基线位置
移至该方案采用的位置，每一步形成的混合网（已落位点取位移后坐标，未落位点
仍取基线坐标）都必须继续满足上述三条硬规则。最终方案仍按 (a)(b)(c) 比较；
静态指标相同的方案不存在（候选序号向量唯一确定方案），但静态最优方案若不
存在安全次序，裁决会继续考察静态上次优、却可安全落位的方案，而**不是**先
选定静态方案再事后试排。次序间再按：
  (d) 逐步位移峰值（各步已落位点位移平方的累计峰值序列）字典序最小；
  (e) 落位点序列（点索引排列）字典序最小。
不存在任何安全次序时，稳定给出最早受阻步骤、已落位集合、尝试点候选与
首条被破坏约束。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any


class AdjudicationError(ValueError):
    """录入草稿不合法（区别于几何上不可行）。"""


# ---------------------------------------------------------------- 基础工具

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cross(ax: int, ay: int, bx: int, by: int) -> int:
    return ax * by - ay * bx


def _dot(ax: int, ay: int, bx: int, by: int) -> int:
    return ax * bx + ay * by


@dataclass(frozen=True)
class _SegHit:
    intersect: bool
    touching: bool  # 交集为单点（端点相接）时为 True；重叠共线为 False


def _segment_hit(a: tuple[int, int], b: tuple[int, int],
                 c: tuple[int, int], d: tuple[int, int]) -> _SegHit:
    """整数判定线段 AB 与 CD 是否相交或相切。

    返回 touching=True 表示交集恰为一个点（端点接触或 T 字相接），
    touching=False 表示存在正向重叠；不相交时 intersect=False。
    """
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    qpx, qpy = c[0] - a[0], c[1] - a[1]
    rxs = _cross(rx, ry, sx, sy)
    qpxr = _cross(qpx, qpy, rx, ry)

    if rxs != 0:
        # 两线不平行：t = cross(QP, s) / rxs，u = cross(QP, r) / rxs
        num_t = _cross(qpx, qpy, sx, sy)
        num_u = qpxr
        if rxs > 0:
            inside = 0 <= num_t <= rxs and 0 <= num_u <= rxs
            boundary = num_t == 0 or num_t == rxs or num_u == 0 or num_u == rxs
        else:
            inside = rxs <= num_t <= 0 and rxs <= num_u <= 0
            boundary = (num_t == 0 or num_t == rxs
                        or num_u == 0 or num_u == rxs)
        if not inside:
            return _SegHit(False, False)
        return _SegHit(True, boundary)

    # 平行：不共线则无交集（r 为零向量时改用 s 判定共线）
    if qpxr != 0:
        return _SegHit(False, False)

    rr = _dot(rx, ry, rx, ry)
    ss = _dot(sx, sy, sx, sy)
    if rr == 0 and ss == 0:
        # 两条都是退化点段：仅当两点重合时相切
        return _SegHit(a == c, a == c)
    if rr == 0:
        # AB 退化为点 A：判定 A 是否落在 CD 上
        if _cross(qpx, qpy, sx, sy) != 0:
            return _SegHit(False, False)
        u = _dot(-qpx, -qpy, sx, sy)  # A 在 CD 上的投影参数（乘 ss）
        inside = 0 <= u <= ss
        return _SegHit(inside, inside)

    # 共线且 AB 非退化：沿 r 方向投影
    if _cross(qpx, qpy, sx, sy) != 0:
        return _SegHit(False, False)
    t0 = _dot(qpx, qpy, rx, ry)          # C 在 AB 上的投影参数（乘 rr）
    t1 = _dot(d[0] - a[0], d[1] - a[1], rx, ry)
    lo, hi = (t0, t1) if t0 <= t1 else (t1, t0)
    overlap_lo, overlap_hi = max(lo, 0), min(hi, rr)
    if overlap_hi < overlap_lo:
        return _SegHit(False, False)
    # 交集为单点即视为相切（CD 为退化点段时同样落到这里）
    return _SegHit(True, overlap_lo == overlap_hi)


# ---------------------------------------------------------------- 录入校验

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise AdjudicationError(msg)


def _validate(data: Any) -> tuple[
        list[dict[str, Any]], list[list[int]], list[dict[str, Any]],
        list[list[list[int]]], bool]:
    _require(isinstance(data, dict), "请求体必须是 JSON 对象")

    raw_points = data.get("points")
    _require(isinstance(raw_points, list) and 5 <= len(raw_points) <= 8,
             "基准点数量必须在 5 至 8 个之间")
    points: list[dict[str, Any]] = []
    for i, p in enumerate(raw_points):
        _require(isinstance(p, dict), f"第 {i + 1} 个基准点格式不正确")
        x, y = p.get("x"), p.get("y")
        _require(_is_int(x) and _is_int(y),
                 f"基准点 {i + 1} 的 x/y 必须为整数")
        name = p.get("name") or f"P{i + 1}"
        _require(isinstance(name, str) and name.strip() != "",
                 f"基准点 {i + 1} 的名称不能为空")
        points.append({"name": name.strip(), "x": x, "y": y})

    n = len(points)

    raw_tris = data.get("triangles")
    _require(isinstance(raw_tris, list) and len(raw_tris) >= 1,
             "至少需要登记一个三角面")
    triangles: list[list[int]] = []
    seen_tri: set[tuple[int, int, int]] = set()
    for i, t in enumerate(raw_tris):
        _require(isinstance(t, list) and len(t) == 3
                 and all(_is_int(v) for v in t),
                 f"第 {i + 1} 个三角面必须是三个整数点索引")
        a, b, c = (int(v) for v in t)
        _require(0 <= a < n and 0 <= b < n and 0 <= c < n,
                 f"三角面 {i + 1} 存在越界点索引")
        _require(len({a, b, c}) == 3,
                 f"三角面 {i + 1} 的三个顶点必须互不相同")
        key = tuple(sorted((a, b, c)))
        _require(key not in seen_tri, f"三角面 {i + 1} 与先前条目重复")
        seen_tri.add(key)
        triangles.append([a, b, c])

    raw_edges = data.get("edges")
    _require(isinstance(raw_edges, list) and len(raw_edges) >= 1,
             "至少需要登记一条边")
    edges: list[dict[str, Any]] = []
    seen_edge: set[frozenset[int]] = set()
    for i, e in enumerate(raw_edges):
        _require(isinstance(e, dict), f"第 {i + 1} 条边格式不正确")
        ep = e.get("endpoints")
        _require(isinstance(ep, list) and len(ep) == 2
                 and all(_is_int(v) for v in ep),
                 f"边 {i + 1} 的 endpoints 必须是两个整数点索引")
        u, v = int(ep[0]), int(ep[1])
        _require(0 <= u < n and 0 <= v < n, f"边 {i + 1} 存在越界点索引")
        _require(u != v, f"边 {i + 1} 的两个端点必须不同")
        key = frozenset((u, v))
        _require(key not in seen_edge, f"边 {i + 1} 与先前条目重复")
        seen_edge.add(key)
        lo, hi = e.get("minSq"), e.get("maxSq")
        _require(_is_int(lo) and _is_int(hi),
                 f"边 {i + 1} 的 minSq/maxSq 必须为整数")
        _require(0 <= lo <= hi,
                 f"边 {i + 1} 的长度平方区间必须满足 0 ≤ minSq ≤ maxSq")
        edges.append({"endpoints": [u, v], "minSq": lo, "maxSq": hi})

    raw_cands = data.get("candidates")
    _require(isinstance(raw_cands, list) and len(raw_cands) == n,
             "candidates 必须为每个基准点提供一组候选位移")
    candidates: list[list[list[int]]] = []
    for i, group in enumerate(raw_cands):
        _require(isinstance(group, list) and 2 <= len(group) <= 3,
                 f"基准点 {points[i]['name']} 必须提供 2 至 3 条候选位移")
        cleaned: list[list[int]] = []
        seen_vec: set[tuple[int, int]] = set()
        for j, vec in enumerate(group):
            _require(isinstance(vec, list) and len(vec) == 2
                     and all(_is_int(w) for w in vec),
                     f"点 {points[i]['name']} 的第 {j + 1} 条候选位移 "
                     "必须是 [dx, dy] 整数对")
            dx, dy = int(vec[0]), int(vec[1])
            _require((dx, dy) not in seen_vec,
                     f"点 {points[i]['name']} 的候选位移 "
                     f"({dx}, {dy}) 重复")
            seen_vec.add((dx, dy))
            cleaned.append([dx, dy])
        candidates.append(cleaned)

    raw_staged = data.get("stagedReview", False)
    _require(isinstance(raw_staged, bool),
             "stagedReview 必须为布尔值 true 或 false")

    return points, triangles, edges, candidates, raw_staged


# ---------------------------------------------------------------- 主裁决

def _signed_area2(p: list[tuple[int, int]], tri: list[int]) -> int:
    a, b, c = tri
    return _cross(p[b][0] - p[a][0], p[b][1] - p[a][1],
                  p[c][0] - p[a][0], p[c][1] - p[a][1])


def _target_positions(choice: tuple[int, ...] | list[int],
                      origin: list[tuple[int, int]],
                      candidates: list[list[list[int]]]
                      ) -> list[tuple[int, int]]:
    return [
        (origin[i][0] + candidates[i][choice[i]][0],
         origin[i][1] + candidates[i][choice[i]][1])
        for i in range(len(origin))
    ]


def _evaluate_choice(choice: tuple[int, ...], *,
                     points: list[dict[str, Any]],
                     triangles: list[list[int]],
                     edges: list[dict[str, Any]],
                     candidates: list[list[list[int]]],
                     origin: list[tuple[int, int]],
                     base_area: list[int],
                     edge_pairs: list[tuple[int, int]]
                     ) -> tuple[dict[str, Any] | None,
                                list[tuple[int, int]]]:
    """评估一个最终（全部点落位后）组合，返回首个违约（无则 None）与终态坐标。

    违约判定次序：朝向 → 边长 → 非共端边相交；同类按录入顺序。
    """
    moved = _target_positions(choice, origin, candidates)
    violation: dict[str, Any] | None = None

    # 1) 严格朝向
    for ti, tri in enumerate(triangles):
        area = _signed_area2(moved, tri)
        if area * base_area[ti] <= 0:
            violation = {
                "kind": "orientation",
                "order": (0, ti),
                "triangleIndex": ti,
                "vertices": tri,
                "originalSignedArea2": base_area[ti],
                "actualSignedArea2": area,
                "message": _orientation_message(ti, tri, points,
                                                 base_area[ti], area),
            }
            break

    # 2) 边长平方闭区间
    if violation is None:
        for ei, e in enumerate(edges):
            u, v = e["endpoints"]
            d2 = ((moved[u][0] - moved[v][0]) ** 2
                  + (moved[u][1] - moved[v][1]) ** 2)
            if d2 < e["minSq"] or d2 > e["maxSq"]:
                side = "小于下限" if d2 < e["minSq"] else "大于上限"
                violation = {
                    "kind": "length",
                    "order": (1, ei),
                    "edgeIndex": ei,
                    "endpoints": [u, v],
                    "minSq": e["minSq"],
                    "maxSq": e["maxSq"],
                    "actualSq": d2,
                    "side": side,
                    "message": (
                        f"边 {points[u]['name']}–{points[v]['name']} "
                        f"实际长度平方 {d2} {side}（闭区间 "
                        f"[{e['minSq']}, {e['maxSq']}]）"),
                }
                break

    # 3) 非共端边相交/相切
    if violation is None:
        for pair_order, (ei, ej) in enumerate(edge_pairs):
            a = edges[ei]["endpoints"]
            b = edges[ej]["endpoints"]
            hit = _segment_hit(moved[a[0]], moved[a[1]],
                               moved[b[0]], moved[b[1]])
            if hit.intersect:
                kind = "相切（单点接触）" if hit.touching else "相交"
                violation = {
                    "kind": "crossing",
                    "order": (2, pair_order),
                    "edgeA": ei,
                    "edgeB": ej,
                    "endpointsA": a,
                    "endpointsB": b,
                    "touching": hit.touching,
                    "message": (
                        f"不共享端点的边 "
                        f"{points[a[0]]['name']}–{points[a[1]]['name']} 与 "
                        f"{points[b[0]]['name']}–{points[b[1]]['name']}{kind}，"
                        "缆线不得交叉或相切"),
                }
                break

    return violation, moved


def adjudicate(data: Any) -> dict[str, Any]:
    """执行联合裁决，返回可直接序列化给前端的证据报告。"""
    points, triangles, edges, candidates, staged = _validate(data)
    n = len(points)
    origin = [(p["x"], p["y"]) for p in points]

    base_area = [_signed_area2(origin, t) for t in triangles]
    for i, area in enumerate(base_area):
        if area == 0:
            raise AdjudicationError(
                f"原基线第 {i + 1} 个三角面退化（有向面积为 0），"
                "无法定义严格朝向")

    # 非共端边对，按边录入顺序确定证据次序
    edge_pairs: list[tuple[int, int]] = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            ei, ej = edges[i]["endpoints"], edges[j]["endpoints"]
            if not ({ei[0], ei[1]} & {ej[0], ej[1]}):
                edge_pairs.append((i, j))

    ranges = [range(len(g)) for g in candidates]
    total = math.prod((len(g) for g in candidates), start=1)

    ctx = {
        "points": points, "triangles": triangles, "edges": edges,
        "candidates": candidates, "origin": origin,
        "base_area": base_area, "edge_pairs": edge_pairs,
    }

    best: tuple[tuple[int, int, tuple[int, ...]], list[int]] | None = None
    first_violation: dict[str, Any] | None = None
    tried = 0

    for choice in itertools.product(*ranges):
        tried += 1
        violation, moved = _evaluate_choice(choice, **ctx)
        if violation is not None:
            if first_violation is None:
                first_violation = {
                    **violation,
                    "choice": list(choice),
                    "movedPoints": [[x, y] for x, y in moved],
                }
            continue

        # 可行组合：按三级目标择优
        disp_sq = [
            candidates[i][choice[i]][0] ** 2
            + candidates[i][choice[i]][1] ** 2
            for i in range(n)
        ]
        key = (max(disp_sq), sum(disp_sq), choice)
        if best is None or key < best[0]:
            best = (key, list(choice))

    report = _build_report(**ctx, best=best, first_violation=first_violation,
                           tried=tried, total=total)

    if staged:
        report = _attach_staged_review(report, **ctx,
                                       best=best, tried=tried, total=total,
                                       first_violation=first_violation)
    return report


def _orientation_message(ti: int, tri: list[int],
                         points: list[dict[str, Any]],
                         original: int, actual: int) -> str:
    names = "、".join(points[v]["name"] for v in tri)
    if actual == 0:
        state = "退化为共线（有向面积为 0）"
    elif actual * original < 0:
        state = "朝向翻转"
    else:
        state = "朝向未保持"
    return (f"三角面 {ti + 1}（{names}）位移后{state}：两倍有向面积 "
            f"{original} → {actual}，须与原基线严格同号")


# ---------------------------------------------------- 分段落位复核

def _affected_triangles(triangles: list[list[int]], p: int) -> list[int]:
    return [ti for ti, tri in enumerate(triangles) if p in tri]


def _affected_edges(edges: list[dict[str, Any]], p: int) -> list[int]:
    return [ei for ei, e in enumerate(edges) if p in e["endpoints"]]


def _affected_pairs(edges: list[dict[str, Any]],
                    edge_pairs: list[tuple[int, int]], p: int
                    ) -> list[tuple[int, int]]:
    """落位 p 时几何发生变化、需要复核的非共端边对（至少一条边以 p 为端点）。"""
    out = []
    for ei, ej in edge_pairs:
        if p in edges[ei]["endpoints"] or p in edges[ej]["endpoints"]:
            out.append((ei, ej))
    return out


def _check_placement(q: int, pos: list[tuple[int, int]], *,
                     points: list[dict[str, Any]],
                     triangles: list[list[int]],
                     edges: list[dict[str, Any]],
                     candidates: list[list[list[int]]],
                     base_area: list[int],
                     edge_pairs: list[tuple[int, int]],
                     target: list[tuple[int, int]],
                     tri_idx: list[int], edge_idx: list[int],
                     pair_idx: list[tuple[int, int]]
                     ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """尝试把 q 从当前混合网位置落位到 target[q]。

    返回 (首个违约或 None, 逐步证据片段)。只复核因 q 落位而受影响的
    三角面、登记边与非共端边对；判定次序仍为 朝向 → 边长 → 相交。
    """
    new_pos = pos.copy()
    new_pos[q] = target[q]

    tri_rows = []
    for ti in tri_idx:
        tri = triangles[ti]
        before = _signed_area2(pos, tri)
        after = _signed_area2(new_pos, tri)
        row = {
            "triangleIndex": ti,
            "vertices": tri,
            "originalSignedArea2": base_area[ti],
            "beforeSignedArea2": before,
            "afterSignedArea2": after,
            "preserved": after * base_area[ti] > 0,
        }
        tri_rows.append(row)
        if after * base_area[ti] <= 0:
            names = "、".join(points[v]["name"] for v in tri)
            if after == 0:
                state = "退化为共线（有向面积为 0）"
            elif after * base_area[ti] < 0:
                state = "朝向翻转"
            else:
                state = "朝向未保持"
            violation = {
                "kind": "orientation",
                "triangleIndex": ti,
                "vertices": tri,
                "originalSignedArea2": base_area[ti],
                "beforeSignedArea2": before,
                "actualSignedArea2": after,
                "message": (
                    f"落位 {points[q]['name']} 时三角面 {ti + 1}"
                    f"（{names}）在混合网中{state}：两倍有向面积 "
                    f"{base_area[ti]} → 步前 {before} → 步后 {after}，"
                    "须与原基线严格同号"),
            }
            return violation, {"triangles": tri_rows,
                               "edges": [], "crossChecks": []}

    edge_rows = []
    for ei in edge_idx:
        e = edges[ei]
        u, v = e["endpoints"]
        before = ((pos[u][0] - pos[v][0]) ** 2
                  + (pos[u][1] - pos[v][1]) ** 2)
        after = ((new_pos[u][0] - new_pos[v][0]) ** 2
                 + (new_pos[u][1] - new_pos[v][1]) ** 2)
        within = e["minSq"] <= after <= e["maxSq"]
        edge_rows.append({
            "edgeIndex": ei,
            "endpoints": [u, v],
            "minSq": e["minSq"],
            "maxSq": e["maxSq"],
            "beforeSq": before,
            "afterSq": after,
            "within": within,
        })
        if not within:
            side = "小于下限" if after < e["minSq"] else "大于上限"
            violation = {
                "kind": "length",
                "edgeIndex": ei,
                "endpoints": [u, v],
                "minSq": e["minSq"],
                "maxSq": e["maxSq"],
                "beforeSq": before,
                "actualSq": after,
                "side": side,
                "message": (
                    f"落位 {points[q]['name']} 时混合网中边 "
                    f"{points[u]['name']}–{points[v]['name']} "
                    f"长度平方 {before} → {after}，{side}（闭区间 "
                    f"[{e['minSq']}, {e['maxSq']}]）"),
            }
            return violation, {"triangles": tri_rows,
                               "edges": edge_rows, "crossChecks": []}

    pair_rows = []
    for ei, ej in pair_idx:
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        hit = _segment_hit(new_pos[a[0]], new_pos[a[1]],
                           new_pos[b[0]], new_pos[b[1]])
        pair_rows.append({
            "edgeA": ei,
            "edgeB": ej,
            "endpointsA": a,
            "endpointsB": b,
            "coordsA": [[new_pos[a[0]][0], new_pos[a[0]][1]],
                        [new_pos[a[1]][0], new_pos[a[1]][1]]],
            "coordsB": [[new_pos[b[0]][0], new_pos[b[0]][1]],
                        [new_pos[b[1]][0], new_pos[b[1]][1]]],
            "intersect": hit.intersect,
            "touching": hit.touching,
        })
        if hit.intersect:
            kind = "相切（单点接触）" if hit.touching else "相交"
            violation = {
                "kind": "crossing",
                "edgeA": ei,
                "edgeB": ej,
                "endpointsA": a,
                "endpointsB": b,
                "touching": hit.touching,
                "message": (
                    f"落位 {points[q]['name']} 时混合网中不共享端点的边 "
                    f"{points[a[0]]['name']}–{points[a[1]]['name']} 与 "
                    f"{points[b[0]]['name']}–{points[b[1]]['name']}{kind}，"
                    "缆线不得交叉或相切"),
            }
            return violation, {"triangles": tri_rows,
                               "edges": edge_rows,
                               "crossChecks": pair_rows}

    return None, {"triangles": tri_rows,
                  "edges": edge_rows, "crossChecks": pair_rows}


def _placement_ok(q: int, pos: list[tuple[int, int]], *,
                  triangles: list[list[int]],
                  edges: list[dict[str, Any]],
                  base_area: list[int],
                  target: list[tuple[int, int]],
                  tri_idx: list[int], edge_idx: list[int],
                  pair_idx: list[tuple[int, int]]) -> bool:
    """轻量判定：q 落位一步后混合网是否仍满足全部硬约束（不构造证据）。"""
    nq = target[q]
    # 1) 朝向
    for ti in tri_idx:
        a, b, c = triangles[ti]
        pa = nq if a == q else pos[a]
        pb = nq if b == q else pos[b]
        pc = nq if c == q else pos[c]
        area = _cross(pb[0] - pa[0], pb[1] - pa[1],
                      pc[0] - pa[0], pc[1] - pa[1])
        if area * base_area[ti] <= 0:
            return False
    # 2) 边长
    for ei in edge_idx:
        e = edges[ei]
        u, v = e["endpoints"]
        pu = nq if u == q else pos[u]
        pv = nq if v == q else pos[v]
        d2 = (pu[0] - pv[0]) ** 2 + (pu[1] - pv[1]) ** 2
        if d2 < e["minSq"] or d2 > e["maxSq"]:
            return False
    # 3) 非共端边相交/相切
    for ei, ej in pair_idx:
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        p0 = nq if a[0] == q else pos[a[0]]
        p1 = nq if a[1] == q else pos[a[1]]
        p2 = nq if b[0] == q else pos[b[0]]
        p3 = nq if b[1] == q else pos[b[1]]
        if _segment_hit(p0, p1, p2, p3).intersect:
            return False
    return True


def _search_safe_order(choice: list[int], *,
                       points: list[dict[str, Any]],
                       triangles: list[list[int]],
                       edges: list[dict[str, Any]],
                       candidates: list[list[list[int]]],
                       base_area: list[int],
                       edge_pairs: list[tuple[int, int]]
                       ) -> dict[str, Any]:
    """在固定最终方案下联合搜索安全落位次序。

    DFS 遍历排列，状态仅由“已落位点集合”决定（混合网坐标随之唯一），
    故以失败集合记忆化，至多访问 2^n 个状态；每步只做布尔几何判定，
    证据在行至叶子或确认受阻步骤时才生成。次序偏好以完整键
    （逐步峰值序列, 点索引排列）分支定界：子节点按
    (累计峰值, 点索引) 展开，并用“剩余点位移升序”构造峰值下界、
    “剩余点索引升序”构造排列下界，无法击败 incumbent 的整枝剪去。
    无安全次序时同时收集最早（最浅、前缀字典序最小）的受阻步骤证据。
    """
    n = len(points)
    origin = [(p["x"], p["y"]) for p in points]
    target = _target_positions(choice, origin, candidates)
    disp_sq = [
        candidates[i][choice[i]][0] ** 2 + candidates[i][choice[i]][1] ** 2
        for i in range(n)
    ]

    tri_aff = [_affected_triangles(triangles, p) for p in range(n)]
    edge_aff = [_affected_edges(edges, p) for p in range(n)]
    pair_aff = [_affected_pairs(edges, edge_pairs, p) for p in range(n)]

    check_kw = {
        "points": points, "triangles": triangles, "edges": edges,
        "candidates": candidates, "base_area": base_area,
        "edge_pairs": edge_pairs, "target": target,
    }
    fast_kw = {
        "triangles": triangles, "edges": edges, "base_area": base_area,
        "target": target,
    }

    visited = 0
    dead: set[frozenset[int]] = set()
    best: tuple[tuple[int, ...], tuple[int, ...]] | None = None
    # 最早受阻状态：（深度, 已落位前缀, {尝试点: (首个违约, 步内证据)}）
    blocker: tuple[int, tuple[int, ...],
                   dict[int, tuple[dict[str, Any],
                                   dict[str, Any]]]] | None = None

    def dfs(prefix: list[int], peaks: list[int],
            pos: list[tuple[int, int]]) -> str:
        """返回 "ok"（存在合法完成，可能已剪枝）或 "dead"（几何上无合法完成）。

        必须区分“完成存在但被最优下界剪枝”与“根本无合法完成”：dead
        记忆只记录后者，因为同一已落位集合可经不同前缀（不同峰值史）到达。
        """
        nonlocal visited, best, blocker
        placed = frozenset(prefix)
        if placed in dead:
            return "dead"
        visited += 1

        remaining = [q for q in range(n) if q not in placed]
        if not remaining:
            cand_key = (tuple(peaks), tuple(prefix))
            if best is None or cand_key < best:
                best = cand_key
            return "ok"

        # 完整键下界：峰值取剩余位移升序排布（逐分量最小），
        # 排列取剩余索引升序（字典序最小）。
        cur = peaks[-1] if peaks else -1
        suffix: list[int] = []
        for v in sorted(disp_sq[q] for q in remaining):
            cur = max(cur, v)
            suffix.append(cur)
        bound = (tuple(peaks + suffix),
                 tuple(prefix + sorted(remaining)))
        if best is not None and bound >= best:
            # 存在合法完成也不可能更优——但不能据此判该集合几何死亡
            return "ok"

        cur_peak = peaks[-1] if peaks else -1
        ordered = sorted(
            remaining,
            key=lambda q: (max(cur_peak, disp_sq[q]), q))

        found_ok = False
        failing: list[int] = []
        for q in ordered:
            ok = _placement_ok(
                q, pos, tri_idx=tri_aff[q], edge_idx=edge_aff[q],
                pair_idx=pair_aff[q], **fast_kw)
            if not ok:
                failing.append(q)
                continue
            new_pos = pos.copy()
            new_pos[q] = target[q]
            new_peak = max(cur_peak, disp_sq[q])
            child = dfs(prefix + [q], peaks + [new_peak], new_pos)
            if child == "ok":
                found_ok = True
                # 子节点按 (累计峰值, 索引) 升序展开，首个合法完成即偏好最优；
                # 其余兄弟必然被完整键下界剪去。
                break

        if found_ok:
            return "ok"

        # 所有子节点要么一步违约、要么落入几何死亡集合：该前缀确实无合法完成。
        # 仅当本层全部为“一步违约”时才是受阻步骤（否则受阻证据在更深层）。
        if len(failing) == len(remaining):
            depth = len(prefix)
            tag = (depth, tuple(prefix))
            if blocker is None or tag < (blocker[0], blocker[1]):
                failures: dict[int, tuple[dict[str, Any],
                                          dict[str, Any]]] = {}
                for q in failing:
                    failures[q] = _check_placement(
                        q, pos, tri_idx=tri_aff[q],
                        edge_idx=edge_aff[q], pair_idx=pair_aff[q],
                        **check_kw)
                blocker = (depth, tuple(prefix), failures)
        dead.add(placed)
        return "dead"

    dfs([], [], list(origin))

    return {
        "feasible": best is not None,
        "order": list(best[1]) if best is not None else None,
        "peaks": list(best[0]) if best is not None else None,
        "visitedStates": visited,
        "blocker": blocker,
        "dispSq": disp_sq,
        "target": target,
        "checkKw": check_kw,
        "triAff": tri_aff,
        "edgeAff": edge_aff,
        "pairAff": pair_aff,
    }


def _build_steps(choice: list[int], order: list[int], peaks: list[int],
                 search: dict[str, Any], *,
                 points: list[dict[str, Any]],
                 candidates: list[list[list[int]]]) -> list[dict[str, Any]]:
    """按既定安全次序重放，生成逐步证据（含受影响面/边/边对与累计状态）。"""
    n = len(points)
    origin = [(p["x"], p["y"]) for p in points]
    target = search["target"]
    disp_sq = search["dispSq"]
    pos = list(origin)
    steps = []
    for k, q in enumerate(order):
        violation, frag = _check_placement(
            q, pos, tri_idx=search["triAff"][q],
            edge_idx=search["edgeAff"][q],
            pair_idx=search["pairAff"][q], **search["checkKw"])
        assert violation is None, "已裁决安全次序在重放时违约"
        pos[q] = target[q]
        ci = choice[q]
        dx, dy = candidates[q][ci]
        steps.append({
            "step": k + 1,
            "pointIndex": q,
            "pointName": points[q]["name"],
            "candidateIndex": ci,
            "displacement": [dx, dy],
            "displacementSq": disp_sq[q],
            "landed": [target[q][0], target[q][1]],
            "peakSq": peaks[k],
            "affectedTriangles": frag["triangles"],
            "affectedEdges": frag["edges"],
            "crossChecks": frag["crossChecks"],
            "checks": {
                "trianglesChecked": len(frag["triangles"]),
                "edgesChecked": len(frag["edges"]),
                "pairsChecked": len(frag["crossChecks"]),
            },
            "cumulative": {
                "placed": list(order[:k + 1]),
                "placedNames": [points[i]["name"] for i in order[:k + 1]],
                "movedPoints": [[x, y] for x, y in pos],
                "peakSq": peaks[k],
            },
        })
    return steps


def _attach_staged_review(report: dict[str, Any], *,
                          points: list[dict[str, Any]],
                          triangles: list[list[int]],
                          edges: list[dict[str, Any]],
                          candidates: list[list[list[int]]],
                          origin: list[tuple[int, int]],
                          base_area: list[int],
                          edge_pairs: list[tuple[int, int]],
                          best: tuple[tuple[int, int, tuple[int, ...]],
                                      list[int]] | None,
                          first_violation: dict[str, Any] | None,
                          tried: int, total: int) -> dict[str, Any]:
    n = len(points)

    # 情形一：连静态最终方案都不存在——沿用确定性首个违约证据，
    # 落位排序无需也无法进行。
    if best is None:
        report["stagedReview"] = {
            "enabled": True,
            "feasible": False,
            "reason": "static-infeasible",
            "message": "不存在满足全部硬约束的最终方案，未进入分段落位排序。",
            "order": None,
            "steps": [],
            "blocking": None,
            "search": {
                "combinations": total,
                "finalFeasibleCombinations": 0,
                "permutationsPerCombination": math.factorial(n),
                "orderSearchVisitedStates": 0,
            },
        }
        return report

    # 收集全部静态可行组合，按既有三级静态目标升序——
    # 第一个“同时存在安全落位次序”的组合即联合裁决的全局最优。
    ranges = [range(len(g)) for g in candidates]
    feasible_choices: list[tuple[
        tuple[int, int, tuple[int, ...]], list[int]]] = []
    for combo in itertools.product(*ranges):
        violation, _moved = _evaluate_choice(
            combo, points=points, triangles=triangles, edges=edges,
            candidates=candidates, origin=origin, base_area=base_area,
            edge_pairs=edge_pairs)
        if violation is None:
            disp_sq = [
                candidates[i][combo[i]][0] ** 2
                + candidates[i][combo[i]][1] ** 2
                for i in range(n)
            ]
            feasible_choices.append(
                ((max(disp_sq), sum(disp_sq), combo), list(combo)))
    feasible_choices.sort(key=lambda t: t[0])

    visited_total = 0
    winning_search: dict[str, Any] | None = None
    winning_choice: list[int] | None = None
    witness_search: dict[str, Any] | None = None
    witness_choice = feasible_choices[0][1]
    combos_order_searched = 0
    for _key, combo in feasible_choices:
        combos_order_searched += 1
        res = _search_safe_order(
            combo, points=points, triangles=triangles, edges=edges,
            candidates=candidates, base_area=base_area,
            edge_pairs=edge_pairs)
        visited_total += res["visitedStates"]
        if witness_search is None:
            # feasible_choices 已按静态键升序，首个即静态最优（阻塞证据锚点）
            witness_search = res
        if res["feasible"]:
            # 第一个（静态最优的）可安全落位组合即联合裁决全局最优
            winning_search = res
            winning_choice = combo
            break
    assert witness_search is not None

    search_info = {
        "combinations": total,
        "combinationsTried": tried,
        "finalFeasibleCombinations": len(feasible_choices),
        "orderSearchedCombinations": combos_order_searched,
        "permutationsPerCombination": math.factorial(n),
        "orderSearchVisitedStates": visited_total,
    }

    if winning_search is not None and winning_choice is not None:
        order = winning_search["order"]
        peaks = winning_search["peaks"]
        steps = _build_steps(winning_choice, order, peaks,
                             winning_search, points=points,
                             candidates=candidates)
        # 联合最优方案若不同于纯静态最优，需要以它重写终态证据字段。
        if list(winning_choice) != report["choice"]:
            joint_report = _build_report(
                points=points, triangles=triangles, edges=edges,
                candidates=candidates, origin=origin, base_area=base_area,
                edge_pairs=edge_pairs,
                best=(_static_key(winning_choice, candidates),
                      list(winning_choice)),
                first_violation=None, tried=tried, total=total)
            report = joint_report
        report["stagedReview"] = {
            "enabled": True,
            "feasible": True,
            "reason": "ok",
            "message": "已在候选位移与落位顺序的完整组合中联合裁决。",
            "order": order,
            "orderNames": [points[i]["name"] for i in order],
            "stepDisplacementSq": [steps[k]["displacementSq"]
                                   for k in range(n)],
            "stepPeakSq": peaks,
            "steps": steps,
            "blocking": None,
            "search": search_info,
        }
        return report

    # 情形二：存在静态可行的最终方案，但没有任何方案能排出安全次序。
    # 证据锚定：静态最优（三级目标第一）的最终方案；在其次序搜索中
    # 稳定取最早受阻步骤。
    witness = witness_search
    assert witness["blocker"] is not None
    depth, prefix, failures = witness["blocker"]

    # 以静态最优但无法落位的方案重写终态字段（终态本身几何合格）。
    report = _build_report(
        points=points, triangles=triangles, edges=edges,
        candidates=candidates, origin=origin, base_area=base_area,
        edge_pairs=edge_pairs,
        best=(_static_key(witness_choice, candidates),
              list(witness_choice)),
        first_violation=None, tried=tried, total=total)
    report["feasible"] = False

    tried_rows = []
    first_viol = None
    target = witness["target"]
    ordered_remaining = sorted(failures.keys(), key=lambda q: (
        max(max(witness["dispSq"][i] for i in prefix),
            witness["dispSq"][q]) if prefix else witness["dispSq"][q], q))
    for q in ordered_remaining:
        v, frag = failures[q]
        tried_rows.append({
            "pointIndex": q,
            "pointName": points[q]["name"],
            "candidateIndex": witness_choice[q],
            "displacement": candidates[q][witness_choice[q]],
            "displacementSq": witness["dispSq"][q],
            "wouldLand": [target[q][0], target[q][1]],
            "affectedTriangles": frag["triangles"],
            "affectedEdges": frag["edges"],
            "crossChecks": frag["crossChecks"],
            "violation": v,
        })
        if first_viol is None:
            first_viol = v

    placed_names = [points[i]["name"] for i in prefix]
    blocking = {
        "kind": "staging-blocked",
        "step": depth + 1,
        "placedCount": depth,
        "placed": list(prefix),
        "placedNames": placed_names,
        "remainingPointCount": n - depth,
        "triedCandidates": tried_rows,
        "firstViolation": first_viol,
        "message": (
            f"第 {depth + 1} 步落位受阻：已按前缀 ["
            + "、".join(placed_names) + "] 落位后，剩余 "
            f"{n - depth} 个点逐一尝试均破坏硬约束，不存在安全次序；"
            f"首条被破坏约束：{first_viol['message']}"
            if first_viol is not None else
            f"第 {depth + 1} 步落位受阻，不存在安全次序。"),
    }
    report["stagedReview"] = {
        "enabled": True,
        "feasible": False,
        "reason": "no-safe-order",
        "message": (
            "全部静态可行组合均不存在满足每步硬约束的落位次序"
            "（候选位移与落位顺序已联合穷举裁决，非事后试排）。"),
        "order": None,
        "steps": [],
        "blocking": blocking,
        "witnessChoice": witness_choice,
        "search": search_info,
    }
    return report


def _static_key(choice: list[int] | tuple[int, ...],
                candidates: list[list[list[int]]]
                ) -> tuple[int, int, tuple[int, ...]]:
    disp_sq = [
        candidates[i][choice[i]][0] ** 2 + candidates[i][choice[i]][1] ** 2
        for i in range(len(choice))
    ]
    return max(disp_sq), sum(disp_sq), tuple(choice)


def _build_report(points, triangles, edges, candidates, origin,
                  base_area, edge_pairs, best, first_violation, tried, total,
                  ) -> dict[str, Any]:
    def assignment_entries(choice: list[int]) -> list[dict[str, Any]]:
        out = []
        for i, ci in enumerate(choice):
            dx, dy = candidates[i][ci]
            out.append({
                "pointIndex": i,
                "pointName": points[i]["name"],
                "candidateIndex": ci,
                "displacement": [dx, dy],
                "displacementSq": dx * dx + dy * dy,
                "displaced": [origin[i][0] + dx, origin[i][1] + dy],
                "rejected": [[dx2, dy2]
                             for j, (dx2, dy2) in enumerate(candidates[i])
                             if j != ci],
            })
        return out

    def geometry_report(choice: list[int]) -> dict[str, Any]:
        moved = _target_positions(choice, origin, candidates)
        edge_rows = []
        for ei, e in enumerate(edges):
            u, v = e["endpoints"]
            d2 = ((moved[u][0] - moved[v][0]) ** 2
                  + (moved[u][1] - moved[v][1]) ** 2)
            edge_rows.append({
                "edgeIndex": ei,
                "endpoints": [u, v],
                "minSq": e["minSq"],
                "maxSq": e["maxSq"],
                "actualSq": d2,
                "within": e["minSq"] <= d2 <= e["maxSq"],
            })
        tri_rows = []
        for ti, tri in enumerate(triangles):
            area = _signed_area2(moved, tri)
            tri_rows.append({
                "triangleIndex": ti,
                "vertices": tri,
                "originalSignedArea2": base_area[ti],
                "actualSignedArea2": area,
                "preserved": area * base_area[ti] > 0,
            })
        conflicts = []
        for ei, ej in edge_pairs:
            a = edges[ei]["endpoints"]
            b = edges[ej]["endpoints"]
            hit = _segment_hit(moved[a[0]], moved[a[1]],
                               moved[b[0]], moved[b[1]])
            if hit.intersect:
                conflicts.append({
                    "edgeA": ei, "edgeB": ej,
                    "endpointsA": a, "endpointsB": b,
                    "touching": hit.touching,
                })
        return {
            "movedPoints": [[x, y] for x, y in moved],
            "edges": edge_rows,
            "triangles": tri_rows,
            "conflicts": conflicts,
        }

    report: dict[str, Any] = {
        "feasible": best is not None,
        "triedCombinations": tried,
        "totalCombinations": total,
        "points": [{"name": p["name"], "x": p["x"], "y": p["y"]}
                   for p in points],
        "candidateCount": [len(g) for g in candidates],
    }

    if best is not None:
        (max_sq, sum_sq, _choice_tuple), choice = best
        report["objective"] = {"maxSq": max_sq, "sumSq": sum_sq}
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        report.update(geometry_report(choice))
        report["firstViolation"] = None
    else:
        assert first_violation is not None
        choice = first_violation["choice"]
        report["objective"] = None
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        geo = geometry_report(choice)
        report.update(geo)
        report["firstViolation"] = first_violation
    return report

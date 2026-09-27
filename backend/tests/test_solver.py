"""裁决核心单元测试：纯几何谓词、三级优化目标与三类违约证据。"""

from __future__ import annotations

import pytest

from app.solver import AdjudicationError, _segment_hit, adjudicate


# --------------------------------------------------------------- 几何谓词

def test_segment_hit_basic_cases():
    # 普通交叉
    assert _segment_hit((0, 0), (10, 10), (0, 10), (10, 0)).intersect
    # 不相交
    assert not _segment_hit((0, 0), (1, 1), (5, 5), (6, 6)).intersect
    # T 字相接（端点落在对方内部）= 相切
    hit = _segment_hit((0, 0), (10, 0), (5, 5), (5, 0))
    assert hit.intersect and hit.touching
    # 端点互相接触 = 相切
    hit = _segment_hit((0, 0), (10, 0), (10, 0), (12, 4))
    assert hit.intersect and hit.touching
    # 共线重叠（多个交点，非相切）
    hit = _segment_hit((0, 0), (10, 0), (2, 0), (8, 0))
    assert hit.intersect and not hit.touching
    # 共线但仅单点接触 = 相切
    hit = _segment_hit((0, 0), (10, 0), (10, 0), (12, 0))
    assert hit.intersect and hit.touching
    # 共线分离
    assert not _segment_hit((0, 0), (10, 0), (11, 0), (12, 0)).intersect
    # 平行不共线
    assert not _segment_hit((0, 0), (10, 0), (0, 1), (10, 1)).intersect
    # 退化点段落在对方内部
    hit = _segment_hit((3, 0), (3, 0), (0, 0), (10, 0))
    assert hit.intersect and hit.touching
    # 退化点段落在对方之外
    assert not _segment_hit((20, 0), (20, 0), (0, 0), (10, 0)).intersect


# --------------------------------------------------------------- 夹具

def _five_points():
    # 凸五边形，三角面按逆时针扇形登记
    return [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 10, "y": 0},
        {"name": "C", "x": 14, "y": 6},
        {"name": "D", "x": 6, "y": 12},
        {"name": "E", "x": -4, "y": 6},
    ]


def _fan_triangles():
    return [[0, 1, 2], [0, 2, 3], [0, 3, 4]]


_PAIRS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0), (0, 2), (0, 3)]


def _wide_edges():
    # 足够宽的长度平方区间，任何小幅位移都通过
    return [{"endpoints": [u, v], "minSq": 0, "maxSq": 100_000}
            for u, v in _PAIRS]


def _payload(points=None, triangles=None, edges=None, candidates=None):
    return {
        "points": points or _five_points(),
        "triangles": triangles or _fan_triangles(),
        "edges": edges or _wide_edges(),
        "candidates": candidates
        or [[[0, 0], [1, 0]] for _ in range(5)],
    }


# --------------------------------------------------------------- 可行裁决

def test_zero_displacement_feasible_and_minimal():
    report = adjudicate(_payload())
    assert report["feasible"] is True
    assert report["choice"] == [0, 0, 0, 0, 0]
    assert report["objective"] == {"maxSq": 0, "sumSq": 0}
    assert all(row["preserved"] for row in report["triangles"])
    assert all(row["within"] for row in report["edges"])
    assert report["conflicts"] == []
    assert report["firstViolation"] is None


def test_assignment_lists_used_and_rejected_candidates():
    payload = _payload(candidates=[
        [[0, 0], [2, 2]],
        [[1, 1], [2, 2]],   # B 无零位移候选，最小平方候选 [1,1]
        [[0, 0], [-1, 3]],
        [[0, 0], [0, 2]],
        [[0, 0], [-2, -2]],
    ])
    report = adjudicate(payload)
    assert report["feasible"] is True
    assert report["choice"][1] == 0
    assert report["assignment"][1]["displacement"] == [1, 1]
    assert report["assignment"][1]["rejected"] == [[2, 2]]
    assert report["assignment"][1]["displaced"] == [11, 1]
    assert report["assignment"][0]["displaced"] == [0, 0]
    assert report["objective"]["maxSq"] == 2


def test_objective_minimax_beats_sum():
    # A-B 原长平方 100，区间锁定 [85,100]：
    #   组合 X：A 取 [2,0](平方4)、B 取 [2,0](平方4) => AB=100，最大4、平方和8
    #   组合 Y：A 取 [1,2](平方5)、B 取 [0,0]        => AB=85， 最大5、平方和5
    # 第一目标最大位移平方最小，故 X 胜——尽管 Y 的平方和更小。
    edges = _wide_edges()
    edges[0] = {"endpoints": [0, 1], "minSq": 85, "maxSq": 100}
    payload = _payload(
        edges=edges,
        candidates=[
            [[2, 0], [1, 2]],
            [[2, 0], [0, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
        ],
    )
    report = adjudicate(payload)
    assert report["feasible"] is True
    assert report["choice"] == [0, 0, 0, 0, 0]
    assert report["objective"] == {"maxSq": 4, "sumSq": 8}


def test_objective_sum_then_lexicographic():
    # A 必动（两个候选均为平方 4）；其余点可取零位移。
    # 平方和同为 4 时，A 的两个候选平方相同，应取候选序号字典序更小的 [2,0]。
    payload = _payload(candidates=[
        [[2, 0], [0, 2]],
        [[2, 0], [0, 0]],
        [[2, 0], [0, 0]],
        [[2, 0], [0, 0]],
        [[2, 0], [0, 0]],
    ])
    report = adjudicate(payload)
    assert report["feasible"] is True
    assert report["objective"] == {"maxSq": 4, "sumSq": 4}
    assert report["choice"] == [0, 1, 1, 1, 1]
    assert report["assignment"][0]["displacement"] == [2, 0]


def test_enumeration_complete():
    payload = _payload(candidates=[
        [[0, 0], [1, 0], [0, 1]],
        [[0, 0], [1, 1]],
        [[0, 0], [1, 0]],
        [[0, 0], [0, 1]],
        [[0, 0], [-1, 0], [0, -1]],
    ])
    report = adjudicate(payload)
    assert report["totalCombinations"] == 3 * 2 * 2 * 2 * 3
    assert report["triedCombinations"] == report["totalCombinations"]


# --------------------------------------------------------------- 违约证据

def test_first_violation_orientation_flip():
    # A 候选 0 大幅移动使三角面 [A,B,C] 翻转；同时把 A-B 锁死为 2000，
    # 任何候选组合都不可行。字典序第一种组合（A 取候选 0）首先报朝向。
    edges = _wide_edges()
    edges[0] = {"endpoints": [0, 1], "minSq": 2000, "maxSq": 2000}
    payload = _payload(
        edges=edges,
        candidates=[
            [[12, 2], [0, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
        ],
    )
    report = adjudicate(payload)
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "orientation"
    assert v["triangleIndex"] == 0
    assert v["originalSignedArea2"] == 60
    assert v["actualSignedArea2"] == -4
    assert v["choice"] == [0, 0, 0, 0, 0]
    assert "翻转" in v["message"]


def test_orientation_degenerate_collinear_is_violation():
    # 移动后三点共线，面积为 0，同样不能保持严格朝向
    points = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 10, "y": 0},
        {"name": "C", "x": 20, "y": 0},
        {"name": "D", "x": 6, "y": 12},
        {"name": "E", "x": -4, "y": 6},
    ]
    # 三角面 [B,C,D]：cross(C-B=(10,0), D-B=(-4,12))=120 > 0
    triangles = [[1, 2, 3], [0, 1, 4]]
    # 另把 B-D 锁死为 500：D 不动时 d2=160、下移时 d2=16，均不可行，
    # 从而保证任何组合都无解。
    edges = _wide_edges()
    edges.append({"endpoints": [1, 3], "minSq": 500, "maxSq": 500})
    # D 候选 0 移到 y=0 与 B、C 共线；候选 1 不动
    candidates = [
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        [[0, -12], [0, 0]],
        [[0, 0], [1, 0]],
    ]
    report = adjudicate(_payload(points=points, triangles=triangles,
                                 edges=edges, candidates=candidates))
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "orientation"
    assert v["triangleIndex"] == 0
    assert v["actualSignedArea2"] == 0
    assert "共线" in v["message"]


def test_first_violation_length_upper_bound():
    # A-B 锁死为长度平方 90：字典序首组合 B 上移 1 => 101，超上限；
    # 其余组合给出 100/82/81，均不等于 90，故整体不可行。
    edges = _wide_edges()
    edges[0] = {"endpoints": [0, 1], "minSq": 90, "maxSq": 90}
    payload = _payload(
        edges=edges,
        candidates=[
            [[0, 0], [1, 0]],
            [[0, 1], [0, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
        ],
    )
    report = adjudicate(payload)
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "length"
    assert v["edgeIndex"] == 0
    assert (v["minSq"], v["maxSq"], v["actualSq"]) == (90, 90, 101)
    assert "大于上限" in v["side"]
    row = report["edges"][0]
    assert row["actualSq"] == 101 and row["within"] is False


def test_first_violation_length_lower_bound():
    # 字典序首组合 A=0、B 取 [-9,0] 使 AB=1，低于下限 100；
    # 四种 A/B 组合给出 AB ∈ {1, 121, 81}，无一等于 100，故整体不可行。
    edges = _wide_edges()
    edges[0] = {"endpoints": [0, 1], "minSq": 100, "maxSq": 100}
    payload = _payload(
        edges=edges,
        candidates=[
            [[0, 0], [2, 0]],
            [[-9, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
        ],
    )
    report = adjudicate(payload)
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "length"
    assert v["actualSq"] == 1
    assert "小于下限" in v["side"]


def _crossing_fixture(d_candidates):
    # A-B 是横贯底部的边（y=0），C-D 在上方；E 只服务于三角面。
    points = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 10, "y": 0},
        {"name": "C", "x": 2, "y": 3},
        {"name": "D", "x": 8, "y": 3},
        {"name": "E", "x": 5, "y": 9},
    ]
    edges = [
        {"endpoints": [0, 1], "minSq": 0, "maxSq": 100_000},
        {"endpoints": [2, 3], "minSq": 0, "maxSq": 100_000},
    ]
    triangles = [[0, 1, 4], [0, 4, 2]]
    candidates = [
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        d_candidates,
        [[0, 0], [1, 0]],
    ]
    return _payload(points=points, triangles=triangles,
                    edges=edges, candidates=candidates)


def test_first_violation_crossing():
    # D 的两个候选都把 C-D 拉过 A-B（交点在两线段内部），任何组合均不可行；
    # 其余点 ±1 的平移不会把交点移出任何线段。
    report = adjudicate(_crossing_fixture([[0, -6], [0, -10]]))
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "crossing"
    assert {v["edgeA"], v["edgeB"]} == {0, 1}
    assert v["touching"] is False
    assert "相交" in v["message"]
    assert report["conflicts"][0]["touching"] is False
    assert v["choice"] == [0, 0, 0, 0, 0]


def test_first_violation_touching_counts_as_crossing():
    # D 候选 0 使其端点恰好落在 A-B 内部（单点相切，禁止）；
    # 候选 1 则整段穿越 A-B，保证没有可行组合。
    points = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 10, "y": 0},
        {"name": "C", "x": 2, "y": 4},
        {"name": "D", "x": 8, "y": 3},
        {"name": "E", "x": 5, "y": 9},
    ]
    edges = [
        {"endpoints": [0, 1], "minSq": 0, "maxSq": 100_000},
        {"endpoints": [2, 3], "minSq": 0, "maxSq": 100_000},
    ]
    triangles = [[0, 1, 4]]
    candidates = [
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        [[0, 0], [1, 0]],
        [[0, -3], [0, -6]],
        [[0, 0], [1, 0]],
    ]
    report = adjudicate(_payload(points=points, triangles=triangles,
                                 edges=edges, candidates=candidates))
    assert report["feasible"] is False
    v = report["firstViolation"]
    assert v["kind"] == "crossing"
    assert v["touching"] is True
    assert "相切" in v["message"]


def test_shared_endpoint_edges_are_allowed_to_meet():
    # 共享端点的边相交于公共端点是正常的，不得误报
    report = adjudicate(_payload())
    assert report["conflicts"] == []


# --------------------------------------------------------------- 录入校验

def test_rejects_wrong_point_count():
    payload = _payload()
    payload["points"] = payload["points"][:4]
    payload["candidates"] = payload["candidates"][:4]
    with pytest.raises(AdjudicationError, match="5 至 8"):
        adjudicate(payload)


def test_rejects_non_integer_coordinate():
    payload = _payload()
    payload["points"][0]["x"] = 1.5
    with pytest.raises(AdjudicationError, match="整数"):
        adjudicate(payload)


def test_rejects_candidate_count_out_of_range():
    payload = _payload()
    payload["candidates"][0] = [[0, 0]]
    with pytest.raises(AdjudicationError, match="2 至 3"):
        adjudicate(payload)


def test_rejects_duplicate_candidate_vector():
    payload = _payload()
    payload["candidates"][0] = [[1, 0], [1, 0]]
    with pytest.raises(AdjudicationError, match="重复"):
        adjudicate(payload)


def test_rejects_bad_interval():
    payload = _payload()
    payload["edges"][0]["minSq"] = 999_999
    with pytest.raises(AdjudicationError, match="minSq"):
        adjudicate(payload)


def test_rejects_degenerate_baseline_triangle():
    payload = _payload()
    payload["points"] = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "B", "x": 10, "y": 0},
        {"name": "C", "x": 20, "y": 0},
        {"name": "D", "x": 6, "y": 12},
        {"name": "E", "x": -4, "y": 6},
    ]
    payload["triangles"] = [[0, 1, 2]]
    with pytest.raises(AdjudicationError, match="退化"):
        adjudicate(payload)


def test_rejects_index_out_of_range_and_duplicate_edge():
    payload = _payload()
    payload["triangles"][0] = [0, 1, 9]
    with pytest.raises(AdjudicationError, match="越界"):
        adjudicate(payload)
    payload = _payload()
    payload["edges"].append({"endpoints": [0, 1], "minSq": 0, "maxSq": 10})
    with pytest.raises(AdjudicationError, match="重复"):
        adjudicate(payload)

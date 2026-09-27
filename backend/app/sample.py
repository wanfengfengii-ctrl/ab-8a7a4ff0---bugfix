"""内置示例：六基准点的玻璃穹顶基线网。

坐标单位为毫米，全部为整数；边长平方闭区间按原长度留有余量，
零位移方案可行，另含若干更激进的候选位移用于展示裁决取舍。
"""

from __future__ import annotations


def example_payload() -> dict:
    points = [
        {"name": "P1", "x": 0, "y": 0},
        {"name": "P2", "x": 10, "y": 0},
        {"name": "P3", "x": 15, "y": 8},
        {"name": "P4", "x": 8, "y": 14},
        {"name": "P5", "x": -2, "y": 12},
        {"name": "P6", "x": -6, "y": 4},
    ]

    # 三角面按逆时针登记，原两倍有向面积均为正
    triangles = [
        [0, 1, 2],
        [0, 2, 3],
        [0, 3, 4],
        [0, 4, 5],
    ]

    def sq(ax: int, ay: int, bx: int, by: int) -> int:
        return (ax - bx) ** 2 + (ay - by) ** 2

    raw_edges = [
        (0, 1), (0, 2), (0, 3), (0, 4), (0, 5),
        (1, 2), (2, 3), (3, 4), (4, 5),
    ]
    margin = 30
    edges = []
    for u, v in raw_edges:
        d2 = sq(points[u]["x"], points[u]["y"], points[v]["x"], points[v]["y"])
        edges.append({
            "endpoints": [u, v],
            "minSq": max(0, d2 - margin),
            "maxSq": d2 + margin,
        })

    candidates = [
        [[0, 0], [1, -1], [3, -2]],
        [[0, 0], [1, 1], [2, 2]],
        [[0, 0], [2, 0], [4, -3]],
        [[0, 0], [0, 2], [-1, 3]],
        [[0, 0], [-2, 1], [-3, 2]],
        [[0, 0], [-1, -1], [-2, -2]],
    ]

    return {
        "points": points,
        "triangles": triangles,
        "edges": edges,
        "candidates": candidates,
    }

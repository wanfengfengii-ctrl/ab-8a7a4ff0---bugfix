"""历史建筑玻璃穹顶基线网位移裁决。

全部几何判定只使用整数（叉积、距离平方），不引入浮点，保证
三角面朝向、边长平方闭区间、非共端边相交/相切三类证据均可复核。
"""

from .solver import AdjudicationError, adjudicate  # noqa: F401

__all__ = ["AdjudicationError", "adjudicate"]

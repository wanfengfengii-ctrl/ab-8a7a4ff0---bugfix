#!/usr/bin/env bash
# Compose 一次性 verify 服务入口：
#   代码测试（pytest） → 前端构建（vite build） → 裁决 API 冒烟
# 仅在 web 服务通过健康检查后才会启动（见 docker-compose.yml 的 depends_on）。
set -euo pipefail

BASE_URL="${BASE_URL:-http://web:8080}"
# Node 基础镜像内解释器为 python3；本地若有 python 亦可
PY="$(command -v python || command -v python3)"

echo "==> [1/3] 安装 Python 测试依赖并运行代码测试"
cd /app/backend
"$PY" -m pip install --no-cache-dir --disable-pip-version-check -q -r requirements-dev.txt
"$PY" -m pytest -q

echo "==> [2/3] 构建前端"
cd /app/frontend
npm install --no-audit --no-fund --silent
npm run build

echo "==> [3/3] 裁决 API 冒烟（目标 ${BASE_URL}）"
cd /app
"$PY" scripts/smoke.py --base-url "${BASE_URL}"

echo "==> verify 全部通过"

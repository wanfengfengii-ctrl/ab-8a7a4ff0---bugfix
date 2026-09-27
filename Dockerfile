# syntax=docker/dockerfile:1

# ---- 阶段 1：构建前端静态产物 -----------------------------------------
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 阶段 2：精简运行时（仅 Python 标准库 + 静态产物） ----------------
FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    STATIC_DIR=/app/frontend
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend-build /app/frontend/dist ./frontend/
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=12 \
  CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3); \
sys.exit(0 if r.status==200 else 1)"
CMD ["python", "backend/server.py"]

# ---- 阶段 3：一次性 verify（需要 Node 构建前端 + Python 跑测试） ------
FROM node:22-bookworm-slim AS verify
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*
# 仓库全量源码（测试、构建、冒烟均在此容器内完成）
COPY . .
ENV BASE_URL=http://web:8080 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    npm_config_cache=/tmp/npm-cache
CMD ["bash", "/app/scripts/verify.sh"]

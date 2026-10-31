# 申论复盘 Web 版镜像
# 运行时需要 Python 3.10+（server.py）和 Node.js >=18（claude-agent-sdk 底层 CLI）。
# 选 python:3.13-slim 基础镜像 + Debian 自带的 nodejs（bookworm 为 18.x，满足要求）。
FROM python:3.13-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* || true
# 上面 apt 失败时（国内网络）换清华 Debian 镜像源重试
RUN if ! node --version 2>/dev/null; then \
        sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
        && apt-get update && apt-get install -y --no-install-recommends nodejs \
        && rm -rf /var/lib/apt/lists/*; \
    fi
RUN node --version && python --version

# 国内服务器构建加速：默认走清华 PyPI 镜像，可用 --build-arg PIP_INDEX_URL=... 覆盖
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖再拷代码，依赖层可缓存，代码更新时重建很快
COPY webapp/requirements-webapp.txt /tmp/requirements-webapp.txt
RUN pip install --no-cache-dir -r /tmp/requirements-webapp.txt

# 拷贝仓库（.dockerignore 已排除 .env / .git / .venv 等敏感与无关内容）
COPY . /app

# 非 root 运行；.env 不在镜像内，由 docker compose 挂载
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8787

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=5)"

CMD ["python", "webapp/server.py", "--port", "8787"]

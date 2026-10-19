# syntax=docker/dockerfile:1.7
# Multi-stage build: compilers and pip caches stay in the builder; the runtime image
# holds only a virtualenv and runs as an unprivileged user.
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps . && python -m compileall -q /opt/venv

FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.title="bidalert" \
      org.opencontainers.image.description="Tender screening pipeline, API and worker" \
      org.opencontainers.image.source="https://github.com/Worldisbinary/bid-alert-tender-pipeline" \
      org.opencontainers.image.licenses="MIT"
# Pick up Debian security fixes released after the base image was built.
RUN apt-get update \
 && apt-get -y upgrade \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 bidalert \
 && useradd --system --uid 10001 --gid bidalert --home-dir /var/lib/bidalert --shell /usr/sbin/nologin bidalert \
 && mkdir -p /var/lib/bidalert /srv/site \
 && chown bidalert:bidalert /var/lib/bidalert /srv/site
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIDALERT_DATABASE_URL=sqlite:////var/lib/bidalert/bidalert.db \
    BIDALERT_OUTPUT_DIR=/var/lib/bidalert/output \
    BIDALERT_SITE_DIR=/var/lib/bidalert/site \
    BIDALERT_HTTP_CACHE_DIR=/var/lib/bidalert/cache
USER 10001:10001
WORKDIR /var/lib/bidalert
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["bidalert", "serve", "--host", "0.0.0.0", "--port", "8000"]

# syntax=docker/dockerfile:1.7
#
# Two stages on purpose: the builder resolves and installs wheels into a virtual
# environment, then only that environment and the runtime libraries are copied
# forward. The build toolchain never reaches the deployed image.

ARG PYTHON_VERSION=3.13

# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

RUN python -m venv "$VIRTUAL_ENV"

# Dependency metadata first: this layer is cached and only rebuilt when the
# declared dependencies change, not on every source edit.
COPY pyproject.toml README.md ./
COPY bot ./bot

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install .

# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DOWNLOAD_DIR=/data/downloads \
    DATABASE_URL=sqlite+aiosqlite:////data/bot.db

# ffmpeg is what merges and muxes formats; ca-certificates is what makes TLS work.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        ffmpeg \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Unprivileged, matching neither the host user nor a common image user.
RUN groupadd --gid "$APP_GID" bot \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-log-init --create-home bot \
    && mkdir -p /data/downloads \
    && chown -R bot:bot /data

WORKDIR /app
COPY --chown=bot:bot bot ./bot

USER bot

EXPOSE 9090

# tini reaps the yt-dlp subprocesses the bot spawns, so a cancelled download does
# not leave a zombie behind.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bot"]

# paia as a Home Assistant add-on.
#
# Base is node:22-bookworm-slim rather than a Home Assistant base image: the
# add-on is distributed pre-built (`image:` in addon/config.yaml), so the
# supervisor never builds it and the s6/bashio layer buys nothing, while Node
# is a hard requirement -- the Claude Agent SDK drives the `claude` CLI.
#
# Debian and not Alpine on purpose: ctranslate2 and onnxruntime, which
# faster-whisper pulls in, publish manylinux aarch64 wheels and no musl ones.
# On Alpine both would be built from source, on a Raspberry Pi, at every bump.
FROM node:22-bookworm-slim

ARG BUILD_VERSION=dev
LABEL org.opencontainers.image.title="paia" \
      org.opencontainers.image.description="Personal assistant on Telegram, with memory" \
      org.opencontainers.image.source="https://github.com/sebbalex/paia" \
      org.opencontainers.image.version="${BUILD_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ASSISTANT_SRC=/opt/paia-src \
    ASSISTANT_ROOT=/data/paia \
    HOME=/data/home \
    PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip git \
      python3 python3-venv libpython3.11 \
      passwd util-linux \
 && rm -rf /var/lib/apt/lists/*

# The account everything runs as. Home is /data/home, created at start on the
# persistent volume, so `claude` and claude-mem keep their state across
# restarts and upgrades.
RUN useradd --system --no-create-home --home-dir /data/home \
            --shell /usr/sbin/nologin assistant

# Bun, for claude-mem. The installer defaults to $HOME, which here points at a
# volume that does not exist yet at build time -- hence the explicit HOME.
RUN HOME=/root bash -c 'curl -fsSL https://bun.sh/install | bash' \
 && mv /root/.bun/bin/bun /usr/local/bin/bun \
 && rm -rf /root/.bun \
 && bun --version

# The CLIs the assistant reaches through: `claude` for the SDK, `gws` for
# Google Workspace, `claude-mem` for semantic recall.
RUN npm install -g --omit=dev \
      @anthropic-ai/claude-code \
      @googleworkspace/cli \
      claude-mem \
 && npm cache clean --force

# Python dependencies in their own venv: Debian's interpreter is
# externally-managed and pip refuses to touch it.
RUN python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

# The source stays here, read-only. entrypoint.py copies it onto /data at
# every start; see the comment there for why it cannot be a symlink.
COPY . /opt/paia-src
COPY docker/entrypoint.py /usr/local/bin/entrypoint.py
RUN chmod 0755 /usr/local/bin/entrypoint.py

WORKDIR /data
ENTRYPOINT ["/usr/local/bin/entrypoint.py"]

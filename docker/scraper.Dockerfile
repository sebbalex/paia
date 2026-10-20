# The scraper worker as its own container, beside the assistant.
#
# Separate from the assistant's image on purpose. Chromium, Xvfb and ydotool
# are around 500 MB and none of them belong in a process whose job is answering
# Telegram; keeping them apart also means a browser that wedges takes the
# scraper down and not the assistant.
#
# Debian bookworm and not Alpine, and python:3.11 rather than a Playwright
# image, for one reason that catches everyone:
#
#   PLAYWRIGHT SHIPS NO CHROMIUM BUILD FOR linux/arm64.
#
# `playwright install chromium` fails on a Raspberry Pi. The supported route is
# Debian's own chromium package, with executable_path pointed at it -- which is
# what CHROMIUM_PATH below does. On amd64 the same image works unchanged.
FROM python:3.11-slim-bookworm

ARG BUILD_VERSION=dev
LABEL org.opencontainers.image.title="paia-scraper" \
      org.opencontainers.image.description="Deterministic fetch ladder for price scouting" \
      org.opencontainers.image.source="https://github.com/sebbalex/paia" \
      org.opencontainers.image.version="${BUILD_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SCRAPER_STATE=/data/scraper \
    SCRAPER_HOST=0.0.0.0 \
    SCRAPER_PORT=8099 \
    CHROMIUM_PATH=/usr/bin/chromium \
    DISPLAY=:99

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      # Tier 2-4. Debian's build, because Playwright has none for arm64.
      chromium \
      # Tier 3-4: a display for a headed browser. Xvfb is enough -- nothing
      # ever looks at it, the point is that Chromium believes it has one.
      xvfb \
      # Tier 4: kernel-level input through /dev/uinput. Without the device
      # mapped in (see addon/scraper/config.yaml) ydotoold starts and the tier
      # still reports itself unavailable, which is the intended failure.
      ydotool \
      # Chromium's own runtime deps that the slim image lacks.
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2 fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# Playwright is installed for its driver and its API, never for its browsers:
# PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD stops it fetching a Chromium that would not
# exist for this architecture anyway.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY scraper/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# The worker runs as a normal account. It needs no privilege of its own; the
# one capability it does need -- writing /dev/uinput for tier 4 -- comes from
# the device being mapped in and group-writable, not from running as root.
# The slim base has no udev, so no `input` group until we make one.
RUN groupadd --system --force input \
 && useradd --system --create-home --home-dir /home/scraper scraper \
 && usermod -aG input scraper

COPY scraper /opt/scraper/scraper
COPY docker/scraper-entrypoint.sh /usr/local/bin/scraper-entrypoint.sh
RUN chmod 0755 /usr/local/bin/scraper-entrypoint.sh

WORKDIR /opt/scraper
USER scraper

# No EXPOSE and no published port. The assistant reaches this over the add-on
# network by container name; nothing outside Docker has a route to it.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/health',timeout=3).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/scraper-entrypoint.sh"]

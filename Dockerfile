FROM python:3.13-slim

ARG NODE_VERSION=22.23.2
ARG NODE_SHA256=d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307
ARG BGUTIL_VERSION=2.0.2

COPY --from=denoland/deno:bin-2.9.6@sha256:4cf0029b9aeeeed5efcbb71828737f0d7c8c8a20072df960e51a5679ef0d21ba /deno /usr/local/bin/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg git xz-utils \
    && curl -fsSLO --retry 3 --connect-timeout 15 "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && echo "${NODE_SHA256}  node-v${NODE_VERSION}-linux-x64.tar.xz" | sha256sum -c - \
    && tar -xJf "node-v${NODE_VERSION}-linux-x64.tar.xz" -C /usr/local --strip-components=1 \
    && rm "node-v${NODE_VERSION}-linux-x64.tar.xz" \
    && git clone --depth 1 --single-branch --branch "${BGUTIL_VERSION}" \
        https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server \
    && npm ci \
    && npx tsc \
    && rm -rf /opt/bgutil/.git \
    && apt-get purge -y --auto-remove curl git xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/playlist-player-downloads \
    && chown -R appuser:appuser /app /tmp/playlist-player-downloads

COPY --chown=appuser:appuser app.py .
USER appuser

ENV PORT=10000 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_TMP_DIR=/tmp/playlist-player-downloads \
    POT_PROVIDER_HOME=/opt/bgutil/server

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]

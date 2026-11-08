FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2-node@sha256:d7d34108b76e52c7d3b575fe9e797e4bf41c1b2a08f80c5292eff6f851ce8472 AS pot_provider
FROM python:3.13-slim

COPY --from=denoland/deno:bin-2.9.6@sha256:4cf0029b9aeeeed5efcbb71828737f0d7c8c8a20072df960e51a5679ef0d21ba /deno /usr/local/bin/deno
COPY --from=pot_provider /usr/local/bin/node /usr/local/bin/node
COPY --from=pot_provider /app /opt/bgutil/server

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && deno --version \
    && test "$(node /opt/bgutil/server/build/generate_once.js --version)" = "2.0.2"

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

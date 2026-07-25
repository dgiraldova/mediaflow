# Media worker image: Python 3.12 + FFmpeg/FFprobe.
#
# FFmpeg must be present in the image (spec MVP1-003 acceptance criteria).
# Debian slim's ffmpeg package ships both ffmpeg and ffprobe.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY worker ./worker

RUN pip install --no-cache-dir .

# Temporary working directory for media processing; mounted as a volume in
# docker-compose so large intermediates do not grow the container layer.
ENV WORKER_TEMP_DIR=/var/tmp/mediaflow-worker
RUN mkdir -p ${WORKER_TEMP_DIR}

# Verifies both the Python entrypoint and the FFmpeg toolchain are usable.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ffprobe -version >/dev/null 2>&1 && python -c "import worker.main" || exit 1

CMD ["python", "-m", "worker.main"]

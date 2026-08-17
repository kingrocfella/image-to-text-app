FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt


FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app ./app ./app
# Each of these is a named-volume mount point in docker-compose.yml, and each is
# created here on purpose: Docker seeds a new named volume from the image path it
# covers, so a directory absent from the image yields a volume owned by root that
# the `app` user cannot then write to.
#
# /home/app/.paddlex is PaddleX's cache. It constructs a TempFileManager at
# *import* time, so with a read-only root and no writable path here the process
# dies before uvicorn even loads the app.
RUN mkdir -p /app/logs /app/shared_files /home/app/.cache /home/app/.paddlex \
    && chown -R app:app /app/logs /app/shared_files /home/app/.cache /home/app/.paddlex

ENV HOME=/home/app \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# BackPAC backend — FastAPI, LiveKit token minting, trip search.
FROM python:3.12-slim

# Python in a container: never write .pyc, never buffer stdout (or logs arrive
# in bursts after a crash instead of before it).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies in their own layer, so a code change doesn't reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. If the process is ever compromised, it should not own
# the filesystem it is standing on.
RUN useradd --create-home --uid 10001 backpac && chown -R backpac:backpac /app
USER backpac

EXPOSE 8000

# /healthz answers without touching the database or any upstream, so this
# reports "is the process alive", which is the only thing a restart can fix.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=2).status==200 else 1)"

# Migrations run on the way up — see entrypoint.sh. With more than one
# replica, run `alembic upgrade head` as a separate release step instead and
# drop this back to the uvicorn line: Alembic takes a lock, so concurrent
# replicas are safe but serialised, and one slow migration delays every boot.
CMD ["./entrypoint.sh"]

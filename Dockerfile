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
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

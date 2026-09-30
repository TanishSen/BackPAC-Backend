#!/bin/sh
# Migrate, then serve.
#
# The schema is Alembic's now, not `create_all`'s, so something has to run the
# migrations — and a container that starts serving against a database one
# revision behind will fail on whichever route touches the new column, which is
# a confusing way to find out. Failing here instead is loud and obvious.
#
# `set -e` matters: without it a failed migration would be a log line and the
# API would start anyway.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

# $PORT because Render, Railway, Fly and Cloud Run all assign one. The proxy
# flags make request.client the real caller rather than the load balancer —
# the per-IP rate limits depend on it. Only trust them behind a proxy you run.
echo "==> starting API on :${PORT:-8000}"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}" \
    --timeout-graceful-shutdown 20 \
    --no-server-header

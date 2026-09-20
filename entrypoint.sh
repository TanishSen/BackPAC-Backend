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

echo "==> starting API"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

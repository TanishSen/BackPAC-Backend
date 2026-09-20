"""Alembic's entry point — wired to this app's settings and models.

Two things make this file different from the one `alembic init` generates:

1. **The URL comes from `Settings`, not `alembic.ini`.** There is exactly one
   place the database lives (the `DATABASE_URL` environment variable), and a
   second copy in an ini file is a second thing to get wrong — the kind that
   migrates staging while you believe you are migrating production.

2. **We only manage our own tables.** This database is shared with Supabase,
   which owns `auth`, `storage`, `realtime` and friends. Autogenerate compares
   the models against the live database, so without `include_object` below it
   would notice all of Supabase's tables, decide they are not in our models,
   and cheerfully write a migration that drops the auth system.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.base import Base

# Importing the models is what puts them on Base.metadata; without this,
# autogenerate would think the schema should be empty.
from app.db import models_registry  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# NOT via config.set_main_option: alembic.ini is a configparser file, and
# configparser reads "%" as the start of an interpolation. A database password
# containing one — which Supabase generates freely — makes it throw on a URL it
# never needed to parse in the first place. The URL goes straight to the engine.
DATABASE_URL = get_settings().database_url


def include_object(object, name, type_, reflected, compare_to):
    """Keep autogenerate's hands off everything that is not ours.

    `reflected` means "found in the database"; a reflected table that we did
    not declare belongs to Supabase, so we ignore it rather than propose
    dropping it. Anything in a non-public schema is likewise not ours.
    """
    if type_ == "table":
        if object.schema not in (None, "public"):
            return False
        if reflected and name not in target_metadata.tables:
            return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it — `alembic upgrade head --sql`.

    Worth knowing about: it is how you hand a DBA the exact statements, or
    review them before they touch production data.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        # Without this, changing a column type is silently ignored by
        # autogenerate and you find out in production.
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        DATABASE_URL,
        poolclass=pool.NullPool,
        # Supabase's pooler does not support the prepared statements asyncpg
        # caches by default. Without this, migrations fail partway through with
        # a DuplicatePreparedStatementError that reads like a database fault.
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Shared Alembic env.py runtime for every ACP microservice.

Each service was carrying a near-identical 50–60 line env.py — `audit`,
`autonomy`, `api`, `flight_recorder`, `identity`, `identity_graph`,
`learning`, `registry`, `usage`. The only per-service parameters are:

  - ``version_table``  — distinct alembic version table per service so the
                        same Postgres schema can host multiple alembic
                        chains without collision
  - ``owned_tables``   — set of table (and optionally enum type) names that
                        this service "owns" and is allowed to manage
  - ``match_types``    — alembic include_object type filter; most services
                        want ``{"table", "type"}`` so custom Postgres
                        enums are included, a few want ``{"table"}`` only

Usage from a service env.py::

    from sdk.common.alembic_env import run
    from services.audit.models import (  # noqa: F401 -- side-effect import
        AuditLog, AuditNote, PendingUsageEvent, TransparencyRoot,
    )

    run(
        version_table="alembic_version_audit",
        owned_tables={"audit_logs", "audit_notes",
                      "pending_usage_events", "transparency_roots"},
    )

That's it. Importing the models is intentionally left to the caller so
autogenerate sees the right metadata; we never want this helper to import
service modules itself (it would create a fan-out of side-effect imports).
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from sdk.common.config import settings
from sdk.common.db import Base


def _purge_appledouble(script_location: str | None) -> None:
    # macOS tar (without COPYFILE_DISABLE=1) emits AppleDouble files prefixed
    # with `._` alongside every real file. Alembic walks the versions dir and
    # tries to load every `*.py` it finds; AppleDouble files have a binary
    # header with NUL bytes and crash with "source code string cannot contain
    # null bytes", taking the whole service down on boot.
    if not script_location:
        return
    versions = Path(script_location) / "versions"
    if not versions.is_dir():
        return
    for p in versions.iterdir():
        if p.name.startswith("._") or p.name == ".DS_Store":
            try:
                p.unlink()
            except OSError:
                pass


def run(
    *,
    version_table: str,
    owned_tables: Iterable[str],
    match_types: Iterable[str] = ("table", "type"),
) -> None:
    """Wire up + run the standard ACP service alembic env.

    This function is intended to be called at module top level inside a
    service's ``alembic/env.py``. It performs the side-effecting alembic
    bootstrap (offline vs. online detection, configuration, migration
    execution) identical to what the per-service env.py files used to do
    inline.
    """
    target_metadata   = Base.metadata
    owned             = frozenset(owned_tables)
    types_to_match    = frozenset(match_types)

    def include_object(obj, name, type_, reflected, compare_to):  # noqa: ARG001
        if type_ in types_to_match:
            return name in owned or name == version_table
        return True

    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    _purge_appledouble(config.get_main_option("script_location"))

    def run_migrations_offline() -> None:
        context.configure(
            url=settings.DATABASE_URL,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            version_table=version_table,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    def do_run_migrations(connection: Connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async_migrations() -> None:
        configuration = config.get_section(config.config_ini_section, {})
        configuration["sqlalchemy.url"] = settings.DATABASE_URL
        connectable = async_engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
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

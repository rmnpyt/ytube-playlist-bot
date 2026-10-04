"""Alembic environment.

Alembic connects synchronously even for an async application, so the URL is converted
here rather than making every migration aware of async SQLAlchemy. The models are
imported so ``--autogenerate`` can see them.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bot.services.storage.orm import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_PLACEHOLDER = "driver://user:pass@localhost/dbname"


def database_url() -> str:
    """The URL to migrate, from the environment or from the ini file.

    The bot passes it in programmatically. The command line reads DATABASE_URL so that
    ``alembic upgrade head`` works without editing a committed file.
    """
    configured = config.get_main_option("sqlalchemy.url", "")
    if configured and configured != _PLACEHOLDER:
        return configured
    from_environment = os.environ.get("DATABASE_URL")
    if not from_environment:
        raise RuntimeError(
            "Set DATABASE_URL, or call bot.services.storage.migrations.apply_migrations "
            "instead of using the Alembic command line."
        )
    return from_environment


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot alter a column in place; batch mode copies the table.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

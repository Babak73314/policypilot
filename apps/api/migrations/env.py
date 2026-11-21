"""Alembic environment.

Safety guard (ADR-0004, docs/deployment/aws.md): migrations refuse to run unless the connected
database is the expected one, so a misconfigured URL can never run DDL against another project's
database on a shared cluster. Migrations never create extensions; they only check for them.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from policypilot.config import get_settings
from policypilot.db.models import SCHEMA, Base

config = context.config
target_metadata = Base.metadata


class WrongDatabaseError(RuntimeError):
    pass


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        current = connection.execute(text("SELECT current_database()")).scalar_one()
        if current != settings.expected_database:
            raise WrongDatabaseError(
                f"refusing to migrate database {current!r}; expected "
                f"{settings.expected_database!r} (POLICYPILOT_EXPECTED_DB)"
            )
        has_vector = connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
        if not has_vector:
            raise RuntimeError(
                "the 'vector' extension is missing; it is created by the database bootstrap "
                "(docker/postgres/init.sh locally), never by migrations"
            )
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

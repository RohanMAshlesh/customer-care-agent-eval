"""Alembic environment. The database URL comes from CCEVALS_DATABASE_URL."""

from alembic import context

from ccevals import models  # noqa: F401  (register tables)
from ccevals.config import get_settings
from ccevals.db import Base, init_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = init_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

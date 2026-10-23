import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from chillwatch.db import Base

cfg = context.config
if os.environ.get("CHILLWATCH_DB_URL"):
    cfg.set_main_option("sqlalchemy.url", os.environ["CHILLWATCH_DB_URL"])
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=cfg.get_main_option("sqlalchemy.url"), target_metadata=target_metadata,
                      literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(cfg.get_section(cfg.config_ini_section, {}), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

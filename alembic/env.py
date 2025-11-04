from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import yaml
from deepinsight.databases.models import Base
# Models are imported via deepinsight.databases.models.__init__ to populate metadata
from sqlalchemy import create_engine

# Load DATABASE_URL from config.yaml and set into Alembic config
with open("config.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
if (cfg.get("database") or {}).get("url"):
    config.set_main_option("sqlalchemy.url", (cfg.get("database") or {}).get("url"))

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    is_sqlite = url.startswith("sqlite") if url else False
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    url = config.get_main_option("sqlalchemy.url")
    is_sqlite = url.startswith("sqlite") if url else False
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

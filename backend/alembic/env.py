from alembic import context
from app.config import get_settings
from app.db import build_engine
from app.domain.tables import metadata

if context.is_offline_mode():
    context.configure(
        url=get_settings().database_url.get_secret_value(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()

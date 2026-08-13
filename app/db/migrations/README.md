# Database migrations

`20260729_0001` is the internal Alembic baseline used after a fresh bootstrap.
It is not an upgrade path for an existing database.

`scripts/deploy_database.py` creates a new world from an empty schema, stamps
that baseline, upgrades to the single current Alembic head, and runs the
current seeds. `scripts/migrate_db.py` rejects unmarked pre-existing schemas.
To replace a world, use `scripts/reset_fresh_world.py` with its explicit
schema confirmation flags.

# backend/alembic/versions/

This directory is the home of the Alembic revisions that keep the TechHub Delivery database schema in sync with the Flask backend. Every file here is a numbered revision (0001 → 0014 plus the named `add_auth_tables`) that captures a concrete DDL change, so this folder is effectively the schema changelog that `alembic upgrade head` walks through whenever the deployment script or a developer refreshes the database.

## Responsibility

`backend/alembic/versions/` is responsible for capturing the incremental history of production schema changes—initial table creation, operational indexes, normalization sweeps, authentication tables, and the newer print-job queue. It owns the migration scripts that the backend ships with (`alembic.ini` points `script_location` back here) and therefore defines what columns, constraints, and indexes exist when the Flask API starts.

## Design Patterns

- Each revision is generated (or hand-edited) via `backend/alembic/script.py.mako`, which standardizes the import statements, type hints, and `upgrade`/`downgrade` stubs so every file references `alembic.op` and `sqlalchemy as sa` with the same structure.
- Revisions follow a predictable naming/numerical cadence (`0001`, `0002`, …, `0014`, plus `add_auth_tables`), allowing `alembic` to discover dependencies linearly. Later revisions borrow phrases like `phase1`/`phase2` or `operational_index_tuning` to hint at their theme, which helps developers match code to domain concerns without reading every line.
- Dialect-aware guards (e.g., `0009_phase1_indexes_fk_constraints` checking `bind.dialect.name` before creating computed columns or using `batch_alter_table`) and defensive defaults (see `0008_add_checkout_type_to_vehicle_checkouts`, which backfills before dropping server defaults) keep the migrations compatible with both MySQL and SQLite test runs.
- We favor expressive indexes and FK constraints, so most revisions either `op.create_index`/`op.create_foreign_key` or add tables with carefully chosen `server_default` values; additional tuning scripts like `0011_...` and `0012_...` focus entirely on composite indexes for delivery run, audit, and checkout throughput.

## Flow

`backend/alembic/env.py` drives the migration flow: it injects the parent directory onto `sys.path`, imports `app.database.Base` and the key models (`Order`, `AuditLog`, `TeamsNotification`, etc.), then configures Alembic with `settings.database_url` so `alembic` points at the same MySQL instance the Flask app uses. `context.configure` can operate in offline mode (rendering SQL to stdout) or online mode (connecting via `engine_from_config`/`pool.NullPool` and running within a transaction), but in both cases it passes `target_metadata = Base.metadata` so autogenerate would stay in sync with the SQLAlchemy models if needed.

The revision scripts themselves are executed sequentially as part of `context.run_migrations()`. Each `upgrade` call builds tables, columns, indexes, and constraints; the corresponding `downgrade` reverses them. Obvious guardrails—such as `op.execute` for SQL that Alembic can’t express (e.g., backfilling `checkout_type`)—keep schema state stable even when the backend code is refactored.

## Integration

- `backend/alembic.ini` points Alembic back to this directory and to the same virtualenv path that the backend’s deployment tooling uses, so `scripts/deploy.sh` can simply run `alembic upgrade head` from `backend/` (it looks for the binary in `venv/bin/alembic` or falls back to `python3 -m alembic`).
- The migration flow imports `app.models` and `app.database`, which means that new tables are immediately reflected in the SQLAlchemy `Base` metadata consumed by the API. That tight coupling keeps the schema referenced in migrations consistent with the ORM layer.
- The Flask `app.main` entry point, CI scripts, and PythonAnywhere deploy process rely on this folder indirectly: any schema change needs a revision here before the backend or scheduler can start, and the deployment script (see `scripts/deploy.sh`) reruns `alembic upgrade head` before touching the WSGI file to ensure migrations have applied.

## Migration Themes
- **Delivery evidence**: `0023_add_delivery_evidence` adds the nullable `orders.delivery_evidence` JSON column after `0022_compat_bundle_ready`, preserving existing completed orders without a backfill. Photo content lives in SharePoint.

- **Normalization + Domain Modeling**: Beyond the initial `0001_mysql_initial` bootstrap, revisions `0002_add_order_details_fields`, `0003_add_remainder_fields`, and `0010_phase2_normalization` gradually normalize orders by adding PDF metadata, parent/remainder tracking, and a dedicated `order_status_history` table with FK links to `users`. Vehicle checkout tracking (`0006`-`0008`) also becomes richer with user identity fields and `checkout_type`, keeping telemetry and audit trails connected to authenticated actors.
- **Indexing & Operational Tuning**: `0005_archive_system_audit_and_session_indexes` seeds archive tables and cursor-friendly indexes, while `0009_phase1_indexes_fk_constraints` introduces the bulk of runtime indexes (orders, delivery runs, notifications, webhooks, sessions) plus case-insensitive inflow order lookups. Later tuning migrations (`0011_operational_index_tuning`, `0012_additional_operational_index_tuning`, `0013_add_delivery_sequence_to_orders`) keep delivery run queries fast (status+created composite indexes, `delivery_sequence` order numbering) and add targeted indexes for audit logs and vehicle checkout filters.
- **Authentication & Configuration**: `add_auth_tables` adds `users` and `sessions` with cascade-safe FKs and indexes to support SAML sessions, and `0004_add_system_settings` introduces a `system_settings` table for dynamic configuration. `0005` complements that with session purge indexes and the audit archive that the maintenance service uses.
- **Print Job Workflow**: `0014_add_print_jobs` stands alone as the migration that adds the `print_jobs` queue, upload paths, claim tracking, status fields, and indexes tuned for order/document lookups and expiry-based claims.

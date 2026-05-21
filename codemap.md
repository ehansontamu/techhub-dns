# Repository Atlas: techhub-dns

## Project Responsibility
Implements the TechHub delivery workflow platform: a React operations SPA backed by a Flask API that ingests Inflow orders, coordinates QA and delivery/shipping workflows, generates and prints picklists/order documents, and integrates with Microsoft services for identity, storage, email, and notifications.

## System Entry Points
- `README.md`: Product overview, architecture diagram, stack, and local/deployment setup.
- `frontend/`: Vite + React + TypeScript SPA served in production from `frontend/dist`.
- `backend/app/main.py`: Flask application factory/entrypoint, Socket.IO wiring, blueprint registration, SPA serving, and `/health`.
- `backend/run_scheduler.py`: Standalone scheduler runner for Inflow sync, webhook health, and maintenance jobs.
- `backend/wsgi.py`: PythonAnywhere WSGI entrypoint.
- `scripts/deploy.sh`: PythonAnywhere deploy orchestration for migrations, frontend build, and webapp reload.
- `.github/workflows/deploy-pythonanywhere.yml`: CI/CD handoff into the remote deploy script.
- `ops/print_agent/agent.py`: Standalone workstation print agent for picklist jobs.

## Architecture Shape
- `frontend/` owns the operator-facing UI, auth bootstrap, routing, and real-time UX.
- `backend/app/` owns HTTP/WebSocket APIs, business logic, SQLAlchemy models, and shared helpers.
- `backend/alembic/` owns schema migration bootstrap and history.
- `backend/scripts/` and `scripts/` provide operational and deployment automation.
- `ops/print_agent/` is a decoupled edge worker for physical printer execution.

## Repository Directory Map
| Directory | Responsibility Summary | Detailed Map |
|-----------|------------------------|--------------|
| `frontend/` | React SPA for dashboards, orders, delivery runs, QA, signing, admin tooling, and auth-aware navigation. | [View Map](frontend/codemap.md) |
| `backend/` | Flask runtime root with app package, Alembic migrations, scheduler entrypoints, and backend operational tooling. | [View Map](backend/codemap.md) |
| `backend/app/` | Core application package composing config, DB, APIs, models, schemas, services, utils, templates, scheduler, and Socket.IO. | [View Map](backend/app/codemap.md) |
| `backend/app/api/` | API surface layer handling blueprints, auth/authorization, error middleware, DB dependencies, and socket event registration. | [View Map](backend/app/api/codemap.md) |
| `backend/app/api/routes/` | Domain route modules for orders, delivery runs, inflow, auth, observability, system settings, SharePoint, analytics, audit, and vehicle checkouts. | [View Map](backend/app/api/routes/codemap.md) |
| `backend/app/models/` | SQLAlchemy entity layer for orders, delivery runs, print jobs, auth sessions, system settings, audits, and notifications. | [View Map](backend/app/models/codemap.md) |
| `backend/app/services/` | Service layer encapsulating business rules, external integrations, print/email workflows, background tasks, and maintenance logic. | [View Map](backend/app/services/codemap.md) |
| `backend/app/schemas/` | Pydantic API contract layer for validation, serialization, enums, and standardized error payloads. | [View Map](backend/app/schemas/codemap.md) |
| `backend/app/utils/` | Cross-cutting helpers for exceptions, idempotency, webhook validation, PDF layout, building resolution, and timezone handling. | [View Map](backend/app/utils/codemap.md) |
| `backend/app/templates/` | Document template assets used by order detail PDF/email generation flows. | [View Map](backend/app/templates/codemap.md) |
| `backend/alembic/` | Alembic environment bootstrap that connects ORM metadata, config, and migration execution modes. | [View Map](backend/alembic/codemap.md) |
| `backend/alembic/versions/` | Ordered schema evolution history covering normalization, indexes, auth/session support, vehicle workflows, and print jobs. | [View Map](backend/alembic/versions/codemap.md) |
| `backend/scripts/` | Maintenance and diagnostics scripts for DB operations, Inflow webhooks, SharePoint checks, retention, and notifications. | [View Map](backend/scripts/codemap.md) |
| `scripts/` | Deployment automation for PythonAnywhere, including locking, migrations, frontend build, reload, and logging. | [View Map](scripts/codemap.md) |
| `ops/` | Operational namespace for standalone infrastructure that sits outside the main web app runtime. | [View Map](ops/codemap.md) |
| `ops/print_agent/` | Printer-side worker that claims backend print jobs, downloads PDFs, prints through SumatraPDF, and reports status. | [View Map](ops/print_agent/codemap.md) |

## Cross-Repository Flow
1. Inflow data enters the backend through scheduled syncs or signed webhooks.
2. Service-layer logic persists and transitions orders, producing audit/status history and delivery/shipping side effects.
3. The frontend consumes REST and Socket.IO updates for operator workflows.
4. Document and notification flows branch into PDF generation, Graph email/Teams/SharePoint operations, and optional print-job queueing.
5. The standalone print agent claims queued jobs from backend APIs and executes physical printing on workstation hardware.

## Operational Integration Points
- Database state is shared between Flask runtime, scheduler jobs, and operational scripts through `backend/app/database.py` and SQLAlchemy models.
- Microsoft integrations are centralized in backend services and surfaced through orders, auth, notification, SharePoint, and observability routes.
- PythonAnywhere deployment flows from GitHub Actions to `scripts/deploy.sh`, which migrates the DB, builds the SPA, and reloads the site.


## Partial-Order Workflow

- **OrderSplittingService** (`backend/app/services/order_splitting.py`)
  - `create_partial_picklist_leg()` - Creates child `-P` order
  - `create_remainder_order()` - Creates remainder `-R` order
  - `process_partial_fulfillments()` - Handles bulk remainder creation
  - `get_remainder_items()` - Calculates remaining items

- **OrderService** (`backend/app/services/order_service.py`)
  - `generate_picklist()` - Handles partial split logic
  - `submit_qa()` - Blocks QA on parent remainder legs
  - Order details generation uses remainder-only view for parent legs

- **Frontend**:
  - `OrderDetail.tsx` - Leg-aware product tables
  - `OrderTable.tsx` - Partial leg labeling
  - `OrderQAPage.tsx` - QA blocking for parent legs

- **Tests**:
  - `backend/tests/test_partial_order_workflow.py`
  - `backend/tests/test_partial_order_smoke.py`

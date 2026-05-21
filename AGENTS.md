# AGENTS.md - techhub-dns-dev

Repository instructions for coding agents working in this checkout.

## Instruction Precedence

1. Active system, developer, and user instructions.
2. This `AGENTS.md`.
3. Tooling configuration in the repo, including ESLint, TypeScript, pytest, Alembic, and package scripts.

No additional Cursor or Copilot instruction files are present in the repo root:

- `.cursor/rules/**` is not present.
- `.cursorrules` is not present.
- `.github/copilot-instructions.md` is not present.

## Project Shape

This repo implements the TechHub delivery workflow platform: a React operations SPA backed by a Flask API. It manages Inflow order sync, QA, delivery and shipping workflows, PDF/signature handling, print jobs, Microsoft integrations, audit trails, and admin tooling.

Primary areas:

- `frontend/` - React 18, TypeScript, Vite, Tailwind SPA.
- `backend/` - Flask, SQLAlchemy, Socket.IO, APScheduler, Alembic.
- `backend/app/main.py` - backend runtime entrypoint with API, Socket.IO, SPA serving, and `/health`.
- `backend/run_scheduler.py` - standalone scheduler process.
- `backend/wsgi.py` - PythonAnywhere WSGI entrypoint.
- `ops/print_agent/` - standalone workstation print agent.
- `scripts/deploy.sh` - deployment automation; do not run unless explicitly requested.
- `codemap.md` and nested `codemap.md` files - repository maps. Check these before broad refactors.

High-level runtime flow:

1. Inflow orders enter through scheduled polling or signed webhooks.
2. Backend services normalize orders, apply workflow transitions, persist audit/status history, and emit Socket.IO updates.
3. The frontend consumes REST and Socket.IO state to drive operator screens for QA, delivery dispatch, shipping, signing, admin, and observability.
4. PDF/email/SharePoint/Teams/print flows branch from backend services and operational workers.

Useful maps before broad work:

- `codemap.md` - repo-wide atlas.
- `frontend/codemap.md` - SPA architecture, routing, auth, API hooks, UI surfaces.
- `backend/codemap.md` - backend runtime root.
- `backend/app/codemap.md` - Flask app package, middleware, services, schemas, Socket.IO.
- `ops/print_agent/codemap.md` - workstation print worker.

## Working Directory Rules

- Run frontend commands from `frontend/`.
- Run backend commands from `backend/`.
- Many backend tests assume the current directory is `backend/` because they use `sys.path.append('.')`.
- Keep changes scoped to the task. Do not reformat unrelated files.
- Do not edit generated build output such as `frontend/dist/` unless the task explicitly requires it.
- Prefer reading the nearest `codemap.md` before changing an unfamiliar subsystem.
- Prefer `rg` and `rg --files` for search.
- Preserve user changes already present in the worktree. If a file has unrelated edits, avoid overwriting them.

## Testing

### Frontend
```bash
cd frontend
npm test          # Run tests (vitest + jsdom + @testing-library/react)
```
- Test files: `*.test.ts` / `*.test.tsx` co-located with source
- Setup: `src/test/setup.ts` imports `@testing-library/jest-dom` matchers
- Config: `vitest.config.ts` (jsdom environment, globals enabled)

### Backend
```bash
cd backend
python -m pytest tests/ -q
```

## Setup

Frontend:

```bash
cd frontend
npm ci
```

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
```

On Windows, activate the virtualenv with `.venv\Scripts\activate`.

Local environment files are expected under `backend/.env`; use `backend/.env.example` as the reference.

Prerequisites from the README:

- Python 3.12+.
- Node.js 18+.
- MySQL 8.0+ for full backend/database flows.

## Frontend Commands

Run from `frontend/`.

```bash
npm run dev
npm run lint
npx tsc --noEmit
npm run build
npm run preview
npm run test
```

Notes:

- `npm run test` runs `vitest run`.
- `npm run build` runs `tsc && vite build`.
- For single test files, use `npm run test -- path/to/file.test.ts`.
- For named tests, use `npm run test -- path/to/file.test.ts -t "test name"`.
- Dev server proxying is configured in `frontend/vite.config.ts`; API and Socket.IO traffic normally targets the Flask backend on port `8000`.

## Backend Commands

Run from `backend/`.

```bash
python -m app.main
alembic upgrade head
pytest -q
python tests/test_error_handling.py
python tests/test_picklist_service.py
python tests/test_db.py
```

Single-test patterns:

```bash
pytest -q tests/test_error_handling.py
pytest -q tests/test_error_handling.py::test_validation_error
pytest -q tests/test_error_handling.py -k "validation"
python tests/test_picklist_service.py
```

Runtime health check after starting the backend:

```bash
curl http://localhost:8000/health
```

Scheduler commands:

```bash
python run_scheduler.py
python run_sync_once.py
```

Use scheduler commands only when the task concerns sync, webhook health, maintenance jobs, or operational behavior.

## Verification Expectations

- Frontend-only changes: run `npm run lint`, `npx tsc --noEmit`, and targeted Vitest coverage when relevant.
- Backend-only changes: run impacted backend tests from `backend/`.
- API contract or shared workflow changes: run both backend tests and frontend type/lint checks.
- Error handling changes: always run `python tests/test_error_handling.py`.
- Alembic/model changes: run the relevant migration command or explain why it was not run.
- Scheduler/sync changes: run the most relevant backend service tests and, when safe, a dry targeted script or unit test rather than a live external sync.
- Print-agent changes: inspect `ops/print_agent/requirements.txt` and run focused checks from `ops/print_agent/` when possible.
- If verification cannot be run because dependencies, services, or credentials are missing, state that clearly in the final response.

Do not invent broad test commands. Use the scripts and direct commands available in this repo.

## Code Style

TypeScript and React:

- Keep imports ordered as external packages, internal modules, then relative modules.
- Use `import type` for type-only imports.
- Respect strict TypeScript settings: `strict`, `noUnusedLocals`, and `noUnusedParameters` are enabled.
- Prefix intentionally unused variables or parameters with `_`.
- Avoid introducing `any`; if unavoidable, keep the scope narrow and justify it inline.
- Follow existing component and styling patterns unless the task is explicitly a redesign.
- Keep route-level screens in `src/pages` and reusable UI/feature pieces in `src/components`.
- Prefer existing hooks in `src/hooks` and API modules in `src/api` over creating ad hoc `fetch` calls in components.
- Keep shared formatting and domain helpers in `src/lib` or `src/utils`.
- Preserve auth/session behavior in `src/contexts/AuthContext.tsx` and protected route handling when modifying routing.
- Treat Socket.IO as an enhancement with HTTP fallback behavior; do not make core screens depend only on live sockets.
- When response shapes change, update frontend types in `src/types` and any API helpers or query hooks that consume them.

Python:

- Keep imports ordered as standard library, third-party, then local `app.*` imports.
- Avoid wildcard imports and unnecessary side-effect imports.
- Parse external/request data at API boundaries.
- Keep service-layer logic on normalized internal data.
- Preserve exception contracts used by centralized API error handling.
- Keep Flask routes thin. Validation, orchestration, and persistence rules should live in schemas/services/models as appropriate.
- Prefer dependency/session helpers already in `app.database` and `app.api.dependencies`.
- Use Pydantic schemas for request/response contracts where the surrounding route family does.
- Keep external API clients and Microsoft/Inflow/ArcGIS behaviors isolated behind service modules.
- Do not commit live credentials, tenant IDs, tokens, cookies, or local `.env` values.

General:

- Prefer guard clauses over deep nesting.
- Keep functions deterministic where practical.
- Use intent-revealing names, especially for workflow/status logic.
- Do not swallow errors with empty `except` or `catch` blocks.
- Use typed domain/API exceptions for expected failures.
- Preserve centralized logging/error-handling patterns instead of adding one-off print/debug output.
- Remove temporary diagnostics before finishing unless the user explicitly asks to keep them.

## Frontend Architecture Notes

- `src/main.tsx` boots React and renders `App`.
- `src/App.tsx` owns routing, lazy page loading, layout wiring, error boundaries, auth provider placement, and app shell composition.
- `src/api` centralizes axios clients and resource modules. Use these modules from hooks/components.
- `src/hooks` contains data and Socket.IO hooks such as order and delivery-run state.
- `src/components/ui` contains shared primitives. Prefer extending existing primitives over adding one-off styling.
- `src/components/error-boundaries` provides app/route failure handling. Use existing boundary patterns for risky lazy-loaded surfaces.
- `src/pages` contains route-level screens for dashboard, orders, QA, shipping, delivery, admin, and related workflows.
- `src/index.css`, `tailwind.config.js`, and component primitives define theme conventions. Avoid broad visual churn unless requested.

Authentication and routing:

- `/login` is unprotected; internal app pages should remain behind `ProtectedRoute`.
- Auth state comes from `/auth/me` and SAML redirects. Be careful when changing base URLs, credential behavior, or 401 interceptors.
- The frontend commonly talks to `/api/*`, while auth endpoints may be used without the `/api` prefix where existing code does so. Follow the local pattern in the file being changed.

## Backend Architecture Notes

- `app/main.py` composes the Flask app, CORS, ProxyFix, Socket.IO, error handlers, auth middleware, blueprints, SPA serving, `/api`, and `/health`.
- `app/config.py` owns environment-driven settings through `pydantic-settings`.
- `app/database.py` owns SQLAlchemy engine/session setup and runtime pool introspection.
- `app/api/middleware.py` serializes `DNSApiError` and related API errors.
- `app/api/auth_middleware.py` owns authentication and permission checks.
- `app/api/socket_events.py` owns Socket.IO connect, disconnect, join, and leave behavior.
- `app/api/routes/*` are the HTTP API surface. Keep them focused on request parsing and service calls.
- `app/models/*` are SQLAlchemy persistence entities.
- `app/schemas/*` are API/domain contracts and serialization shapes.
- `app/services/*` are business logic and integration boundaries.
- `app/utils/*` contains cross-cutting helpers such as exceptions, timezone normalization, idempotency, PDF helpers, building mapping, and webhook security.
- `app/templates/*` contains HTML/image assets for PDF and email generation.

Backend request pattern:

1. Route validates input/query data.
2. Route obtains a DB session through the existing dependency/session pattern.
3. Service applies domain logic and external integration calls.
4. Model changes are committed intentionally.
5. Response is serialized through schema or existing route convention.
6. Expected failures use typed API/domain exceptions so middleware can serialize them.

## Database And Migrations

- Alembic config lives at `backend/alembic.ini`.
- Migration environment lives under `backend/alembic/`.
- Versioned migrations live under `backend/alembic/versions/`.
- SQLAlchemy models live under `backend/app/models/`.
- When changing persistent models, check whether a migration is required. If no migration is needed, mention why.
- Prefer additive/backward-compatible migrations when possible because production data matters.
- Do not run destructive migrations or data cleanup scripts unless the user explicitly requests it.
- Do not point tests or scripts at production databases.

## Domain Boundaries

- Order workflow rules belong in backend services and schemas, not duplicated ad hoc in routes.
- API routes should stay thin: parse input, call services, return serialized responses.
- Frontend API contracts should match backend schemas; update both sides together when changing response shapes.
- Audit/status history matters. Preserve user attribution, timestamps, and status transition semantics.
- Real-time behavior uses Socket.IO with polling fallbacks; do not break REST behavior when changing sockets.
- Backend serves `frontend/dist` in production when present; otherwise root returns JSON guidance.
- Delivery and shipping workflows are distinct. Do not collapse status transitions unless the domain change is explicit.
- QA gating is part of workflow correctness. Preserve blocking checks before delivery/shipping advancement.
- Vehicle checkout and delivery-run state are operationally sensitive. Preserve runner/vehicle accountability and auditability.
- PDF/signature flows must preserve document integrity and storage semantics.
- Print jobs may be consumed by a separate workstation worker. Keep backend queue contracts compatible with `ops/print_agent`.

## External Integrations

The app integrates with Inflow, Microsoft Graph, SharePoint, Teams/Power Automate, ArcGIS, MySQL, SAML SSO, and workstation printing. Keep these points isolated behind existing service/config layers. Do not require live external services in unit tests unless the existing test already does.

Integration guidance:

- Inflow sync/webhooks: preserve idempotency, signature/security checks, and fallback polling behavior.
- Microsoft Graph/email/SharePoint/Teams: keep network calls in services; do not leak credentials or raw tokens.
- ArcGIS/building resolution: keep parsing and mapping logic deterministic and testable.
- SAML/session handling: preserve cookie/session security and existing redirect semantics.
- Socket.IO: keep room names and event payload compatibility unless updating both frontend and backend consumers.
- Printing: maintain backend print-job API compatibility for the separate agent.

## Operational Scripts

- `scripts/deploy.sh` is deployment-only and may run migrations/build/reload steps. Do not run it without explicit user approval.
- `backend/scripts/` contains diagnostics and maintenance scripts. Read the script before running it; many expect real services or credentials.
- `backend/run_scheduler.py` starts recurring jobs. Do not leave long-running processes active after verification.
- `backend/run_sync_once.py` may contact external services depending on configuration.
- `ops/print_agent/agent.py` is a workstation process for physical printing; do not assume printers or SumatraPDF are available in the dev environment.

## Safety

- Do not run `scripts/deploy.sh` unless explicitly requested.
- Avoid destructive git operations unless explicitly requested.
- Never remove user changes to files unrelated to the task.
- Do not commit, amend, merge, or push unless explicitly requested.
- Treat credentials and `.env` contents as sensitive. Do not print secrets in logs or responses.
- Do not run live sync, webhook registration, notification-send, print, or deploy actions unless the task explicitly requires it and the environment is clearly safe.
- Avoid adding new dependencies unless necessary. If needed, update the relevant lock/config files and explain the reason.
- Keep generated artifacts, caches, and local virtualenv files out of commits unless the repo already tracks them.
- If you encounter unexpected worktree changes that conflict with the task, stop and ask before overwriting them.


## Partial-Order Workflow

The partial-order workflow handles splitting orders into picked and remainder legs.

### Key Components

- **Backend Services**:
  - `OrderSplittingService` in `backend/app/services/order_splitting.py`
  - Creates child `-P` orders for picked items
  - Creates remainder `-R` orders for unpicked items
  - Handles document generation for each leg separately

- **Frontend Components**:
  - `OrderDetail.tsx` - Shows leg-specific product tables
  - `OrderTable.tsx` - Distinguishes partial legs in the orders list
  - `OrderQAPage.tsx` - Blocks QA on parent remainder legs

- **Key Behaviors**:
  - Parent order shows remaining items
  - Child `-P` order shows picked items and owns generated documents
  - Remainder `-R` order is created after delivery if items remain unpicked
  - QA is only allowed on child legs, not parent remainder legs

### Testing

- Backend tests in `backend/tests/test_partial_order_workflow.py`
- Smoke test in `backend/tests/test_partial_order_smoke.py`
